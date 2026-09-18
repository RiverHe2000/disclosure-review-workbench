"""Transparent, conservative numeric baseline. Every result needs human review."""

from __future__ import annotations

import re
from datetime import date

from disclosure.parser import METRIC_PATTERNS, NUMBER_PATTERN

RULES_VERSION = "native-rules-v1"
METRICS = {
    "cet1_ratio": {"label": "CET1 capital ratio", "unit": "percent"},
    "total_capital_ratio": {"label": "Total capital ratio", "unit": "percent"},
    "rwa": {"label": "Risk-weighted assets", "unit": "AUD_million"},
    "lcr": {"label": "Liquidity coverage ratio", "unit": "percent"},
    "nsfr": {"label": "Net stable funding ratio", "unit": "percent"},
}
MONTHS = {"january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
          "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12}
DATE_RE = re.compile(r"\b(\d{1,2})\s+(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
                     r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
                     r"\s+(20\d{2}|\d{2})\b", re.I)


def missing_fact(metric_id: str, document: dict, method: str = "rules") -> dict:
    return {"document_id": document["id"], "metric_id": metric_id, "value": None,
            "unit": METRICS[metric_id]["unit"], "period": None, "entity_scope": "unknown",
            "basis": "unknown", "observation": "unknown", "restated": False,
            "candidate_id": None, "status": "missing", "issues": ["No unambiguous numeric candidate"],
            "method": method}


def infer_context(candidate: dict, document: dict, metric_id: str) -> dict:
    """Read context explicitly present on the page; never infer from bank/year alone."""
    text = candidate["context"]
    lower = text.lower()
    dates = []
    for day, month, year in DATE_RE.findall(text):
        full_year = int(year) if len(year) == 4 else 2000 + int(year)
        month_number = next(value for name, value in MONTHS.items() if name.startswith(month.lower()[:3]))
        try:
            parsed = date(full_year, month_number, int(day)).isoformat()
        except ValueError:
            continue
        if full_year == int(document["year"]):
            dates.append(parsed)
    unique_dates = sorted(set(dates))
    period = unique_dates[0] if len(unique_dates) == 1 else None
    capital = metric_id in {"cet1_ratio", "total_capital_ratio", "rwa"}
    has_l1 = bool(re.search(r"level\s*1\b", lower))
    has_l2 = bool(re.search(r"level\s*2\b", lower))
    scope = "Group" if has_l2 and not has_l1 else "unknown"
    if not capital and re.search(r"\bgroup(?:'s|’s)?\b", lower):
        scope = "Group"
    apra = "apra" in lower
    international = bool(re.search(r"international(?:ly)?\s+(?:comparable|harmonised|harmonized)", lower))
    basis = "APRA Level 2" if capital and apra and has_l2 and not has_l1 and not international else "unknown"
    if not capital and scope == "Group":
        basis = "reported"
    observation = "unknown"
    if metric_id == "lcr":
        if re.search(r"average.{0,90}quarter|quarter.{0,90}average", lower, re.S):
            observation = "quarter_average"
        elif re.search(r"average.{0,60}(?:year|twelve months)|(?:year|twelve months).{0,60}average", lower, re.S):
            observation = "year_average"
    elif period and re.search(r"\bas\s+(?:at|of)\b|\bat\s+\d{1,2}\s+(?:june|september)\b", lower):
        observation = "point_in_time"
    return {"period": period, "entity_scope": scope, "basis": basis, "observation": observation,
            "restated": bool(re.search(r"\brestat(?:ed|ement)\b", lower))}


def _numeric_value(candidate: dict, metric_id: str, document: dict) -> tuple[float | None, list[str]]:
    text = candidate["text"]
    match = METRIC_PATTERNS[metric_id].search(text)
    if not match:
        return None, []
    tail = text[match.end():]
    # A second metric on the same line is ambiguous, not a licence to pick its number.
    for other_id, pattern in METRIC_PATTERNS.items():
        if other_id != metric_id:
            other = pattern.search(tail)
            if other:
                tail = tail[:other.start()]
    tail = DATE_RE.sub(" ", tail)
    tail = re.sub(r"\b\d+\s*[-–]?\s*(?:day|month|year)s?\b", " ", tail, flags=re.I)
    tail = re.sub(r"(?:at least|greater than|minimum(?: of)?|above)\s+\d+(?:\.\d+)?%", " ", tail, flags=re.I)
    tokens = NUMBER_PATTERN.findall(tail)
    values = []
    for token in tokens:
        number = float(token.replace(",", "").rstrip("%"))
        if 1900 <= number <= 2100 and "," not in token and "." not in token:
            continue
        if metric_id == "rwa":
            # Reject percentage references and small footnote markers.
            if token.endswith("%") or number < 100:
                continue
        elif not (0 < number <= (300 if metric_id in {"lcr", "nsfr"} else 40)):
            continue
        elif number < 5 and "." not in token and not token.endswith("%"):
            continue
        values.append((number, token))
    if not values:
        return None, []
    issues = []
    if len(values) > 1:
        # Financial statements usually display the current period first, but this is
        # only a proposed draft. Preserve the ambiguity instead of claiming certainty.
        issues.append("Multiple numbers in evidence; verify current-period column")
    value = values[0][0]
    if metric_id == "rwa":
        context = candidate["context"].lower()
        if re.search(r"\$\s*b(?:n|illion)?\b|\bbillion\b", text.lower()):
            value *= 1000
        elif not re.search(r"\$\s*m(?:illion)?\b|\bmillion\b|\ba\$m\b", context):
            issues.append("Source currency/scale unresolved; verify AUD millions")
    if str(document["year"]) not in candidate["context"] and str(document["year"])[2:] not in candidate["context"]:
        issues.append("Current reporting year not established in page-local context")
    return value, issues


def extract_rules(candidates: list[dict], document: dict) -> list[dict]:
    facts = []
    for metric_id in METRICS:
        options = []
        for candidate in rank_candidates(candidates, metric_id, document):
            value, issues = _numeric_value(candidate, metric_id, document)
            if value is None:
                continue
            context = infer_context(candidate, document, metric_id)
            score = _rank_score(candidate, metric_id, document)
            options.append((score, candidate, value, issues, context))
        if not options:
            facts.append(missing_fact(metric_id, document))
            continue
        options.sort(key=lambda item: (-item[0], item[1]["page"], item[1]["bbox"][1]))
        score, candidate, value, issues, context = options[0]
        if score < 1:
            facts.append(missing_fact(metric_id, document))
            continue
        issues = list(issues)
        for key in ("period", "entity_scope", "basis", "observation"):
            if context[key] is None or context[key] == "unknown":
                issues.append(f"Unresolved {key.replace('_', ' ')}")
        if context["restated"]:
            issues.append("Page mentions restatement; reviewer must resolve applicability")
        tied = [item for item in options if item[0] == score and item[2] != value]
        if tied:
            issues.append("Equally ranked evidence has conflicting values")
        facts.append({**missing_fact(metric_id, document), **context, "value": value,
                      "candidate_id": candidate["id"], "status": "pending",
                      "issues": issues + ["Rule-generated draft; human review required"]})
    return facts


def _rank_score(candidate: dict, metric_id: str, document: dict) -> int:
    context = infer_context(candidate, document, metric_id)
    text = candidate["text"].lower()
    score = 0
    if "ratio" in text and metric_id != "rwa":
        score += 3
    if re.match(r"\s*(?:total\s+)?(?:common equity|cet1|risk.weighted assets|liquidity coverage|net stable funding|total capital ratio)", text):
        score += 3
    if "%" in text and metric_id != "rwa":
        score += 1
    if context["period"]:
        score += 3
    if context["entity_scope"] != "unknown":
        score += 2
    if context["basis"] != "unknown":
        score += 2
    if _numeric_value(candidate, metric_id, document)[0] is not None:
        score += 4
    if re.search(r"minimum|require[dm]|requirement|target|floor|buffer|increas|decreas|movement|change|at least|greater than", text):
        score -= 8
    if len(candidate["metric_ids"]) > 1:
        score -= 2
    return score


def rank_candidates(candidates: list[dict], metric_id: str, document: dict, limit: int = 6) -> list[dict]:
    """Shared source-only ranking for rule and model paths; never reads gold labels."""
    eligible = [candidate for candidate in candidates if metric_id in candidate["metric_ids"]]
    return sorted(eligible, key=lambda candidate: (-_rank_score(candidate, metric_id, document),
                                                  candidate["page"], candidate["bbox"][1],
                                                  len(candidate["text"])))[:limit]
