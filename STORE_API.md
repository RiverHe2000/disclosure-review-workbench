# Store and worker API

`from disclosure.store import Store, LeaseLost, Conflict, ValidationError, NotFound`

Each call opens its own SQLite connection; WAL, foreign keys, busy timeout and `BEGIN IMMEDIATE` protect mutations. `Store(data_dir: Path)` creates the database and `documents/` directory. There is no in-process worker thread.

```python
store.register_document(path: Path, bank: str, year: int, source_url: str = "", filename: str | None = None) -> dict
store.document(document_id: str) -> dict
store.document_path(document_id: str) -> Path
store.documents() -> list[dict]
store.enqueue(document_id: str, method: str, retry: bool = False, pipeline_version: str | None = None) -> dict
store.jobs() -> list[dict]
store.job(job_id: str) -> dict
store.claim(worker_id: str = "worker", lease_seconds: float = 120) -> dict | None
store.heartbeat(job_id: str, lease_token: str, stage: str | None = None, lease_seconds: float = 120) -> bool
store.complete(job_id: str, lease_token: str, candidates: list[dict], facts: list[dict]) -> str  # run id
store.fail(job_id: str, lease_token: str, error: str) -> bool
store.touch_worker(worker_id: str = "worker") -> None
store.worker_active(max_age: float = 60) -> bool
store.facts(document_id: str) -> list[dict]
store.candidates(document_id: str) -> list[dict]
store.review(fact_id: str, review: dict) -> dict
store.stats() -> dict
```

Claim atomically reclaims expired jobs and claims one queued job. Result includes `lease_token`, `document` (Document), and `path` (absolute str); the caller must heartbeat during expensive work. Heartbeat returns false after expiry/reclaim. `complete` raises `LeaseLost` for a stale lease; `fail` returns false. A successful completion stores immutable candidates and original facts in one transaction before marking the job complete. The worker may call `touch_worker` while idle. All timestamps are UTC ISO strings except internal lease timestamps.

Registration validates actual PDF parsing, caps at 50 MiB, uses sha256 for filename/dedup, and rejects conflicting bank/year for identical bytes. Jobs are idempotent per document/method/pipeline version (default includes code/prompt revision and local model identity). Explicit retry is only for failed jobs. Completed reruns require a changed pipeline_version; previous runs/reviews remain retained.

Facts must include exactly the five contractual metric IDs. Store assigns id/run_id/document_id and original review/version. Reviews require expected_version, action and a nonempty reason. Accept/edit requires an existing referenced candidate, a finite value, ISO period, entity_scope, basis, and a known observation. Optional `restatement_resolved: true` records explicit human resolution when original `restated` is true. Comparisons otherwise block restated observations. Review rows are append-only; originals never change.

Reviews may optionally override `candidate_id`; the candidate must belong to the same run/document and identify this metric. Effective evidence updates with the selection, preserving the original fact. This allows a reviewer to recover a missing extraction using an existing candidate. Period must match the document fiscal year. `stats.latest_runs` merges actual matching worker `run_artifacts/{job_id}.json` timing/model metadata when available. Comparison `relative_change` is a fraction (0.1 means 10%), and only applies to RWA.

Comparison rows distinguish source `unit` (`percent` or `AUD_million`) from `delta_unit` (`percentage_points` or `AUD_million`). CSV/Markdown label changes with `delta_unit`. Routine comparisons are limited to 2023–2025; a year outside this range blocks every change pending additional framework confirmation. Capital rows crossing 2022–2023 also explain that a separately verified like-for-like restatement is required. This v1 boundary cannot be overridden with review acceptance or `restatement_resolved`; no automatic restatement is performed.
