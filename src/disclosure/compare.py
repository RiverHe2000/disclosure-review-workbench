"""Conservative comparisons and portable, evidence-bearing review exports."""

from __future__ import annotations

import csv
import io
import math
from datetime import date

from disclosure.store import Store

LABELS = {
    "cet1_ratio": "CET1 capital ratio", "total_capital_ratio": "Total capital ratio",
    "rwa": "Risk-weighted assets", "lcr": "Liquidity coverage ratio", "nsfr": "Net stable funding ratio",
}


def normalize(value: str | None) -> str:
    return " ".join((value or "").strip().casefold().split())


def compare_documents(store: Store, left_id: str, right_id: str) -> dict:
    left, right = store.document(left_id), store.document(right_id)
    left_facts = {f["metric_id"]: f for f in store.facts(left_id)}
    right_facts = {f["metric_id"]: f for f in store.facts(right_id)}
    rows = []
    for metric_id, label in LABELS.items():
        a, b = left_facts.get(metric_id), right_facts.get(metric_id)
        reasons = []
        if left["bank"] != right["bank"]:
            reasons.append("Different banks; cross-bank changes are not comparable.")
        if left_id == right_id:
            reasons.append("Select two different reporting periods.")
        if any(document["year"] not in {2023, 2024, 2025} for document in (left, right)):
            reasons.append("Fiscal years outside the validated 2023–2025 range require additional framework confirmation; automatic comparison is blocked.")
        if metric_id in {"cet1_ratio", "total_capital_ratio", "rwa"} and min(left["year"], right["year"]) <= 2022 < max(left["year"], right["year"]):
            reasons.append("This comparison crosses the 2022–2023 capital framework boundary; a separately verified like-for-like restatement is required.")
        for side, fact, document in (("Left", a, left), ("Right", b, right)):
            if fact is None:
                reasons.append(f"{side}: no completed extraction.")
                continue
            if fact.get("status") != "accepted":
                reasons.append(f"{side}: result has not been accepted by a reviewer ({fact.get('status', 'unknown')}).")
            if not fact.get("evidence"):
                reasons.append(f"{side}: missing source evidence.")
            value = fact.get("value")
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                reasons.append(f"{side}: missing or invalid numeric value.")
            if fact.get("restated") and not fact.get("restatement_resolved"):
                reasons.append(f"{side}: restatement requires explicit human resolution.")
            for key in ("entity_scope", "basis", "observation"):
                if normalize(fact.get(key)) in {"", "unknown", "unspecified", "n/a", "none", "null"}:
                    reasons.append(f"{side}: unresolved {key}.")
            if metric_id in {"cet1_ratio", "total_capital_ratio", "rwa"} and normalize(fact.get("basis")) != "apra level 2":
                reasons.append(f"{side}: capital comparison requires the APRA Level 2 regulatory basis.")
            try:
                period = date.fromisoformat(fact.get("period") or "")
                if period.year != document["year"]:
                    reasons.append(f"{side}: reporting date does not match document fiscal year.")
            except (ValueError, TypeError):
                reasons.append(f"{side}: explicit reporting date is missing or invalid.")
        if a and b:
            for key, description in (("unit", "units"), ("entity_scope", "entity scope"),
                                     ("basis", "regulatory basis"), ("observation", "observation basis")):
                if normalize(a.get(key)) != normalize(b.get(key)):
                    reasons.append(f"Different {description}.")
            try:
                pa, pb = date.fromisoformat(a.get("period") or ""), date.fromisoformat(b.get("period") or "")
                if (pa.month, pa.day) != (pb.month, pb.day):
                    reasons.append("Reporting dates have different fiscal date patterns.")
                if pa == pb:
                    reasons.append("Reporting periods are identical.")
            except (ValueError, TypeError):
                pass
        comparable = not reasons
        delta = float(b["value"] - a["value"]) if comparable else None
        relative = delta / a["value"] if comparable and metric_id == "rwa" and a["value"] != 0 else None
        rows.append(dict(metric_id=metric_id, label=label, left=a, right=b, comparable=comparable,
                         delta=delta, relative_change=relative,
                         unit="AUD_million" if metric_id == "rwa" else "percent",
                         delta_unit="AUD_million" if metric_id == "rwa" else "percentage_points", reasons=reasons))
    return dict(left=left, right=right, rows=rows)


def cell(value: object) -> str:
    """Prevent untrusted spreadsheet text being interpreted as an executable formula."""
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + text
    return text


def export_rows(comparison: dict) -> list[dict]:
    result = []
    for row in comparison["rows"]:
        item = {"metric": row["label"], "comparable": row["comparable"], "delta": row["delta"],
                "delta_unit": row["delta_unit"], "relative_change_fraction": row["relative_change"],
                "blocking_reasons": "; ".join(row["reasons"])}
        for side in ("left", "right"):
            doc, fact = comparison[side], row[side] or {}
            evidence = fact.get("evidence") or {}
            review = fact.get("review") or {}
            item.update({f"{side}_{key}": value for key, value in {
                "bank": doc["bank"], "year": doc["year"], "filename": doc["filename"], "sha256": doc["sha256"],
                "source_url": doc["source_url"], "value": fact.get("value"), "unit": fact.get("unit"),
                "period": fact.get("period"), "entity_scope": fact.get("entity_scope"), "basis": fact.get("basis"),
                "observation": fact.get("observation"), "status": fact.get("status", "missing"),
                "method": fact.get("method"), "run_id": fact.get("run_id"), "review_version": fact.get("version"),
                "review_reason": review.get("reason"), "restated": fact.get("restated"),
                "restatement_resolved": fact.get("restatement_resolved", False),
                "evidence_page": evidence.get("page"), "evidence_quote": evidence.get("text"),
                "evidence_context": evidence.get("context"),
            }.items()})
        result.append(item)
    return result


def export_csv(comparison: dict) -> str:
    rows = export_rows(comparison)
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    for row in rows:
        writer.writerow({key: cell(value) if isinstance(value, str) else value for key, value in row.items()})
    return out.getvalue()


def md(value: object) -> str:
    return ("" if value is None else str(value)).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def export_markdown(comparison: dict) -> str:
    lines = ["# Disclosure review comparison", "", "Changes are right minus left. Ratio changes are percentage points; "
             "RWA relative change is a fraction (multiply by 100 for percent). Only accepted, compatible results are compared.", ""]
    for side in ("left", "right"):
        doc = comparison[side]
        lines.extend([f"## {side.title()}: {md(doc['bank'])} {doc['year']}", "",
                      f"File: {md(doc['filename'])}; SHA-256: `{doc['sha256']}`",
                      f"Source: {md(doc['source_url']) or '(uploaded locally)'}", ""])
    for row in comparison["rows"]:
        lines.extend([f"## {row['label']}", "",
                      f"Comparable: {'yes' if row['comparable'] else 'no'}; change: {row['delta'] if row['delta'] is not None else 'blocked'} {row['delta_unit']}",
                      f"RWA relative change: {row['relative_change'] if row['relative_change'] is not None else 'not applicable'}", ""])
        if row["reasons"]:
            lines.extend(["Blocking reasons: " + "; ".join(map(md, row["reasons"])), ""])
        for side in ("left", "right"):
            fact = row[side]
            if not fact:
                lines.extend([f"**{side.title()}**: no completed extraction.", ""])
                continue
            evidence, review = fact.get("evidence") or {}, fact.get("review") or {}
            lines.extend([
                f"**{side.title()}**: {md(fact.get('value'))} {md(fact.get('unit'))}; {md(fact.get('status'))}; review v{fact.get('version', 0)}",
                f"Period: {md(fact.get('period'))}; entity: {md(fact.get('entity_scope'))}; basis: {md(fact.get('basis'))}; observation: {md(fact.get('observation'))}",
                f"Restated: {fact.get('restated', False)}; restatement resolved: {fact.get('restatement_resolved', False)}; method: {md(fact.get('method'))}; run: {md(fact.get('run_id'))}",
                f"Evidence: physical PDF page {md(evidence.get('page'))}: {md(evidence.get('text'))}",
                f"Context: {md(evidence.get('context'))}", f"Review reason: {md(review.get('reason'))}", "",
            ])
    return "\n".join(lines)
