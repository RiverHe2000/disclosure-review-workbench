# Disclosure Desk frontend

React + TypeScript + Vite review workspace. Uses the root `CONTRACT.md` API at `/api`.

```sh
pnpm install
pnpm dev        # localhost:5173; /api forwards to localhost:8000
pnpm typecheck
pnpm build      # frontend/dist, served by the Python app
```

The library supports report upload, bank filters, rules/local Qwen jobs, polling, failed-job retries and explicit worker availability. Source-candidate selection defaults to candidates for the selected metric, with an option to inspect all candidates. Selecting a different candidate immediately previews its PDF page and bounding box; saving sends the candidate ID alongside the corrected value and context. The review pane submits versioned accept/edit/reject decisions; conflicting edits keep the local draft until the reviewer loads the latest record. Comparisons and CSV/Markdown exports use the server's comparability decisions.

PDF.js renders the original PDF into a high-DPI canvas. The evidence overlay uses the parser's 1-based physical page, bounding box and page dimensions: horizontal coordinates are normalized by `page_width` and vertical coordinates by `page_height`, so the overlay follows the rendered canvas at fit width and every zoom level. Page navigation temporarily hides evidence from another page. No screenshots or invented document content are used.

All counts, figures, review states and queue stages come from the API. Empty, pending, missing, failed and unavailable-model states remain explicit. Typography uses system sans-serif and Georgia, with no external font requests. The application has no account or paid API dependency.

## Browser verification

With the backend, Vite, Chromium for Playwright, and a populated CBA 2023 report available, run `python frontend/tests/smoke_ui.py` from the project root. This checks real PDF rendering, normalized highlight coordinates at two zoom levels, evidence replacement, the upload dialog, stale-version recovery, comparison warnings and mobile overflow. Its stale-version POST is intercepted inside the test browser; it never changes stored reviews or enqueues jobs. Screenshots and a check report are written to the project's ignored `output/` folder.

For the full workflow and a recording, run `python scripts/demo_ui.py`. It makes a SQLite backup and links the immutable PDFs into `output/demo_workbench`, then starts its own API on port 8001. Six source-checked CBA 2023/24 facts are reviewed through real browser actions; comparison changes and actual CSV/Markdown downloads are checked. Every review explicitly says `Scripted demonstration, not user research`. The source store's review-history hash must remain unchanged. The WebM recording, screenshots, exports and validation report stay in ignored `output/`; the isolated service stops when the script finishes.
