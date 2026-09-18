"""Reproducible, source-labelled evaluation. No labels enter extraction."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .worker import atomic_json

log = logging.getLogger(__name__)
CONTEXT_FIELDS = ("period", "entity_scope", "basis", "observation", "restated")
SCORER_VERSION = "source-tuple-v2"


def equivalent(value: object, target: object, tolerance: float = 1e-6) -> bool:
    if value is None or target is None or isinstance(value, bool):
        return False
    try:
        return math.isclose(float(value), float(target), rel_tol=0, abs_tol=max(1e-6, tolerance) + 1e-9)
    except (ValueError, TypeError):
        return False


def score_fact(prediction: dict, gold: dict, candidates: list[dict]) -> dict:
    # Use only each label's explicit source precision, never a broad global tolerance.
    cid = prediction.get("candidate_id")
    supporting = [c for c in candidates if c["id"] == cid]
    target_page = gold.get("evidence_page")
    target_pages = gold.get("evidence_pages") or [target_page]
    tolerance = float(gold.get("value_tolerance") or 0)
    def supports_value(c: dict) -> bool:
        scale = 1000 if gold["metric_id"] == "rwa" and re.search(
            r"\bbillion\b|\$\s*b(?:n)?\b", c.get("text", ""), re.I) else 1
        return any(equivalent(float(str(n).replace(",", "").rstrip("%")) * scale, gold["value"], tolerance)
                   for n in c.get("numbers", []))

    candidate_hit = any(c.get("page") in target_pages and gold["metric_id"] in c.get("metric_ids", [])
                        and supports_value(c) for c in candidates)
    selected_evidence_correct = bool(supporting and supporting[0].get("page") in target_pages
                                     and gold["metric_id"] in supporting[0].get("metric_ids", [])
                                     and supports_value(supporting[0]))
    value_correct = equivalent(prediction.get("value"), gold.get("value"), tolerance) and prediction.get("unit") == gold.get("unit")
    context_labelled = all(gold.get(k) not in (None, "", "unknown") for k in CONTEXT_FIELDS)
    context_correct = context_labelled and all(prediction.get(k) == gold.get(k) for k in CONTEXT_FIELDS)
    return {
        "value_correct": value_correct,
        "context_labelled": context_labelled,
        "context_correct": context_correct if context_labelled else None,
        "complete_fact_correct": bool(value_correct and context_correct and selected_evidence_correct) if context_labelled else None,
        "has_prediction": prediction.get("value") is not None,
        "has_evidence": bool(supporting),
        "selected_gold_evidence_correct": selected_evidence_correct,
        "gold_page_candidate_hit": candidate_hit,
        "review_status": prediction.get("status", "missing"),
    }


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if r.get("gold_verified")]
    context_scored = [r for r in scored if r["scores"]["context_labelled"]]
    return {
        "requested_fields": len(rows), "source_verified_labels": len(scored),
        "predictions": sum(r["prediction"].get("value") is not None for r in rows),
        "value_correct": sum(r["scores"]["value_correct"] for r in scored),
        "context_labelled": len(context_scored),
        "complete_fact_correct": sum(r["scores"]["complete_fact_correct"] for r in context_scored),
        "gold_page_candidate_hits": sum(r["scores"]["gold_page_candidate_hit"] for r in scored),
        "predictions_with_evidence": sum(r["scores"]["has_evidence"] for r in scored),
        "incorrect_nonempty_predictions": sum(r["scores"]["has_prediction"] and not r["scores"]["value_correct"] for r in scored),
        "automatically_accepted": sum(r["prediction"].get("status") == "accepted" for r in rows),
    }


def verify_source(path: Path, entry: dict, labels: list[dict]) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != entry.get("sha256"):
        raise ValueError(f"Source hash differs from the locked manifest: {entry['id']}")
    if any(label.get("source_pdf_sha256") != digest for label in labels):
        raise ValueError(f"Gold labels belong to another PDF version: {entry['id']}")
    return digest


def rescore_report(root: Path, report_path: Path) -> dict:
    """Re-evaluate recorded predictions with current labels/scorer; never rerun inference."""
    from .parser import parse_pdf

    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest = {r["id"]: r for r in json.loads((root / "data/manifest.json").read_text(encoding="utf-8"))}
    gold = json.loads((root / "data/gold.json").read_text(encoding="utf-8"))
    labels = {(r["document_id"], r["metric_id"]): r for r in gold}
    grouped = defaultdict(list)
    for doc_id in dict.fromkeys(row["document_id"] for row in report["rows"]):
        entry = manifest[doc_id]
        path = root / "data/raw" / entry["filename"]
        verify_source(path, entry, [g for g in gold if g["document_id"] == doc_id])
        log.info("Rescore recorded predictions: %s", doc_id)
        candidates = parse_pdf(path, doc_id)
        for row in (r for r in report["rows"] if r["document_id"] == doc_id):
            label = labels.get((doc_id, row["metric_id"]))
            verified = bool(label and label.get("annotation_status") == "verified" and label.get("value") is not None)
            row.update(gold=label, gold_verified=verified,
                       scores=score_fact(row["prediction"], label, candidates) if verified else None)
            grouped[row["split"]].append(row)
    report.update(scorer_version=SCORER_VERSION, rescored_at=datetime.now(UTC).isoformat(),
                  source_hashes_verified=True, summary=summarize(report["rows"]),
                  by_split={k: summarize(v) for k, v in grouped.items()})
    report["notes"].append("Scores were recomputed from recorded predictions; original model outputs and execution timings were preserved. The scorer checks restatement and source precision.")
    atomic_json(report_path, report)
    write_markdown(report, report_path.with_suffix(".md"))
    return report


def run_benchmark(root: Path, method: str, output: Path, ids: list[str] | None = None,
                  model_path: str | None = None) -> dict:
    from .extraction import METRICS, RULES_VERSION, extract_rules
    from .parser import PARSER_VERSION, parse_pdf

    manifest = json.loads((root / "data/manifest.json").read_text(encoding="utf-8"))
    selected_documents = [r for r in manifest if not ids or r["id"] in ids]
    if ids and set(ids) - {r["id"] for r in manifest}:
        raise ValueError("Requested unknown manifest ID")
    gold_path = root / "data/gold.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else []
    labels = {(r["document_id"], r["metric_id"]): r for r in gold}
    rows, runs = [], []
    output.mkdir(parents=True, exist_ok=True)
    for item in selected_documents:
        path = root / "data/raw" / item["filename"]
        if not path.exists():
            log.warning("Missing report: %s", path)
            runs.append({"document_id": item["id"], "error": "PDF not downloaded"})
            continue
        started = time.perf_counter()
        log.info("Benchmark %s %s", method, item["id"])
        digest = verify_source(path, item, [g for g in gold if g["document_id"] == item["id"]])
        candidates = parse_pdf(path, item["id"])
        parse_seconds = time.perf_counter() - started
        model_info = None
        if method == "qwen":
            from .model import DEFAULT_MODEL, LAST_STATS, extract_qwen

            predictions = extract_qwen(candidates, item, model_path or os.environ.get("DISCLOSURE_MODEL_PATH", DEFAULT_MODEL))
            model_info = dict(LAST_STATS)
        else:
            predictions = extract_rules(candidates, item)
        facts_by_id = {f["metric_id"]: f for f in predictions}
        for metric_id in METRICS:
            prediction = facts_by_id.get(metric_id, {})
            label = labels.get((item["id"], metric_id))
            verified = bool(label and label.get("annotation_status") == "verified" and label.get("value") is not None)
            evidence = next((c for c in candidates if c["id"] == prediction.get("candidate_id")), None)
            # Commit only the short cited row, not page-long source extracts.
            if evidence:
                evidence = {k: evidence[k] for k in ("id", "page", "bbox", "page_width", "page_height", "text")}
            row = {"document_id": item["id"], "split": item["split"], "metric_id": metric_id,
                   "gold_verified": verified, "gold": label, "prediction": prediction,
                   "evidence": evidence, "scores": score_fact(prediction, label, candidates) if verified else None}
            rows.append(row)
        runs.append({"document_id": item["id"], "split": item["split"], "source_url": item["url"],
                     "sha256": digest,
                     "candidate_count": len(candidates), "parse_seconds": round(parse_seconds, 3),
                     "elapsed_seconds": round(time.perf_counter() - started, 3), "model": model_info})
        # Incremental checkpoint, preserving an expensive real run if later work fails.
        atomic_json(output / f"{method}_checkpoint.json", {"rows": rows, "runs": runs})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["split"]].append(row)
    report = {"generated_at": datetime.now(UTC).isoformat(), "method": method, "scorer_version": SCORER_VERSION,
              "source_hashes_verified": True,
              "parser_version": PARSER_VERSION, "rules_version": RULES_VERSION,
              "requested_documents": len(selected_documents), "requested_fields": len(selected_documents) * len(METRICS),
              "missing_documents": [r["document_id"] for r in runs if "error" in r],
              "source_label_method": "AI-assisted direct source verification; no independent human study",
              "notes": ["All extracted values require manual review; this benchmark scores draft extraction.",
                        "Fields within reports are dependent; counts are pilot observations, not population guarantees.",
                        "Evidence existence does not establish correct column, period or regulatory scope.",
                        "Complete-tuple scoring additionally requires a labelled source page; correct alternative citations may be undercounted.",
                        "Timing includes fresh PDF parsing. Model calls are uncached. Initial model load is included in first document."],
              "summary": summarize(rows), "by_split": {k: summarize(v) for k, v in grouped.items()},
              "runs": runs, "rows": rows}
    atomic_json(output / f"{method}.json", report)
    write_markdown(report, output / f"{method}.md")
    print(json.dumps(report["summary"], indent=2))
    return report


def write_markdown(report: dict, path: Path) -> None:
    lines = [f"# {report['method'].upper()} — real-report pilot", "", f"Generated: {report['generated_at']}", "",
             "Source labels were checked with AI assistance; an independent human audit is still needed.",
             "These are unreviewed drafts, never automatically approved financial facts.", "",
             "| Split | Source-checked labels | Correct value + unit | Correct complete tuple | Wrong nonempty drafts |",
             "|---|---:|---:|---:|---:|"]
    for split, scores in report["by_split"].items():
        lines.append(f"| {split} | {scores['source_verified_labels']} | {scores['value_correct']}/{scores['source_verified_labels']} | {scores['complete_fact_correct']}/{scores['context_labelled']} | {scores['incorrect_nonempty_predictions']} |")
    lines += ["", "## Limits", ""] + [f"- {note}" for note in report["notes"]]
    lines += ["", "## Per-document execution", "", "| Report | Candidate rows | Parsing seconds | Total seconds |", "|---|---:|---:|---:|"]
    for run in report["runs"]:
        if "error" in run:
            lines.append(f"| {run['document_id']} | missing PDF | — | — |")
        else:
            lines.append(f"| {run['document_id']} | {run['candidate_count']} | {run['parse_seconds']} | {run['elapsed_seconds']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
