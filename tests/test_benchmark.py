from disclosure.benchmark import score_fact, summarize


def test_correct_number_wrong_year_not_complete_fact():
    g = {"metric_id": "cet1_ratio", "value": 12.3, "unit": "percent", "period": "2025-06-30",
         "entity_scope": "Group", "basis": "APRA Level 2", "observation": "point_in_time", "evidence_page": 5, "restated": False}
    p = {**g, "period": "2024-06-30", "candidate_id": "c1", "status": "pending"}
    scores = score_fact(p, g, [{"id": "c1", "page": 5, "metric_ids": ["cet1_ratio"], "numbers": ["12.3%"]}])
    assert scores["value_correct"] is True
    assert scores["complete_fact_correct"] is False


def test_unverified_gold_not_silently_in_denominator():
    s = summarize([{"gold_verified": False, "prediction": {"value": None}}])
    assert s["requested_fields"] == 1 and s["source_verified_labels"] == 0


def test_wrong_evidence_page_not_complete():
    gold = {"metric_id": "rwa", "value": 496100, "unit": "AUD_million", "period": "2025-06-30",
            "entity_scope": "Group", "basis": "APRA Level 2", "observation": "point_in_time", "evidence_page": 5, "restated": False}
    pred = {**gold, "candidate_id": "c1"}
    candidate = {"id": "c1", "page": 9, "metric_ids": ["rwa"], "text": "RWA $496.1 billion", "numbers": ["496.1"]}
    assert score_fact(pred, gold, [candidate])["complete_fact_correct"] is False
    candidate["page"] = 5
    assert score_fact(pred, gold, [candidate])["complete_fact_correct"] is True


def test_source_precision_boundary():
    from disclosure.benchmark import equivalent

    assert equivalent(12.55, 12.5, 0.05)
    assert not equivalent(12.551, 12.5, 0.05)


def test_changed_pdf_cannot_use_stale_labels(tmp_path):
    import hashlib

    import pytest

    from disclosure.benchmark import verify_source

    path = tmp_path / "source"
    path.write_bytes(b"replacement")
    with pytest.raises(ValueError, match="locked manifest"):
        verify_source(path, {"id": "x", "sha256": "old"}, [])
    digest = hashlib.sha256(b"replacement").hexdigest()
    with pytest.raises(ValueError, match="another PDF"):
        verify_source(path, {"id": "x", "sha256": digest}, [{"source_pdf_sha256": "old"}])
