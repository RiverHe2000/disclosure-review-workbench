"""Record and verify a real review-to-export workflow in an isolated database copy.

Run from any directory with the project's Python environment:
    python scripts/demo_ui.py

Requires the production frontend build and Playwright Chromium. The source store
is opened read-only. Only output/demo_workbench receives scripted review writes.
The resulting recording is a scripted demonstration, not user research.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "output"
METRICS = {"total_capital_ratio": "Total capital", "lcr": "LCR", "nsfr": "NSFR"}
REASON = "Scripted demonstration, not user research"


def readonly_db(directory: Path) -> sqlite3.Connection:
    return sqlite3.connect((directory / "workbench.sqlite3").as_uri() + "?mode=ro", uri=True)


def review_fingerprint(directory: Path) -> dict:
    with readonly_db(directory) as database:
        rows = database.execute("SELECT id,fact_id,version,created_at,payload FROM reviews ORDER BY id").fetchall()
    return {"count": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


def copy_store(source: Path, destination: Path) -> None:
    source, destination = source.resolve(), destination.resolve()
    if source == destination or not destination.is_relative_to(OUTPUT.resolve()):
        raise ValueError("The demonstration must run in a separate directory under the project's output folder.")
    destination.mkdir(parents=True, exist_ok=True)
    with readonly_db(source) as original, sqlite3.connect(destination / "workbench.sqlite3") as duplicate:
        original.backup(duplicate)
    pdf_directory = destination / "documents"
    pdf_directory.mkdir(exist_ok=True)
    for pdf in (source / "documents").glob("*.pdf"):
        linked = pdf_directory / pdf.name
        if linked.exists():
            if linked.stat().st_size != pdf.stat().st_size:
                raise ValueError(f"Existing demonstration PDF differs from source: {linked.name}")
            continue
        try:
            linked.hardlink_to(pdf)
        except OSError:
            shutil.copy2(pdf, linked)


def get_json(url: str):
    with urlopen(url, timeout=30) as response:
        return json.load(response)


def select_source(candidates: list[dict], gold: dict) -> dict:
    raw_value = str(gold.get("source_raw_value", gold["value"]))
    expected_pages = gold.get("evidence_pages", [gold["evidence_page"]])
    matches = [
        candidate for candidate in candidates
        if candidate["page"] in expected_pages
        and gold["metric_id"] in candidate.get("metric_ids", [])
        and raw_value in candidate["text"]
    ]
    if not matches:
        raise AssertionError(f"No source candidate independently matches {gold['document_id']} {gold['metric_id']}.")
    quote = re.sub(r"\s+", " ", gold["evidence_quote"]).casefold()
    matches.sort(key=lambda candidate: (quote not in re.sub(r"\s+", " ", candidate["text"]).casefold(), len(candidate["text"])))
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=PROJECT / "data/workbench")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--pace-ms", type=int, default=1600, help="Short reading pause between real UI actions.")
    args = parser.parse_args()
    source = args.source_dir.resolve()
    target = (OUTPUT / "demo_workbench").resolve()
    OUTPUT.mkdir(exist_ok=True)
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", args.port))
        except OSError as error:
            raise RuntimeError(f"Port {args.port} is already occupied; refusing to attach to another server.") from error
    original_reviews = review_fingerprint(source)
    copy_store(source, target)
    gold_rows = json.loads((PROJECT / "data/gold.json").read_text(encoding="utf-8"))
    gold = {(row["document_id"], row["metric_id"]): row for row in gold_rows}
    base_url = f"http://127.0.0.1:{args.port}"
    environment = {**os.environ, "DISCLOSURE_DATA_DIR": str(target), "PYTHONPATH": str(PROJECT / "src")}
    commands = [sys.executable, "-m", "uvicorn", "disclosure.api:create_app", "--factory", "--host", "127.0.0.1", "--port", str(args.port)]
    checks: list[str] = []
    errors: list[str] = []
    saved_reviews: list[dict] = []
    recording = None
    with (OUTPUT / "demo-server.log").open("w", encoding="utf-8") as logfile:
        server = subprocess.Popen(commands, cwd=PROJECT, env=environment, stdout=logfile, stderr=logfile,
                                  creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            for _ in range(80):
                if server.poll() is not None:
                    raise RuntimeError("Isolated API exited; inspect output/demo-server.log.")
                try:
                    if get_json(base_url + "/api/health").get("status") == "ok":
                        break
                except OSError:
                    time.sleep(0.15)
            else:
                raise RuntimeError("Isolated API did not become ready.")

            documents = get_json(base_url + "/api/documents")
            selected_documents = {year: next(doc for doc in documents if doc["bank"] == "CBA" and doc["year"] == year) for year in (2023, 2024)}
            evidence = {}
            for year, document in selected_documents.items():
                candidates = get_json(f"{base_url}/api/documents/{document['id']}/candidates")
                for metric_id in METRICS:
                    annotation = gold[(f"cba_{year}", metric_id)]
                    assert annotation["annotation_status"] == "verified"
                    assert annotation["source_pdf_sha256"] == document["sha256"]
                    evidence[(year, metric_id)] = select_source(candidates, annotation)
            checks.append("All six review values use verified, source-review-assisted gold with a matching PDF hash, metric, physical page and source number")
            print("Isolated copy is ready. Recording six real review actions.", flush=True)

            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, slow_mo=150)
                context = browser.new_context(viewport={"width": 1440, "height": 1100},
                                              record_video_dir=str(OUTPUT / "demo-recordings"),
                                              record_video_size={"width": 1440, "height": 1100},
                                              accept_downloads=True)
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                video = page.video
                started = time.monotonic()
                page.goto(base_url, wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="Every figure. In context.")).to_be_visible()
                expect(page.locator(".summary-note")).to_have_text("Source-linked. Reviewer-controlled.")
                page.wait_for_timeout(args.pace_ms * 2)
                page.get_by_label("Filter reports by bank").select_option("CBA")
                page.wait_for_timeout(args.pace_ms)

                for year, document in selected_documents.items():
                    page.locator(".document-card").filter(has_text=document["filename"]).click()
                    expect(page.locator(".review-detail")).to_be_visible()
                    page.wait_for_timeout(args.pace_ms)
                    for metric_id, label in METRICS.items():
                        annotation = gold[(f"cba_{year}", metric_id)]
                        candidate = evidence[(year, metric_id)]
                        page.locator(".metric-row").filter(has_text=label).click()
                        expect(page.locator(".review-detail .context-warning strong")).to_have_text("Context needs attention")
                        page.get_by_label("Source evidence", exact=True).select_option(candidate["id"])
                        expect(page.get_by_label("PDF page number")).to_have_value(str(candidate["page"]))
                        expect(page.locator(".evidence-highlight")).to_be_visible(timeout=30_000)
                        expect(page.locator(".pdf-loading")).to_have_count(0, timeout=30_000)
                        page.wait_for_timeout(args.pace_ms)
                        detail = page.locator(".review-detail")
                        detail.get_by_label(re.compile(r"^Value")).fill(str(annotation["value"]))
                        detail.get_by_label("Reporting date", exact=True).fill(annotation["period"])
                        detail.get_by_label("Entity scope", exact=True).fill(annotation["entity_scope"])
                        detail.get_by_label("Reporting basis", exact=True).fill(annotation["basis"])
                        detail.locator("label").filter(has_text=re.compile(r"^Observation")).locator("select").select_option(annotation["observation"])
                        detail.locator("textarea").fill(f"{REASON}. Source-checked against CBA {year}, physical PDF page {candidate['page']}; {annotation['annotation_by']} annotation.")
                        page.wait_for_timeout(args.pace_ms)
                        with page.expect_response(lambda response: "/api/facts/" in response.url and response.url.endswith("/review") and response.request.method == "POST") as response_info:
                            detail.get_by_role("button", name=re.compile(r"^(Save & accept|Accept figure)$")).click()
                        response = response_info.value
                        assert response.ok, response.text()
                        saved = response.json()
                        assert saved["status"] == "accepted"
                        assert saved["candidate_id"] == candidate["id"]
                        assert saved["evidence"]["page"] == candidate["page"]
                        assert saved["period"] == annotation["period"]
                        assert math.isclose(saved["value"], annotation["value"], abs_tol=1e-9)
                        assert saved["review"]["reason"].startswith(REASON)
                        expect(detail.locator(".status-badge")).to_have_text("accepted")
                        expect(detail.locator(".saved-review p")).to_contain_text(REASON)
                        expect(detail.get_by_text("Context needs attention", exact=True)).to_have_count(0)
                        original_notes = detail.locator(".reviewed-notes")
                        assert original_notes.get_attribute("open") is None
                        expect(original_notes.locator("summary")).to_have_text("Original extraction notes (reviewed)")
                        original_notes.locator("summary").click()
                        expect(original_notes.locator("li").first).to_be_visible()
                        original_notes.locator("summary").click()
                        assert original_notes.get_attribute("open") is None
                        saved_reviews.append({"bank": "CBA", "year": year, "metric_id": metric_id, "value": saved["value"], "fact_id": saved["id"], "version": saved["version"], "candidate_id": candidate["id"], "evidence_page": candidate["page"]})
                        print(f"Accepted CBA {year} {metric_id} through the browser.", flush=True)
                        page.wait_for_timeout(args.pace_ms)
                    if year == 2024:
                        page.locator(".metric-row").filter(has_text="LCR").click()
                        page.wait_for_timeout(args.pace_ms)
                        page.keyboard.press("Control+Home")
                        page.screenshot(path=str(OUTPUT / "demo-review-accepted.png"), full_page=True)
                checks.append("Six real versioned review POSTs accepted matching evidence and persisted explicit scripted-demonstration reasons in the isolated store")
                checks.append("Pending warnings remain visible; accepted reviews show their saved reason and collapsed historical extraction notes, with reviewer-controlled wording")

                page.get_by_role("button", name="Period comparison", exact=False).click()
                expect(page.locator(".comparison-table tbody tr")).to_have_count(5)
                baseline = page.locator(".comparison-selectors select").nth(0)
                following = page.locator(".comparison-selectors select").nth(1)
                baseline.select_option(selected_documents[2023]["id"])
                following.select_option(selected_documents[2024]["id"])
                expected_deltas = {"total_capital_ratio": 0.9, "lcr": 5.0, "nsfr": -8.0}
                comparison = get_json(f"{base_url}/api/compare?left={selected_documents[2023]['id']}&right={selected_documents[2024]['id']}")
                for row in comparison["rows"]:
                    if row["metric_id"] in expected_deltas:
                        assert row["comparable"] is True, row["reasons"]
                        assert row["delta_unit"] == "percentage_points"
                        assert row["relative_change"] is None
                        assert math.isclose(row["delta"], expected_deltas[row["metric_id"]], abs_tol=1e-9)
                    else:
                        assert row["comparable"] is False
                        assert row["delta"] is None
                expect(page.locator(".delta-value")).to_have_count(3)
                expect(page.locator(".delta-value").nth(0)).to_have_text("+0.9 pp")
                expect(page.locator(".delta-value").nth(1)).to_have_text("+5 pp")
                expect(page.locator(".delta-value").nth(2)).to_have_text("-8 pp")
                expect(page.locator(".not-comparable")).to_have_count(2)
                page.wait_for_timeout(args.pace_ms * 3)
                page.locator(".delta-value").last.scroll_into_view_if_needed()
                expect(page.locator(".delta-value").last).to_be_in_viewport()
                page.wait_for_timeout(args.pace_ms * 2)
                page.screenshot(path=str(OUTPUT / "demo-comparison-accepted.png"), full_page=True)
                checks.append("Browser and API both show +0.9 pp total capital, +5 pp LCR and -8 pp NSFR; two unreviewed rows remain blocked")

                with page.expect_download() as download_info:
                    page.get_by_role("link", name="CSV", exact=True).click()
                csv_path = OUTPUT / "demo-comparison.csv"
                download_info.value.save_as(csv_path)
                with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
                    csv_rows = list(csv.DictReader(csv_file))
                assert len(csv_rows) == 5
                csv_text = csv_path.read_text(encoding="utf-8-sig")
                assert REASON in csv_text
                assert "percentage_points" in csv_text
                csv_by_label = {row["metric"]: row for row in csv_rows}
                for comparison_row in comparison["rows"]:
                    csv_row = csv_by_label[comparison_row["label"]]
                    metric_id = comparison_row["metric_id"]
                    if metric_id in expected_deltas:
                        assert csv_row["comparable"] == "True"
                        assert math.isclose(float(csv_row["delta"]), expected_deltas[metric_id], abs_tol=1e-9)
                        for side, year in (("left", 2023), ("right", 2024)):
                            assert csv_row[f"{side}_status"] == "accepted"
                            assert csv_row[f"{side}_review_reason"].startswith(REASON)
                            assert int(csv_row[f"{side}_evidence_page"]) == evidence[(year, metric_id)]["page"]
                    else:
                        assert csv_row["comparable"] == "False" and not csv_row["delta"]
                page.wait_for_timeout(args.pace_ms * 2)
                with page.expect_download() as markdown_info:
                    page.get_by_role("link", name="Markdown", exact=True).click()
                markdown_path = OUTPUT / "demo-comparison.md"
                markdown_info.value.save_as(markdown_path)
                assert REASON in markdown_path.read_text(encoding="utf-8")
                page.wait_for_timeout(args.pace_ms * 2)
                checks.append("Real browser CSV and Markdown downloads retain all five metrics, percentage-point units and the scripted review reason")
                assert not errors, errors
                elapsed = time.monotonic() - started
                context.close()
                assert video is not None
                recording = OUTPUT / "disclosure-desk-demo.webm"
                video.save_as(recording)
                browser.close()

            current_reviews = review_fingerprint(source)
            assert current_reviews == original_reviews, "The original review history changed during the demonstration."
            checks.append("Original data/workbench review history hash and count are unchanged")
            report = {"kind": "Scripted demonstration, not user research", "source_store": str(source), "demo_store": str(target),
                      "production_review_mutations": 0, "source_reviews_before": original_reviews,
                      "source_reviews_after": current_reviews, "demo_reviews": saved_reviews,
                      "checks_passed": checks, "browser_errors": errors, "duration_seconds": round(elapsed, 2),
                      "recording": str(recording), "recording_bytes": recording.stat().st_size,
                      "gold_annotation_method": "source_review_assisted; not independent human double review"}
            (OUTPUT / "demo-e2e-result.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps(report, indent=2), flush=True)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


if __name__ == "__main__":
    main()
