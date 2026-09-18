# Implementation contract

Single-user localhost app, no paid services. Python package `disclosure` under `src/`.
Frontend React/TS/Vite under `frontend/`; API root `/api`. English UI. Do not add coauthor trailers.

## Shared data shapes (JSON)
- Document: id, bank (`CBA|Westpac|ANZ|NAB`), year (int), filename, sha256, source_url, created_at, page_count.
- Candidate: id, document_id, page (1-based physical PDF page), bbox [x0,top,x1,bottom] in PDF points, page_width, page_height, text, context (header and adjacent lines), metric_ids (list), numbers (list of strings). Coordinates always parser-originated. Context MUST remain page-local; candidate id stable for same PDF.
- Fact: id, document_id, run_id, metric_id (`cet1_ratio|total_capital_ratio|rwa|lcr|nsfr`), value (number or null), unit (`percent|AUD_million`), period (ISO date or null), entity_scope (string), basis (string), observation (`point_in_time|quarter_average|year_average|unknown`), restated (bool), candidate_id (string or null), evidence (Candidate or null), status (`pending|accepted|rejected|missing`), issues (list of strings), method (`rules|qwen|replay`), review (object or null).
- Review: action (`accept|edit|reject`), value/period/entity_scope/basis/observation/candidate_id (optional overrides), restatement_resolved (optional boolean), reason (string), expected_version (int). Backend requires evidence for accepted results and explicit resolution of unknown context. A restatement confirmation is invalidated when the evidence, value or reporting context changes, unless that same review explicitly reconfirms `restatement_resolved: true`; note-only reviews and unchanged values retain it. Units and the original restated flag remain read-only. Versioned manual review is stored separately from originals. Re-running must NOT discard prior reviews; latest run shown, history retained.
- Job: id, document_id, method (`rules|qwen`), status (`queued|running|completed|failed`), stage, error, attempts, created_at, updated_at. Request idempotency uses file hash+method+pipeline/model/prompt version. Failed job can explicitly retry. Leases prevent stale worker completion.

## HTTP
GET /api/health -> {status, model_available, model_path, worker_active}
GET /api/documents -> Document[]
POST /api/documents multipart file, bank, year, source_url -> Document (size capped, PDF only)
GET /api/documents/{id}/pdf -> original PDF (Range support desirable)
GET /api/documents/{id}/facts -> Fact[] (latest completed run, with effective review and evidence; each fact includes version int)
GET /api/documents/{id}/candidates -> Candidate[]
POST /api/documents/{id}/jobs JSON {method:'rules'|'qwen', retry?:bool} -> Job
GET /api/jobs -> Job[]
GET /api/jobs/{id} -> Job
POST /api/facts/{id}/review -> Fact (409 stale expected_version; 422 invalid/no evidence)
GET /api/compare?left={document_id}&right={document_id} -> {left:Document,right:Document,rows:[{metric_id,label,left:Fact|null,right:Fact|null,comparable:bool,delta:number|null,relative_change:number|null,unit:'percent'|'AUD_million',delta_unit:'percentage_points'|'AUD_million',reasons:string[]}]}.
Comparisons require both facts accepted, same bank/entity/basis/observation, valid matching fiscal date pattern, explicit periods, same unit, and no unresolved restatement. `unit` describes the source values; `delta_unit` describes the change (ratios use percentage points). CSV/Markdown use `delta_unit` for changes. `relative_change` is a fraction and ONLY for RWA (null if baseline zero).
Version 1 permits routine comparisons only within fiscal years 2023–2025. Other years are blocked with an explicit additional-framework-confirmation reason; capital comparisons across the 2022–2023 boundary additionally identify the framework change. Review acceptance or `restatement_resolved` does not bypass this limit, and the app does not automatically restate prior-framework figures.
GET /api/export?left=...&right=...&format=csv|markdown -> attachment incl sources, evidence pages, review state and comparability reasons.
GET /api/stats -> {documents,jobs_completed,jobs_failed,facts_pending,facts_accepted,latest_runs:[...]}. No fabricated costs or uptime.

## Module interfaces
`parser.parse_pdf(path: Path, document_id: str) -> list[dict]` returns Candidates; no gold labels.
`extraction.extract_rules(candidates: list[dict], document: dict) -> list[dict]` returns exactly 5 Fact-like dicts (without id/run_id/evidence/review/version); unresolved context flagged rather than invented. metric catalog in `extraction.METRICS` dict {id:{label,unit,...}}.
Root owns `model.py` extract_qwen(candidates,document,model_path) -> facts, with same shape; local HF only, bounded context/output, select candidate IDs, validation against source.
Backend owner supplies `Store(data_dir)` API and documents actual method signatures in STORE_API.md. Store persists candidates and runs and supports worker claim/heartbeat/complete/fail. Root writes worker/CLI after that interface is reported.
Backend `create_app(data_dir: Path|None=None)` in api.py; default env DISCLOSURE_DATA_DIR or project `data/workbench`. Model path env DISCLOSURE_MODEL_PATH or D:/models/Qwen3-4B-Instruct-2507. Frontend built files served if present. CORS only localhost Vite dev.

## Corpus
Manifest `data/manifest.json` array entries {id,bank,year,url,filename,split}; 12 official annual reports 2023-25. Development CBA/Westpac/ANZ 2023-24; temporal_test these banks 2025; bank_test NAB 2023-25. Store source URLs, hashes after download in ignored local receipt. PDFs excluded from git. Gold `data/gold.json` labels CURRENT PERIOD metrics with value, unit, actual period, entity/basis/observation, evidence page and quote, and annotation status. Labels are checked directly against source tables with AI assistance, independently of extraction predictions; they have not undergone an independent human audit. Do not derive gold from model prediction. If a label is unverified, use null with explicit annotation status. The benchmark scores only source-checked pilot labels marked `verified`, reports coverage and missing labels, and must not describe these as independently human-validated ground truth.
