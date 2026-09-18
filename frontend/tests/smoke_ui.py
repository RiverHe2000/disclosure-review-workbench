"""Read-only browser smoke check against a populated local workbench.

Run after starting the Python service and Vite:
    python frontend/tests/smoke_ui.py

No review, extraction, upload, or production data changes are submitted. The 409
workflow intercepts the review POST within the isolated browser context.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright

BASE_URL = os.environ.get("DISCLOSURE_UI_URL", "http://127.0.0.1:5173")
API_URL = os.environ.get("DISCLOSURE_API_URL", "http://127.0.0.1:8000")
OUTPUT = Path(__file__).resolve().parents[2] / "output"


def read_json(path: str):
    with urlopen(API_URL + path, timeout=30) as response:
        return json.load(response)


def main() -> None:
    OUTPUT.mkdir(exist_ok=True)
    documents = read_json("/api/documents")
    document = next(doc for doc in documents if doc["bank"] == "CBA" and doc["year"] == 2023)
    facts = read_json(f"/api/documents/{document['id']}/facts")
    fact = next(item for item in facts if item["metric_id"] == "cet1_ratio" and item.get("evidence"))
    candidates = read_json(f"/api/documents/{document['id']}/candidates")
    errors: list[str] = []
    checks: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE_URL, wait_until="domcontentloaded")
        expect(page.get_by_role("heading", name="Every figure. In context.")).to_be_visible()
        expect(page.locator(".document-card")).to_have_count(len(documents))
        page.locator(".document-card").filter(has_text=document["filename"]).click()
        expect(page.locator(".evidence-highlight")).to_be_visible(timeout=45_000)
        expect(page.get_by_label("PDF page number")).to_have_value(str(fact["evidence"]["page"]))
        checks.append("Real PDF loads with the selected fact's physical evidence page")

        def check_coordinates(evidence: dict) -> None:
            paper = page.locator(".pdf-page").bounding_box()
            overlay = page.locator(".evidence-highlight").bounding_box()
            assert paper is not None and overlay is not None
            expected_x = paper["x"] + evidence["bbox"][0] / evidence["page_width"] * paper["width"]
            expected_y = paper["y"] + evidence["bbox"][1] / evidence["page_height"] * paper["height"]
            assert abs(overlay["x"] - expected_x) < 1.1, (overlay["x"], expected_x)
            assert abs(overlay["y"] - expected_y) < 1.1, (overlay["y"], expected_y)

        check_coordinates(fact["evidence"])
        page.get_by_label("PDF zoom").select_option("1.5")
        expect(page.locator(".pdf-page")).not_to_have_class("pdf-page is-loading", timeout=30_000)
        check_coordinates(fact["evidence"])
        page.get_by_label("PDF zoom").select_option("1")
        expect(page.locator(".pdf-page")).not_to_have_class("pdf-page is-loading", timeout=30_000)
        checks.append("Evidence coordinates track the original PDF at fit width and 150% zoom")

        alternate = next(item for item in candidates if "cet1_ratio" in item.get("metric_ids", []) and item["id"] != fact["candidate_id"])
        page.get_by_label("Source evidence", exact=True).select_option(alternate["id"])
        expect(page.get_by_label("PDF page number")).to_have_value(str(alternate["page"]))
        expect(page.locator(".evidence-highlight")).to_be_visible(timeout=30_000)
        expect(page.locator(".pdf-page")).not_to_have_class("pdf-page is-loading", timeout=30_000)
        check_coordinates(alternate)
        checks.append("Replacing the evidence candidate immediately selects and highlights its source")
        page.get_by_label("Source evidence", exact=True).select_option(fact["candidate_id"])
        expect(page.locator(".evidence-highlight")).to_be_visible(timeout=30_000)
        page.screenshot(path=str(OUTPUT / "workspace-desktop.png"), full_page=True)

        page.get_by_role("button", name="Add report", exact=True).click()
        expect(page.get_by_role("dialog")).to_be_visible()
        page.get_by_role("button", name="Cancel", exact=True).click()
        expect(page.get_by_role("dialog")).not_to_be_visible()
        checks.append("Upload dialog opens and cancels without changing the library")

        page.route("**/api/facts/*/review", lambda route: route.fulfill(status=409, content_type="application/json", body=json.dumps({"detail": "Simulated stale review for browser smoke test"})))
        page.get_by_label("Review note", exact=False).fill("Automated UI smoke check — intercepted; never persisted.")
        page.get_by_role("button", name="Accept figure", exact=True).click()
        expect(page.get_by_role("alert")).to_contain_text("Your draft is preserved")
        expect(page.get_by_label("Review note", exact=False)).to_have_value("Automated UI smoke check — intercepted; never persisted.")
        expect(page.get_by_role("button", name="Accept figure", exact=True)).to_be_disabled()
        page.get_by_role("button", name="Load latest record", exact=True).click()
        expect(page.get_by_role("button", name="Accept figure", exact=True)).to_be_enabled()
        checks.append("409 conflicts preserve the draft and require loading the latest review")

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function("""() => {
            const stage = document.querySelector('.pdf-stage');
            const paper = document.querySelector('.pdf-page');
            if (!stage || !paper) return false;
            return Math.abs(paper.getBoundingClientRect().width - (stage.clientWidth - 48)) < 1;
        }""")
        expect(page.locator(".pdf-loading")).to_have_count(0, timeout=30_000)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "Page overflows the mobile viewport"
        page.screenshot(path=str(OUTPUT / "workspace-mobile.png"), full_page=True)
        checks.append("390px layout has no page-level horizontal overflow")
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.get_by_role("button", name="Period comparison", exact=False).click()
        expect(page.locator(".comparison-table tbody tr")).to_have_count(5, timeout=30_000)
        expect(page.locator(".not-comparable").first).to_be_visible()
        page.screenshot(path=str(OUTPUT / "comparison-desktop.png"), full_page=True)
        checks.append("Comparison exposes five metric rows and actual comparability warnings")
        assert not errors, f"Browser errors: {errors}"
        context.close()
        browser.close()

    report = {"checks_passed": checks, "browser_errors": errors, "production_mutations": 0}
    (OUTPUT / "frontend-smoke.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
