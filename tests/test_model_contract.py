import pytest

from disclosure.model import parse_response, validate_selection


def candidate(text="Common Equity Tier 1 12.3% 11.9%"):
    return {"id": "c1", "text": text, "context": "Group APRA Level 2 at 30 June 2025"}


def prediction(**kw):
    return {"candidate_id": "c1", "raw_value": 12.3, "raw_unit": "percent",
            "period": "2025-06-30", "entity_scope": "Group", "basis": "APRA Level 2",
            "observation": "point_in_time", "restated": False, **kw}


DOC = {"id": "cba_2025", "year": 2025}


def test_evidence_cannot_be_invented():
    f = validate_selection(prediction(candidate_id="invented"), [candidate()], DOC, "cet1_ratio")
    assert f["value"] is None and f["status"] == "missing"


def test_numeric_substring_is_not_evidence():
    f = validate_selection(prediction(), [candidate("CET1 112.3%")], DOC, "cet1_ratio")
    assert f["value"] is None


@pytest.mark.parametrize("v", [True, float("nan"), float("inf"), "12.3"])
def test_malformed_numbers_are_rejected(v):
    f = validate_selection(prediction(raw_value=v), [candidate()], DOC, "cet1_ratio")
    assert f["value"] is None


def test_valid_model_output_still_requires_review():
    f = validate_selection(prediction(), [candidate()], DOC, "cet1_ratio")
    assert f["value"] == 12.3 and f["status"] == "pending"


def test_wrong_year_is_flagged():
    f = validate_selection(prediction(period="2024-06-30"), [candidate()], DOC, "cet1_ratio")
    assert any("current reporting year" in i for i in f["issues"])


def test_billion_conversion():
    f = validate_selection(prediction(raw_value=496.1, raw_unit="AUD_billion"),
                           [candidate("Risk weighted assets $496.1 billion")], DOC, "rwa")
    assert f["value"] == pytest.approx(496100)


def test_invented_reporting_date_becomes_unresolved():
    f = validate_selection(prediction(period="2025-12-31"), [candidate()], DOC, "cet1_ratio")
    assert f["period"] is None and any("page-local evidence" in i for i in f["issues"])


@pytest.mark.parametrize("field", ["raw_unit", "observation", "basis", "entity_scope"])
def test_json_wrong_semantic_types_are_retryable(field):
    with pytest.raises(ValueError):
        validate_selection(prediction(**{field: [] if field not in {"basis", "entity_scope"} else {"x": 1}}), [candidate()], DOC, "cet1_ratio")


def test_json_only_no_execution():
    assert parse_response('```json\n{"raw_value": 12.3}\n```')["raw_value"] == 12.3
    with pytest.raises(ValueError):
        parse_response('__import__("os").system("anything")')
