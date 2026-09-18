from __future__ import annotations

import csv
import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure.api import create_app
from disclosure.compare import compare_documents, export_csv, export_markdown
from disclosure.store import METRIC_UNITS, Conflict, LeaseLost, Store, ValidationError


def pdf_bytes(text: str = "Capital disclosure 2024") -> bytes:
    stream = f"BT /F1 12 Tf 40 750 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"]
    result = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(result)
    result += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    result += b"".join(f"{offset:010} 00000 n \n".encode() for offset in offsets[1:])
    result += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return result


def document(store: Store, year: int = 2024, bank: str = "CBA") -> dict:
    path = store.data_dir / f"source-{bank}-{year}.pdf"
    path.write_bytes(pdf_bytes(f"{bank} {year}"))
    return store.register_document(path, bank, year, f"https://example.org/{bank}/{year}.pdf")


def extraction(doc: dict, value: float = 12.0, **overrides):
    candidate = dict(id="candidate-1", document_id=doc["id"], page=1, bbox=[40, 30, 500, 70],
                     page_width=612, page_height=792, text="CET1 ratio 12.0 2024", context="APRA Level 2 Group",
                     metric_ids=list(METRIC_UNITS), numbers=["12.0", "2024"])
    facts = []
    for metric_id, unit in METRIC_UNITS.items():
        fact = dict(metric_id=metric_id, value=value, unit=unit, period=f"{doc['year']}-06-30",
                    entity_scope="Group", basis="APRA Level 2", observation="point_in_time", restated=False,
                    candidate_id=candidate["id"], status="pending", issues=[], method="rules")
        fact.update(overrides)
        facts.append(fact)
    return [candidate], facts


def finish(store: Store, doc: dict, value: float = 12.0, version: str = "v1", **overrides):
    job = store.enqueue(doc["id"], "rules", pipeline_version=version)
    claim = store.claim()
    assert claim["id"] == job["id"]
    candidates, facts = extraction(doc, value, **overrides)
    store.complete(job["id"], claim["lease_token"], candidates, facts)
    return store.facts(doc["id"])


def accept_all(store: Store, doc: dict, **overrides):
    for fact in store.facts(doc["id"]):
        body = dict(action="accept", reason="Checked original PDF", expected_version=fact["version"])
        body.update(overrides)
        store.review(fact["id"], body)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "state")


def test_hash_dedup_and_filename_safety(store):
    doc = document(store)
    same = store.register_document(store.document_path(doc["id"]), "CBA", 2024, filename="../../escape.pdf")
    assert same["id"] == doc["id"]
    assert len(store.documents()) == 1
    assert store.document_path(doc["id"]).parent == store.pdf_dir
    with pytest.raises(Conflict):
        store.register_document(store.document_path(doc["id"]), "NAB", 2024)


def test_queue_idempotency_and_restart(store):
    doc = document(store)
    job = store.enqueue(doc["id"], "rules", pipeline_version="rules-v1")
    restarted = Store(store.data_dir)
    assert restarted.enqueue(doc["id"], "rules", pipeline_version="rules-v1")["id"] == job["id"]
    claim = restarted.claim("worker-2")
    assert claim["attempts"] == 1 and claim["document"]["id"] == doc["id"]
    assert Path(claim["path"]).is_file()
    assert store.job(job["id"])["status"] == "running"


def test_two_workers_cannot_claim_same_job(store):
    doc = document(store)
    store.enqueue(doc["id"], "rules")
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda worker: Store(store.data_dir).claim(worker), ["a", "b"]))
    assert sum(item is not None for item in result) == 1


def test_expired_lease_reclaimed_stale_worker_cannot_write(store):
    doc = document(store)
    store.enqueue(doc["id"], "rules")
    first = store.claim("first")
    with store.connection(write=True) as db:
        db.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (first["id"],))
    assert not store.heartbeat(first["id"], first["lease_token"])
    second = Store(store.data_dir).claim("second")
    assert second["id"] == first["id"] and second["attempts"] == 2
    candidates, facts = extraction(doc)
    with pytest.raises(LeaseLost):
        store.complete(first["id"], first["lease_token"], candidates, facts)
    assert not store.fail(first["id"], first["lease_token"], "stale failure")
    assert store.facts(doc["id"]) == []
    store.complete(second["id"], second["lease_token"], candidates, facts)
    assert store.job(second["id"])["status"] == "completed"
    assert len(store.facts(doc["id"])) == 5


def test_heartbeat_extends_lease_and_worker_health(store):
    doc = document(store)
    store.enqueue(doc["id"], "rules")
    job = store.claim()
    assert store.heartbeat(job["id"], job["lease_token"], "extracting", lease_seconds=300)
    assert store.job(job["id"])["stage"] == "extracting"
    assert store.worker_active()
    assert not store.heartbeat(job["id"], "wrong-token")


def test_failed_job_requires_explicit_retry(store):
    doc = document(store)
    queued = store.enqueue(doc["id"], "rules")
    job = store.claim()
    assert store.fail(job["id"], job["lease_token"], "model unavailable")
    assert store.enqueue(doc["id"], "rules")["status"] == "failed"
    assert store.claim() is None
    assert store.enqueue(doc["id"], "rules", retry=True)["id"] == queued["id"]
    assert store.claim()["attempts"] == 2


def test_bad_completion_rolls_back_everything(store):
    doc = document(store)
    store.enqueue(doc["id"], "rules")
    claim = store.claim()
    candidates, facts = extraction(doc)
    facts[-1]["candidate_id"] = "not-real"
    with pytest.raises(ValidationError):
        store.complete(claim["id"], claim["lease_token"], candidates, facts)
    with store.connection() as db:
        for table in ("runs", "facts", "candidates"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_review_append_only_original_immutable_and_optimistic_version(store):
    doc = document(store)
    fact = finish(store, doc)[0]
    edited = store.review(fact["id"], dict(action="edit", reason="Original PDF checked", expected_version=0, value=13.2))
    assert edited["value"] == 13.2 and edited["status"] == "accepted" and edited["version"] == 1
    with pytest.raises(Conflict):
        store.review(fact["id"], dict(action="reject", reason="Stale browser", expected_version=0))
    rejected = store.review(fact["id"], dict(action="reject", reason="Different basis", expected_version=1))
    assert rejected["version"] == 2 and rejected["status"] == "rejected"
    with store.connection() as db:
        original = json.loads(db.execute("SELECT payload FROM facts WHERE id=?", (fact["id"],)).fetchone()[0])
        assert original["value"] == 12 and original["status"] == "pending"
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2
    with pytest.raises(sqlite3.IntegrityError), store.connection(write=True) as db:
        db.execute("UPDATE reviews SET version=5")


def test_repeated_run_keeps_review_history_but_new_facts_need_review(store):
    doc = document(store)
    old = finish(store, doc)[0]
    accept_all(store, doc)
    new = finish(store, doc, version="v2")[0]
    assert new["id"] != old["id"] and new["status"] == "pending"
    assert store.fact(old["id"])["status"] == "accepted"
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2


@pytest.mark.parametrize("overrides", [{"entity_scope": "unknown"}, {"basis": ""}, {"observation": "unknown"},
                                     {"period": None}, {"period": "2023-06-30"}, {"candidate_id": None}])
def test_review_blocks_unresolved_context_or_missing_evidence(store, overrides):
    doc = document(store)
    fact = finish(store, doc, **overrides)[0]
    with pytest.raises(ValidationError):
        store.review(fact["id"], dict(action="accept", reason="Checked", expected_version=0))


def test_review_can_resolve_semantics_but_cannot_override_unit(store):
    doc = document(store)
    fact = finish(store, doc, period="2023-06-30", observation="unknown", basis="unknown")[0]
    with pytest.raises(ValidationError):
        store.review(fact["id"], dict(action="edit", reason="Change units", expected_version=0, unit="AUD_billion"))
    saved = store.review(fact["id"], dict(action="edit", reason="Verified current-period column", expected_version=0,
                                        period="2024-06-30", observation="point_in_time", basis="APRA Level 2"))
    assert saved["status"] == "accepted"


def test_review_can_recover_missing_using_existing_metric_candidate(store):
    doc = document(store)
    fact = finish(store, doc, candidate_id=None, value=None)[0]
    saved = store.review(fact["id"], dict(action="edit", reason="Found value in source row", expected_version=0,
                                        candidate_id="candidate-1", value=12.5))
    assert saved["value"] == 12.5 and saved["evidence"]["id"] == "candidate-1"
    assert saved["status"] == "accepted"
    with store.connection() as db:
        raw = json.loads(db.execute("SELECT payload FROM facts WHERE id=?", (fact["id"],)).fetchone()[0])
        assert raw["candidate_id"] is None and raw["value"] is None


def test_review_rejects_candidate_from_another_run(store):
    doc = document(store)
    fact = finish(store, doc, candidate_id=None)[0]
    other_doc = document(store, 2023)
    store.enqueue(other_doc["id"], "rules")
    claim = store.claim()
    candidates, facts = extraction(other_doc)
    candidates[0]["id"] = "other-run-candidate"
    for item in facts:
        item["candidate_id"] = "other-run-candidate"
    store.complete(claim["id"], claim["lease_token"], candidates, facts)
    with pytest.raises(ValidationError, match="same extraction run"):
        store.review(fact["id"], dict(action="edit", reason="Wrong document evidence", expected_version=0,
                                     candidate_id="other-run-candidate"))


def test_review_rejects_candidate_for_different_metric(store):
    doc = document(store)
    store.enqueue(doc["id"], "rules")
    claim = store.claim()
    candidates, facts = extraction(doc)
    candidates[0]["metric_ids"] = ["rwa"]
    store.complete(claim["id"], claim["lease_token"], candidates, facts)
    fact = store.facts(doc["id"])[0]
    with pytest.raises(ValidationError, match="identify this metric"):
        store.review(fact["id"], dict(action="edit", reason="Wrong metric evidence", expected_version=0,
                                     candidate_id="candidate-1"))


def test_csv_neutralizes_formula_text(store):
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left)
    finish(store, right)
    accept_all(store, left, reason="=HYPERLINK(evil)")
    result = compare_documents(store, left["id"], right["id"])
    rows = list(csv.DictReader(io.StringIO(export_csv(result))))
    assert rows[0]["left_review_reason"].startswith("'=HYPERLINK")


def test_comparison_requires_review_and_computes_correct_units(store):
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left, value=10)
    finish(store, right, value=12)
    assert all(not row["comparable"] and row["delta"] is None for row in compare_documents(store, left["id"], right["id"])["rows"])
    accept_all(store, left)
    accept_all(store, right)
    rows = compare_documents(store, left["id"], right["id"])["rows"]
    assert all(row["comparable"] and row["delta"] == 2 for row in rows)
    assert rows[0]["unit"] == "percent" and rows[0]["delta_unit"] == "percentage_points"
    assert rows[0]["relative_change"] is None
    rwa = next(row for row in rows if row["metric_id"] == "rwa")
    assert rwa["unit"] == rwa["delta_unit"] == "AUD_million" and rwa["relative_change"] == 0.2


def test_http_compare_and_exports_distinguish_source_and_delta_units(tmp_path):
    app = create_app(tmp_path / "api")
    store = app.state.store
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left, value=10)
    finish(store, right, value=12)
    accept_all(store, left)
    accept_all(store, right)
    params = {"left": left["id"], "right": right["id"]}
    with TestClient(app) as client:
        response = client.get("/api/compare", params=params)
        assert response.status_code == 200
        rows = {row["metric_id"]: row for row in response.json()["rows"]}
        ratio, rwa = rows["cet1_ratio"], rows["rwa"]
        assert ratio["left"]["value"] == 10 and ratio["right"]["value"] == 12
        assert ratio["unit"] == "percent" and ratio["delta_unit"] == "percentage_points" and ratio["delta"] == 2
        assert rwa["unit"] == rwa["delta_unit"] == "AUD_million"
        exported = client.get("/api/export", params={**params, "format": "csv"})
        csv_rows = list(csv.DictReader(io.StringIO(exported.text)))
        assert csv_rows[0]["left_unit"] == csv_rows[0]["right_unit"] == "percent"
        assert csv_rows[0]["delta_unit"] == "percentage_points" and csv_rows[0]["delta"] == "2.0"
        markdown = client.get("/api/export", params={**params, "format": "markdown"}).text
        assert "change: 2.0 percentage_points" in markdown
        assert "change: 2.0 AUD_million" in markdown


@pytest.mark.parametrize("years", [(2022, 2023), (2023, 2022), (2025, 2026)])
def test_http_comparison_blocks_unvalidated_years_and_capital_boundary(tmp_path, years):
    app = create_app(tmp_path / "api")
    store = app.state.store
    left, right = document(store, years[0]), document(store, years[1])
    finish(store, left, value=10, restated=True)
    finish(store, right, value=12)
    accept_all(store, left, restatement_resolved=True)
    accept_all(store, right)
    with TestClient(app) as client:
        result = client.get("/api/compare", params={"left": left["id"], "right": right["id"]})
    assert result.status_code == 200
    for row in result.json()["rows"]:
        assert not row["comparable"] and row["delta"] is None and row["relative_change"] is None
        assert any("additional framework confirmation" in reason for reason in row["reasons"])
        crosses_capital_boundary = 2022 in years and row["metric_id"] in {"cet1_ratio", "total_capital_ratio", "rwa"}
        assert any("capital framework boundary" in reason for reason in row["reasons"]) == crosses_capital_boundary


@pytest.mark.parametrize("overrides,reason", [({"basis": "APRA Level 1"}, "regulatory basis"),
                                            ({"entity_scope": "Bank"}, "entity scope"),
                                            ({"observation": "quarter_average"}, "observation basis"),
                                            ({"period": "2024-09-30"}, "fiscal date"),
                                            ({"restated": True}, "restatement")])
def test_comparison_blocks_semantic_mismatches(store, overrides, reason):
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left)
    finish(store, right, **overrides)
    accept_all(store, left)
    accept_all(store, right)
    rows = compare_documents(store, left["id"], right["id"])["rows"]
    assert all(not row["comparable"] for row in rows)
    assert reason in "; ".join(rows[0]["reasons"])


def test_restatement_explicit_resolution_and_zero_baseline(store):
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left, value=0, restated=True)
    finish(store, right)
    accept_all(store, left, restatement_resolved=True)
    accept_all(store, right)
    rows = compare_documents(store, left["id"], right["id"])["rows"]
    assert all(row["comparable"] for row in rows)
    assert next(row for row in rows if row["metric_id"] == "rwa")["relative_change"] is None


@pytest.mark.parametrize("change", [
    {"candidate_id": "candidate-2"}, {"value": 13.0}, {"period": "2023-09-30"},
    {"entity_scope": "Bank"}, {"basis": "APRA Level 1"}, {"observation": "quarter_average"},
])
def test_restatement_confirmation_expires_when_reviewed_context_changes(store, change):
    left, right = document(store, 2023), document(store, 2024)
    store.enqueue(left["id"], "rules")
    claimed = store.claim()
    candidates, facts = extraction(left, restated=True)
    candidates.append(dict(candidates[0], id="candidate-2", text="Alternative CET1 disclosure 13.0"))
    store.complete(claimed["id"], claimed["lease_token"], candidates, facts)
    finish(store, right)
    accept_all(store, left, restatement_resolved=True)
    accept_all(store, right)
    fact = store.facts(left["id"])[0]
    assert compare_documents(store, left["id"], right["id"])["rows"][0]["comparable"]

    updated = store.review(fact["id"], dict(action="edit", reason="Updated source context", expected_version=1, **change))
    assert updated["status"] == "accepted" and updated["restatement_resolved"] is False
    assert Store(store.data_dir).fact(fact["id"])["restatement_resolved"] is False
    row = compare_documents(store, left["id"], right["id"])["rows"][0]
    assert not row["comparable"] and row["delta"] is None
    assert any("restatement requires explicit human resolution" in reason for reason in row["reasons"])
    with store.connection() as db:
        reviews = [json.loads(item[0]) for item in db.execute(
            "SELECT payload FROM reviews WHERE fact_id=? ORDER BY version", (fact["id"],))]
        assert reviews[0]["restatement_resolved"] is True
        assert reviews[1]["restatement_resolved"] is False


def test_restatement_edit_can_explicitly_reconfirm_new_value(store):
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left, restated=True)
    finish(store, right)
    accept_all(store, left, restatement_resolved=True)
    accept_all(store, right)
    fact = store.facts(left["id"])[0]
    updated = store.review(fact["id"], dict(action="edit", reason="Checked corrected value and its restatement basis",
                                          expected_version=1, value=13.0, restatement_resolved=True))
    assert updated["restatement_resolved"] is True
    row = compare_documents(store, left["id"], right["id"])["rows"][0]
    assert row["comparable"] and row["delta"] == -1.0


def test_note_only_or_unchanged_fields_keep_restatement_confirmation(store):
    doc = document(store)
    fact = finish(store, doc, restated=True)[0]
    store.review(fact["id"], dict(action="accept", reason="Checked restatement", expected_version=0, restatement_resolved=True))
    note = store.review(fact["id"], dict(action="accept", reason="Added review explanation", expected_version=1))
    assert note["restatement_resolved"] is True
    unchanged = store.review(fact["id"], dict(action="edit", reason="Same fields sent by form", expected_version=2,
                                            value=12, period=fact["period"], candidate_id=fact["candidate_id"],
                                            entity_scope=fact["entity_scope"], basis=fact["basis"], observation=fact["observation"]))
    assert unchanged["restatement_resolved"] is True


def test_legacy_review_history_replays_with_confirmation_bound_to_context(store):
    doc = document(store)
    fact = finish(store, doc, restated=True)[0]
    store.review(fact["id"], dict(action="accept", reason="Checked original restatement", expected_version=0,
                                 restatement_resolved=True))
    # Emulate a previously persisted review from before the confirmation-reset fix.
    legacy = dict(action="edit", reason="Changed value without reconfirming", expected_version=1, value=13.0)
    with store.connection(write=True) as db:
        db.execute("INSERT INTO reviews (fact_id,version,created_at,payload) VALUES (?,?,?,?)",
                   (fact["id"], 2, "2026-09-18T00:00:00+00:00", json.dumps(legacy)))
    assert Store(store.data_dir).fact(fact["id"])["restatement_resolved"] is False
    with store.connection() as db:
        saved = json.loads(db.execute("SELECT payload FROM reviews WHERE fact_id=? AND version=2", (fact["id"],)).fetchone()[0])
    assert saved == legacy  # The append-only historical record was not rewritten.


@pytest.mark.parametrize("forbidden_change", [{"unit": "AUD_million"}, {"restated": False}])
def test_read_only_financial_fields_cannot_bypass_confirmation(store, forbidden_change):
    doc = document(store)
    fact = finish(store, doc, restated=True)[0]
    store.review(fact["id"], dict(action="accept", reason="Checked original restatement", expected_version=0,
                                 restatement_resolved=True))
    with pytest.raises(ValidationError, match="Unknown review fields"):
        store.review(fact["id"], dict(action="edit", reason="Attempted read-only change", expected_version=1,
                                     **forbidden_change))
    unchanged = store.fact(fact["id"])
    assert unchanged["version"] == 1 and unchanged["restated"] is True and unchanged["unit"] == "percent"


def test_export_keeps_sources_review_and_blocking_reasons(store):
    left, right = document(store, 2023), document(store, 2024)
    finish(store, left)
    finish(store, right)
    result = compare_documents(store, left["id"], right["id"])
    rows = list(csv.DictReader(io.StringIO(export_csv(result))))
    assert rows[0]["delta"] == "" and rows[0]["left_status"] == "pending"
    assert rows[0]["left_source_url"].startswith("https:") and rows[0]["left_evidence_page"] == "1"
    assert "has not been accepted" in rows[0]["blocking_reasons"]
    markdown = export_markdown(result)
    assert "physical PDF page 1" in markdown and "Blocking reasons" in markdown


def test_http_upload_job_fact_review_pdf_and_export(tmp_path):
    app = create_app(tmp_path / "api")
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200 and health.json()["worker_active"] is False
        response = client.post("/api/documents", data={"bank": "CBA", "year": "2024"},
                               files={"file": ("../report.pdf", pdf_bytes(), "application/pdf")})
        assert response.status_code == 201
        doc = response.json()
        assert doc["filename"] == "report.pdf"
        assert client.get(f"/api/documents/{doc['id']}/pdf").content == pdf_bytes()
        queued = client.post(f"/api/documents/{doc['id']}/jobs", json={"method": "rules"})
        assert queued.status_code == 202 and queued.json()["status"] == "queued"
        store = app.state.store
        claim = store.claim()
        candidates, facts = extraction(doc)
        store.complete(claim["id"], claim["lease_token"], candidates, facts)
        fact = client.get(f"/api/documents/{doc['id']}/facts").json()[0]
        accepted = client.post(f"/api/facts/{fact['id']}/review", json={"action": "accept", "reason": "Checked source", "expected_version": 0})
        assert accepted.status_code == 200 and accepted.json()["status"] == "accepted"
        stale = client.post(f"/api/facts/{fact['id']}/review", json={"action": "reject", "reason": "Old browser", "expected_version": 0})
        assert stale.status_code == 409
        stats = client.get("/api/stats").json()
        assert stats["facts_accepted"] == 1 and stats["facts_pending"] == 4
        export = client.get("/api/export", params={"left": doc["id"], "right": doc["id"], "format": "markdown"})
        assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
        assert "Select two different" in export.text
        assert client.get("/api/missing").status_code == 404


def test_http_upload_limits_pdf_validation_and_local_origin(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "api")) as client:
        args = dict(data={"bank": "CBA", "year": "2024"}, files={"file": ("fake.pdf", b"not a pdf", "application/pdf")})
        assert client.post("/api/documents", **args).status_code == 422
        assert client.post("/api/documents", headers={"origin": "https://evil.example"}, **args).status_code == 403
        monkeypatch.setattr("disclosure.api.MAX_PDF_BYTES", 8)
        assert client.post("/api/documents", **args).status_code == 413
        assert client.get("/api/health", headers={"host": "evil.example"}).status_code == 400
    assert not list((tmp_path / "api").glob("*.upload"))
