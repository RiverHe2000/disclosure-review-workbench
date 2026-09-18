"""Durable local work queue, immutable extraction runs and append-only human reviews."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pdfplumber

METRIC_UNITS = {
    "cet1_ratio": "percent", "total_capital_ratio": "percent", "rwa": "AUD_million",
    "lcr": "percent", "nsfr": "percent",
}
MAX_PDF_BYTES = 50 * 1024 * 1024
BANKS = {"CBA", "Westpac", "ANZ", "NAB"}
RESTATEMENT_CONTEXT_FIELDS = (
    "candidate_id", "value", "unit", "period", "entity_scope", "basis", "observation", "restated",
)


class ValidationError(ValueError):
    pass


class Conflict(ValueError):
    pass


class NotFound(KeyError):
    pass


class LeaseLost(RuntimeError):
    pass


def now() -> str:
    return datetime.now(UTC).isoformat()


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def bind_restatement_confirmation(fact: dict, review: dict) -> dict:
    """A confirmation belongs to the reviewed evidence and semantics, not the fact ID."""
    changed = any(key in review and review[key] != fact.get(key) for key in RESTATEMENT_CONTEXT_FIELDS)
    if changed and review.get("restatement_resolved") is not True:
        return dict(review, restatement_resolved=False)
    return review


def model_path() -> Path:
    return Path(os.environ.get("DISCLOSURE_MODEL_PATH", "D:/models/Qwen3-4B-Instruct-2507"))


def pipeline_version(method: str) -> str:
    from disclosure.extraction import RULES_VERSION
    from disclosure.parser import PARSER_VERSION

    revision = f"{os.environ.get('DISCLOSURE_PIPELINE_VERSION', 'pipeline-v1')}:{PARSER_VERSION}:{RULES_VERSION}"
    if method == "rules":
        return revision
    from disclosure.model import PROMPT_VERSION

    path = model_path()
    config = path / "config.json"
    identity = config.read_bytes() if config.is_file() else b"missing"
    for weight in sorted([*path.glob("*.safetensors"), *path.glob("pytorch_model*.bin")]):
        stat = weight.stat()
        identity += f"{weight.name}:{stat.st_size}:{stat.st_mtime_ns}".encode()
    fingerprint = hashlib.sha256(identity).hexdigest()[:16]
    # Include the model files' identity in the cache key without rehashing multi-GB weights per request.
    return f"{revision}:{PROMPT_VERSION}:{path.resolve()}:{fingerprint}"


class Store:
    def __init__(self, data_dir: Path | str):
        self.data_dir = Path(data_dir).resolve()
        self.pdf_dir = self.data_dir / "documents"
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "workbench.sqlite3"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id),
                    method TEXT NOT NULL, pipeline_version TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    lease_token TEXT, lease_until REAL, worker_id TEXT,
                    UNIQUE(document_id, method, pipeline_version));
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
                    document_id TEXT NOT NULL REFERENCES documents(id), created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS candidates (
                    run_id TEXT NOT NULL REFERENCES runs(id), id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(run_id, id));
                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    document_id TEXT NOT NULL REFERENCES documents(id), payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS raw_extractions (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id), facts_payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, fact_id TEXT NOT NULL REFERENCES facts(id),
                    version INTEGER NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(fact_id, version));
                CREATE TABLE IF NOT EXISTS workers (id TEXT PRIMARY KEY, heartbeat REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, lease_until, created_at);
                CREATE INDEX IF NOT EXISTS idx_runs_document ON runs(document_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_reviews_fact ON reviews(fact_id, version);
                CREATE TRIGGER IF NOT EXISTS reviews_no_update BEFORE UPDATE ON reviews
                    BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS reviews_no_delete BEFORE DELETE ON reviews
                    BEGIN SELECT RAISE(ABORT, 'reviews are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS facts_no_update BEFORE UPDATE ON facts
                    BEGIN SELECT RAISE(ABORT, 'original facts are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS candidates_no_update BEFORE UPDATE ON candidates
                    BEGIN SELECT RAISE(ABORT, 'original candidates are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS raw_extractions_no_update BEFORE UPDATE ON raw_extractions
                    BEGIN SELECT RAISE(ABORT, 'original extractions are immutable'); END;
            """)

    @contextmanager
    def connection(self, write: bool = False):
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def register_document(self, path: Path, bank: str, year: int, source_url: str = "",
                          filename: str | None = None) -> dict:
        path = Path(path)
        if bank not in BANKS or isinstance(year, bool) or not 1990 <= year <= 2100:
            raise ValidationError("Choose a supported bank and a valid fiscal year.")
        if not path.is_file():
            raise ValidationError("PDF file does not exist.")
        if path.stat().st_size > MAX_PDF_BYTES:
            raise ValidationError("PDF exceeds the 50 MiB limit.")
        with path.open("rb") as stream:
            if not stream.read(8).startswith(b"%PDF-"):
                raise ValidationError("File is not a PDF.")
        try:
            with pdfplumber.open(path) as pdf:
                page_count = len(pdf.pages)
                if page_count < 1:
                    raise ValidationError("PDF has no pages.")
        except Exception as exc:
            raise ValidationError("PDF cannot be read or is encrypted.") from exc
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        safe_name = Path((filename or path.name).replace("\\", "/")).name
        if not safe_name.lower().endswith(".pdf"):
            safe_name += ".pdf"
        document = dict(id=uuid.uuid4().hex, bank=bank, year=year, filename=safe_name,
                        sha256=sha, source_url=source_url, created_at=now(), page_count=page_count)
        destination = self.pdf_dir / f"{sha}.pdf"
        with self.connection(write=True) as db:
            existing = db.execute("SELECT payload FROM documents WHERE sha256=?", (sha,)).fetchone()
            if existing:
                saved = json.loads(existing["payload"])
                if saved["bank"] != bank or saved["year"] != year:
                    raise Conflict("This PDF was already imported for a different bank or year.")
                return saved
            if path.resolve() != destination.resolve():
                temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp")
                try:
                    shutil.copyfile(path, temporary)
                    os.replace(temporary, destination)
                finally:
                    temporary.unlink(missing_ok=True)
            db.execute("INSERT INTO documents VALUES (?, ?, ?)", (document["id"], sha, encode(document)))
        return document

    def document(self, document_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT payload FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise NotFound("Document not found.")
        return json.loads(row["payload"])

    def document_path(self, document_id: str) -> Path:
        doc = self.document(document_id)
        path = (self.pdf_dir / f"{doc['sha256']}.pdf").resolve()
        if path.parent != self.pdf_dir or not path.is_file():
            raise NotFound("Original PDF is unavailable.")
        return path

    def documents(self) -> list[dict]:
        with self.connection() as db:
            return [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM documents ORDER BY rowid DESC")]

    @staticmethod
    def _job(row: sqlite3.Row) -> dict:
        return {key: row[key] for key in ("id", "document_id", "method", "pipeline_version", "status",
                                         "stage", "error", "attempts", "created_at", "updated_at")}

    def enqueue(self, document_id: str, method: str, retry: bool = False,
                pipeline_version: str | None = None) -> dict:
        self.document(document_id)
        if method not in {"rules", "qwen"}:
            raise ValidationError("Unsupported extraction method.")
        version = pipeline_version or globals()["pipeline_version"](method)
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE document_id=? AND method=? AND pipeline_version=?",
                             (document_id, method, version)).fetchone()
            if row:
                if retry and row["status"] == "failed":
                    db.execute("UPDATE jobs SET status='queued', stage='queued', error=NULL, updated_at=?, "
                               "lease_token=NULL, lease_until=NULL WHERE id=?", (now(), row["id"]))
                    row = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
                return self._job(row)
            job_id, stamp = uuid.uuid4().hex, now()
            db.execute("INSERT INTO jobs (id,document_id,method,pipeline_version,status,stage,created_at,updated_at) "
                       "VALUES (?,?,?,?, 'queued','queued',?,?)", (job_id, document_id, method, version, stamp, stamp))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def jobs(self) -> list[dict]:
        with self.connection() as db:
            return [self._job(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def job(self, job_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("Job not found.")
        return self._job(row)

    def touch_worker(self, worker_id: str = "worker") -> None:
        with self.connection(write=True) as db:
            db.execute("INSERT INTO workers VALUES (?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat",
                       (worker_id, time.time()))

    def worker_active(self, max_age: float = 60) -> bool:
        with self.connection() as db:
            return db.execute("SELECT 1 FROM workers WHERE heartbeat>? LIMIT 1", (time.time() - max_age,)).fetchone() is not None

    def claim(self, worker_id: str = "worker", lease_seconds: float = 120) -> dict | None:
        if lease_seconds <= 0:
            raise ValidationError("Lease duration must be positive.")
        clock = time.time()
        with self.connection(write=True) as db:
            db.execute("INSERT INTO workers VALUES (?,?) ON CONFLICT(id) DO UPDATE SET heartbeat=excluded.heartbeat",
                       (worker_id, clock))
            db.execute("UPDATE jobs SET status='queued', stage='lease expired; queued', lease_token=NULL, "
                       "lease_until=NULL, updated_at=? WHERE status='running' AND lease_until<=?", (now(), clock))
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if row is None:
                return None
            token = uuid.uuid4().hex
            db.execute("UPDATE jobs SET status='running', stage='parsing', attempts=attempts+1, lease_token=?, "
                       "lease_until=?, worker_id=?, updated_at=? WHERE id=?",
                       (token, clock + lease_seconds, worker_id, now(), row["id"]))
            result = self._job(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())
            document = json.loads(db.execute("SELECT payload FROM documents WHERE id=?", (row["document_id"],)).fetchone()[0])
        result.update(lease_token=token, document=document, path=str(self.pdf_dir / f"{document['sha256']}.pdf"))
        return result

    def heartbeat(self, job_id: str, lease_token: str, stage: str | None = None,
                  lease_seconds: float = 120) -> bool:
        if lease_seconds <= 0:
            raise ValidationError("Lease duration must be positive.")
        clock = time.time()
        with self.connection(write=True) as db:
            changed = db.execute("UPDATE jobs SET lease_until=?, updated_at=?, stage=COALESCE(?, stage) "
                                 "WHERE id=? AND lease_token=? AND status='running' AND lease_until>?",
                                 (clock + lease_seconds, now(), stage, job_id, lease_token, clock)).rowcount
            if changed:
                worker = db.execute("SELECT worker_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
                db.execute("UPDATE workers SET heartbeat=? WHERE id=?", (clock, worker))
            return bool(changed)

    @staticmethod
    def _lease(db: sqlite3.Connection, job_id: str, token: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM jobs WHERE id=? AND lease_token=? AND status='running' AND lease_until>?",
                         (job_id, token, time.time())).fetchone()
        if row is None:
            raise LeaseLost("Worker lease expired or was replaced; result was not committed.")
        return row

    def complete(self, job_id: str, lease_token: str, candidates: list[dict], facts: list[dict]) -> str:
        if len(facts) != 5 or {f.get("metric_id") for f in facts} != set(METRIC_UNITS):
            raise ValidationError("Each run must contain exactly the five supported metrics.")
        ids = [c.get("id") for c in candidates]
        if any(not isinstance(c, str) or not c for c in ids) or len(ids) != len(set(ids)):
            raise ValidationError("Candidate IDs must be nonempty and unique within a run.")
        with self.connection(write=True) as db:
            job = self._lease(db, job_id, lease_token)
            document_id = job["document_id"]
            document = json.loads(db.execute("SELECT payload FROM documents WHERE id=?", (document_id,)).fetchone()[0])
            run_id = uuid.uuid4().hex
            for candidate in candidates:
                if candidate.get("document_id") != document_id:
                    raise ValidationError("Candidate belongs to another document.")
                bbox = candidate.get("bbox", [])
                if len(bbox) != 4 or any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in bbox):
                    raise ValidationError("Candidate requires parser-provided coordinates.")
                page = candidate.get("page")
                width, height = candidate.get("page_width"), candidate.get("page_height")
                if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= document["page_count"]:
                    raise ValidationError("Candidate references a nonexistent PDF page.")
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in (width, height)):
                    raise ValidationError("Candidate requires the actual page dimensions.")
                if not (0 <= bbox[0] < bbox[2] <= width and 0 <= bbox[1] < bbox[3] <= height):
                    raise ValidationError("Candidate coordinates are outside its PDF page.")
            db.execute("INSERT INTO runs VALUES (?,?,?,?)", (run_id, job_id, document_id, now()))
            db.execute("INSERT INTO raw_extractions VALUES (?,?)", (run_id, encode(facts)))
            for candidate in candidates:
                db.execute("INSERT INTO candidates VALUES (?,?,?)", (run_id, candidate["id"], encode(candidate)))
            for original in facts:
                fact = dict(original)
                if fact.get("candidate_id") is not None and fact["candidate_id"] not in ids:
                    raise ValidationError("Fact evidence does not belong to this run.")
                if fact.get("unit") != METRIC_UNITS[fact["metric_id"]]:
                    raise ValidationError("Fact has an unexpected unit.")
                value = fact.get("value")
                if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)):
                    raise ValidationError("Fact value must be finite or null.")
                fact.update(id=uuid.uuid4().hex, document_id=document_id, run_id=run_id,
                            status="pending" if value is not None else "missing", review=None, version=0)
                fact.pop("evidence", None)
                db.execute("INSERT INTO facts VALUES (?,?,?,?)", (fact["id"], run_id, document_id, encode(fact)))
            self._lease(db, job_id, lease_token)
            db.execute("UPDATE jobs SET status='completed', stage='completed', error=NULL, updated_at=?, "
                       "lease_token=NULL, lease_until=NULL WHERE id=?", (now(), job_id))
            return run_id

    def fail(self, job_id: str, lease_token: str, error: str) -> bool:
        with self.connection(write=True) as db:
            try:
                self._lease(db, job_id, lease_token)
            except LeaseLost:
                return False
            db.execute("UPDATE jobs SET status='failed', stage='failed', error=?, updated_at=?, "
                       "lease_token=NULL, lease_until=NULL WHERE id=?", (str(error)[:4000], now(), job_id))
            return True

    @staticmethod
    def _effective(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
        fact = json.loads(row["payload"])
        reviews = db.execute("SELECT payload,version,created_at FROM reviews WHERE fact_id=? ORDER BY version",
                             (fact["id"],)).fetchall()
        for item in reviews:
            # Apply the same rule to legacy history, without rewriting append-only rows.
            review = bind_restatement_confirmation(fact, json.loads(item["payload"]))
            for key in ("value", "period", "entity_scope", "basis", "observation", "restatement_resolved", "candidate_id"):
                if key in review:
                    fact[key] = review[key]
            fact["status"] = "rejected" if review["action"] == "reject" else "accepted"
            fact["version"] = item["version"]
            fact["review"] = dict(review, created_at=item["created_at"], version=item["version"])
        evidence = db.execute("SELECT payload FROM candidates WHERE run_id=? AND id=?",
                              (fact["run_id"], fact.get("candidate_id"))).fetchone()
        fact["evidence"] = json.loads(evidence[0]) if evidence else None
        return fact

    def fact(self, fact_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                raise NotFound("Fact not found.")
            return self._effective(db, row)

    def facts(self, document_id: str) -> list[dict]:
        self.document(document_id)
        with self.connection() as db:
            run = db.execute("SELECT id FROM runs WHERE document_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                             (document_id,)).fetchone()
            if run is None:
                return []
            rows = db.execute("SELECT * FROM facts WHERE run_id=? ORDER BY rowid", (run[0],)).fetchall()
            return [self._effective(db, row) for row in rows]

    def candidates(self, document_id: str) -> list[dict]:
        self.document(document_id)
        with self.connection() as db:
            run = db.execute("SELECT id FROM runs WHERE document_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                             (document_id,)).fetchone()
            return [] if run is None else [json.loads(row[0]) for row in db.execute("SELECT payload FROM candidates WHERE run_id=?", (run[0],))]

    def review(self, fact_id: str, review: dict) -> dict:
        permitted = {"action", "value", "period", "entity_scope", "basis", "observation", "reason",
                     "expected_version", "restatement_resolved", "candidate_id"}
        if set(review) - permitted:
            raise ValidationError("Unknown review fields.")
        if review.get("action") not in {"accept", "edit", "reject"}:
            raise ValidationError("Choose accept, edit or reject.")
        if not isinstance(review.get("reason"), str) or not review["reason"].strip():
            raise ValidationError("A review reason is required.")
        if "restatement_resolved" in review and not isinstance(review["restatement_resolved"], bool):
            raise ValidationError("restatement_resolved must be true or false.")
        with self.connection(write=True) as db:
            row = db.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
            if row is None:
                raise NotFound("Fact not found.")
            fact = self._effective(db, row)
            expected = review.get("expected_version")
            if isinstance(expected, bool) or not isinstance(expected, int):
                raise ValidationError("expected_version must be an integer.")
            if expected != fact["version"]:
                raise Conflict("This fact was reviewed in another session. Refresh before saving.")
            review = bind_restatement_confirmation(fact, review)
            effective = dict(fact)
            effective.update({key: value for key, value in review.items() if key in permitted})
            if "candidate_id" in review:
                candidate = db.execute("SELECT payload FROM candidates WHERE run_id=? AND id=?",
                                       (fact["run_id"], review["candidate_id"])).fetchone()
                if candidate is None:
                    raise ValidationError("Selected evidence must exist in the same extraction run and document.")
                selected = json.loads(candidate[0])
                if selected.get("document_id") != fact["document_id"]:
                    raise ValidationError("Selected evidence belongs to a different document.")
                if fact["metric_id"] not in selected.get("metric_ids", []):
                    raise ValidationError("Selected evidence does not identify this metric.")
                effective["evidence"] = selected
            if review["action"] != "reject":
                if not effective.get("evidence"):
                    raise ValidationError("Accepting a result requires existing source evidence.")
                if fact["metric_id"] not in effective["evidence"].get("metric_ids", []):
                    raise ValidationError("Source evidence does not identify this metric.")
                value = effective.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValidationError("A finite, nonnegative value is required.")
                try:
                    period = date.fromisoformat(effective.get("period") or "")
                    if period.isoformat() != effective.get("period"):
                        raise ValueError("Use YYYY-MM-DD")
                except (ValueError, TypeError) as exc:
                    raise ValidationError("An explicit ISO reporting date is required.") from exc
                document = json.loads(db.execute("SELECT payload FROM documents WHERE id=?", (fact["document_id"],)).fetchone()[0])
                if period.year != document["year"]:
                    raise ValidationError("Resolve the current reporting year before accepting.")
                for field in ("entity_scope", "basis"):
                    value = effective.get(field)
                    if not isinstance(value, str) or value.strip().lower() in {"", "unknown", "unspecified", "n/a", "none", "null"}:
                        raise ValidationError(f"Resolve {field} before accepting.")
                if effective.get("observation") not in {"point_in_time", "quarter_average", "year_average"}:
                    raise ValidationError("Resolve the observation basis before accepting.")
            db.execute("INSERT INTO reviews (fact_id,version,created_at,payload) VALUES (?,?,?,?)",
                       (fact_id, fact["version"] + 1, now(), encode(review)))
            return self._effective(db, row)

    def stats(self) -> dict:
        documents, jobs = self.documents(), self.jobs()
        facts = [fact for doc in documents for fact in self.facts(doc["id"])]
        with self.connection() as db:
            runs = [dict(row) for row in db.execute("SELECT r.*, j.method FROM runs r JOIN jobs j ON j.id=r.job_id "
                                                   "ORDER BY r.created_at DESC LIMIT 20")]
        for run in runs:
            artifact = self.data_dir / "run_artifacts" / f"{run['job_id']}.json"
            if artifact.is_file():
                try:
                    metadata = json.loads(artifact.read_text(encoding="utf-8"))
                    if metadata.get("run_id") == run["id"]:
                        for key in ("elapsed_seconds", "parser_cache_hit", "candidate_count", "fact_count", "started_at", "completed_at", "model"):
                            if key in metadata:
                                run[key] = metadata[key]
                except (OSError, ValueError, AttributeError):
                    pass
        return dict(documents=len(documents), jobs_completed=sum(j["status"] == "completed" for j in jobs),
                    jobs_failed=sum(j["status"] == "failed" for j in jobs),
                    facts_pending=sum(f["status"] == "pending" for f in facts),
                    facts_accepted=sum(f["status"] == "accepted" for f in facts), latest_runs=runs)
