"""Checks use candidates extracted from the real CBA 2023 PDF, never generated PDFs."""

import json
from pathlib import Path

import pytest

from disclosure.extraction import METRICS, extract_rules, infer_context, rank_candidates
from disclosure.parser import parse_pdf

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def candidates():
    fixture = Path(__file__).parent / "fixtures/cba_2023_candidates.json"
    return json.loads(fixture.read_text(encoding="utf-8"))["candidates"]


def test_exactly_five_reviewable_drafts(candidates):
    facts = extract_rules(candidates, {"id": "cba_2023", "year": 2023})
    assert {fact["metric_id"] for fact in facts} == set(METRICS)
    assert all(fact["status"] in {"pending", "missing"} for fact in facts)
    assert all(fact["method"] == "rules" for fact in facts)


def test_development_source_numbers(candidates):
    facts = extract_rules(candidates, {"id": "cba_2023", "year": 2023})
    assert {fact["metric_id"]: fact["value"] for fact in facts} == {
        "cet1_ratio": 12.2, "total_capital_ratio": 20.0, "rwa": 467992.0, "lcr": 131.0, "nsfr": 124.0,
    }


def test_no_evidence_abstains():
    facts = extract_rules([], {"id": "empty", "year": 2025})
    assert len(facts) == 5
    assert all(fact["value"] is None and fact["candidate_id"] is None for fact in facts)


def test_document_year_does_not_invent_an_observation_date(candidates):
    candidate = next(c for c in candidates if c["page"] == 289 and "lcr" in c["metric_ids"])
    context = infer_context(candidate, {"id": "different", "year": 2026}, "lcr")
    assert context["period"] is None


def test_scope_not_inferred_from_bank_name(candidates):
    candidate = next(c for c in candidates if c["page"] == 287 and "rwa" in c["metric_ids"])
    context = infer_context(candidate, {"id": "cba_2023", "year": 2023, "bank": "CBA"}, "rwa")
    assert context["entity_scope"] == "unknown"
    assert context["basis"] == "unknown"


def test_regulatory_threshold_is_not_a_reported_nsfr(candidates):
    thresholds = [c for c in candidates if "greater than 100%" in c["text"] and "nsfr" in c["metric_ids"]]
    assert thresholds
    fact = next(f for f in extract_rules(thresholds, {"id": "cba_2023", "year": 2023}) if f["metric_id"] == "nsfr")
    assert fact["value"] is None


def test_date_number_is_not_mistaken_for_ratio(candidates):
    source = [c for c in candidates if "NSFR as at 30 June 2023 was 124%" in c["text"]]
    assert source
    fact = next(f for f in extract_rules(source, {"id": "cba_2023", "year": 2023}) if f["metric_id"] == "nsfr")
    assert fact["value"] == 124


def test_shared_ranking_is_bounded_and_source_only(candidates):
    doc = {"id": "cba_2023", "year": 2023}
    ranked = rank_candidates(candidates, "cet1_ratio", doc)
    assert len(ranked) <= 6
    assert all("cet1_ratio" in c["metric_ids"] for c in ranked)
    assert ranked == rank_candidates(list(reversed(candidates)), "cet1_ratio", doc)


def test_source_coordinates_stay_inside_visible_pdf_page(candidates):
    assert len({c["id"] for c in candidates}) == len(candidates)
    for candidate in candidates:
        x0, top, x1, bottom = candidate["bbox"]
        assert 0 <= x0 < x1 <= candidate["page_width"] + 0.01
        assert 0 <= top < bottom <= candidate["page_height"] + 0.01
        assert candidate["page"] >= 1


def test_real_pdf_matches_stable_fixture(candidates):
    source = ROOT / "data/raw/cba_2023.pdf"
    if not source.exists():
        pytest.skip("Download the official CBA corpus to run the native-PDF integration check")
    reparsed = parse_pdf(source, "cba_2023")
    assert reparsed == candidates
    # A store-assigned document ID must not change the evidence identity.
    assert all(candidate["id"].startswith("c_") for candidate in reparsed)


def test_manifest_has_disjoint_splits_and_official_urls():
    manifest = json.loads((ROOT / "data/manifest.json").read_text())
    assert len(manifest) == 12
    assert len({item["id"] for item in manifest}) == 12
    assert sum(item["split"] == "development" for item in manifest) == 6
    assert sum(item["split"] == "temporal_test" for item in manifest) == 3
    assert sum(item["split"] == "bank_test" for item in manifest) == 3
    assert all(item["url"].startswith("https://www.") for item in manifest)


def test_gold_coverage_is_explicit_and_current_period_only():
    gold = json.loads((ROOT / "data/gold.json").read_text(encoding="utf-8"))
    assert len(gold) == 60
    for fact in gold:
        if fact["annotation_status"] == "verified":
            assert fact["value"] is not None
            assert fact["period"].startswith(fact["document_id"][-4:])
            assert fact["evidence_page"] > 0 and fact["evidence_quote"]
            assert fact["value_tolerance"] > 0
        else:
            assert fact["value"] is None
    assert all(fact["annotation_by"] == "source_review_assisted" for fact in gold)
