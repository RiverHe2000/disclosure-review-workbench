"""A separate, restartable worker. SQLite is the queue and source of truth."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .store import LeaseLost, Store

log = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def default_data_dir() -> Path:
    return Path(os.environ.get("DISCLOSURE_DATA_DIR", str(PROJECT_ROOT / "data" / "workbench")))


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp-" + uuid.uuid4().hex[:8])
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def process_one(store: Store, data_dir: Path, worker_id: str = "local-worker", model_path: str | None = None) -> bool:
    from .extraction import RULES_VERSION, extract_rules
    from .parser import PARSER_VERSION, parse_pdf

    store.touch_worker(worker_id)
    job = store.claim(worker_id=worker_id, lease_seconds=120)
    if job is None:
        return False
    stop, lost = threading.Event(), threading.Event()
    stage = ["parsing"]

    def maintain_lease() -> None:
        while not stop.wait(10):
            try:
                store.touch_worker(worker_id)
                if not store.heartbeat(job["id"], job["lease_token"], stage[0], lease_seconds=120):
                    lost.set()
                    return
            except Exception:
                log.exception("Heartbeat failed")
                lost.set()
                return

    heartbeat = threading.Thread(target=maintain_lease, daemon=True, name="lease-heartbeat")
    heartbeat.start()
    started = datetime.now(UTC).isoformat()
    clock = time.perf_counter()
    try:
        document = job["document"]
        log.info("Processing %s %s: %s", document["bank"], document["year"], job["method"])
        store.heartbeat(job["id"], job["lease_token"], stage[0], lease_seconds=120)
        candidates = parse_pdf(Path(job["path"]), document["id"])
        if lost.is_set():
            raise LeaseLost("Worker lease expired while parsing")
        stage[0] = "extracting"
        store.heartbeat(job["id"], job["lease_token"], stage[0], lease_seconds=120)
        metadata = {"job_id": job["id"], "method": job["method"], "started_at": started,
                    "candidate_count": len(candidates), "parser_cache_hit": False,
                    "parser_version": PARSER_VERSION, "rules_version": RULES_VERSION}
        if job["method"] == "qwen":
            from .model import DEFAULT_MODEL, LAST_STATS, extract_qwen

            facts = extract_qwen(candidates, document, model_path or os.environ.get("DISCLOSURE_MODEL_PATH", DEFAULT_MODEL))
            metadata["model"] = dict(LAST_STATS)
        else:
            facts = extract_rules(candidates, document)
        if lost.is_set():
            raise LeaseLost("Worker lease expired while extracting")
        stage[0] = "saving"
        run_id = store.complete(job["id"], job["lease_token"], candidates, facts)
        metadata.update(run_id=run_id, completed_at=datetime.now(UTC).isoformat(),
                        elapsed_seconds=round(time.perf_counter() - clock, 3), fact_count=len(facts))
        try:
            atomic_json(data_dir / "run_artifacts" / f"{job['id']}.json", metadata)
        except OSError:
            log.exception("Result committed, but optional timing artifact could not be saved: %s", job["id"])
        log.info("Completed %s in %.1fs", job["id"], metadata["elapsed_seconds"])
    except LeaseLost:
        log.warning("Discarded result from expired lease %s", job["id"])
    except Exception as exc:
        log.exception("Job %s failed", job["id"])
        store.fail(job["id"], job["lease_token"], f"{type(exc).__name__}: {exc}"[:1800])
    finally:
        stop.set()
        heartbeat.join(timeout=2)
    return True


def run_worker(data_dir: Path, *, once: bool = False, drain: bool = False, model_path: str | None = None) -> None:
    store = Store(data_dir)
    worker_id = f"worker-{os.getpid()}"
    while True:
        processed = process_one(store, data_dir, worker_id, model_path)
        if once or (drain and not processed):
            return
        if not processed:
            time.sleep(2)
