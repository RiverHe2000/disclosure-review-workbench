"""Local HTTP application; workers run in a separate process against the durable queue."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from disclosure.compare import compare_documents, export_csv, export_markdown
from disclosure.store import MAX_PDF_BYTES, Conflict, NotFound, Store, ValidationError, model_path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ORIGIN = re.compile(r"^https?://(?:localhost|127\.0\.0\.1|\[::1\])(?::[0-9]+)?$")


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    method: Literal["rules", "qwen"]
    retry: bool = False


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["accept", "edit", "reject"]
    expected_version: int = Field(ge=0, strict=True)
    reason: str = Field(min_length=1, max_length=4000)
    value: float | None = Field(default=None, allow_inf_nan=False, strict=True)
    period: str | None = None
    entity_scope: str | None = Field(default=None, max_length=200)
    basis: str | None = Field(default=None, max_length=200)
    observation: Literal["point_in_time", "quarter_average", "year_average", "unknown"] | None = None
    restatement_resolved: bool | None = None
    candidate_id: str | None = Field(default=None, max_length=200)


def create_app(data_dir: Path | None = None) -> FastAPI:
    store = Store(data_dir or Path(os.environ.get("DISCLOSURE_DATA_DIR", str(PROJECT_ROOT / "data/workbench"))))
    app = FastAPI(title="Disclosure Review Workbench", version="0.1.0")
    app.state.store = store
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])
    app.add_middleware(CORSMiddleware, allow_origin_regex=LOCAL_ORIGIN.pattern,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Content-Type"], allow_credentials=False)

    @app.middleware("http")
    async def local_mutations(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and not LOCAL_ORIGIN.fullmatch(origin):
            return JSONResponse(status_code=403, content={"detail": "Only local browser origins may modify this workbench."})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(NotFound)
    async def not_found_handler(request: Request, exc: NotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc.args[0])})

    @app.exception_handler(Conflict)
    async def conflict_handler(request: Request, exc: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def validation_handler(request: Request, exc: ValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.get("/api/health")
    def health():
        path = model_path()
        available = (path / "config.json").is_file() and (any(path.glob("*.safetensors")) or any(path.glob("pytorch_model*.bin")))
        return dict(status="ok", model_available=available, model_path=str(path), worker_active=store.worker_active())

    @app.get("/api/documents")
    def documents():
        return store.documents()

    @app.post("/api/documents", status_code=201)
    async def upload(file: Annotated[UploadFile, File()], bank: Annotated[str, Form()],
                     year: Annotated[int, Form()], source_url: Annotated[str, Form()] = ""):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(422, "Upload a PDF file.")
        if source_url:
            url = urlparse(source_url)
            if url.scheme not in {"http", "https"} or not url.netloc or url.username or url.password:
                raise HTTPException(422, "Source URL must be an HTTP or HTTPS URL without credentials.")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=store.data_dir, suffix=".upload", delete=False) as stream:
                temporary = Path(stream.name)
                total = 0
                while block := await file.read(1024 * 1024):
                    total += len(block)
                    if total > MAX_PDF_BYTES:
                        raise HTTPException(413, "PDF exceeds the 50 MiB limit.")
                    stream.write(block)
            return await run_in_threadpool(store.register_document, temporary, bank, year, source_url, file.filename)
        finally:
            await file.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @app.get("/api/documents/{document_id}/pdf")
    def pdf(document_id: str):
        doc = store.document(document_id)
        return FileResponse(store.document_path(document_id), media_type="application/pdf",
                            filename=doc["filename"], content_disposition_type="inline")

    @app.get("/api/documents/{document_id}/facts")
    def facts(document_id: str):
        return store.facts(document_id)

    @app.get("/api/documents/{document_id}/candidates")
    def candidates(document_id: str):
        return store.candidates(document_id)

    @app.post("/api/documents/{document_id}/jobs", status_code=202)
    def enqueue(document_id: str, body: JobRequest):
        return store.enqueue(document_id, body.method, body.retry)

    @app.get("/api/jobs")
    def jobs():
        return store.jobs()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return store.job(job_id)

    @app.post("/api/facts/{fact_id}/review")
    def review(fact_id: str, body: ReviewRequest):
        return store.review(fact_id, body.model_dump(exclude_unset=True))

    @app.get("/api/compare")
    def compare(left: str, right: str):
        return compare_documents(store, left, right)

    @app.get("/api/export")
    def export(left: str, right: str, format: Literal["csv", "markdown"] = "csv"):
        comparison = compare_documents(store, left, right)
        body = export_csv(comparison) if format == "csv" else export_markdown(comparison)
        extension = "csv" if format == "csv" else "md"
        return Response(body, media_type="text/csv" if format == "csv" else "text/markdown",
                        headers={"Content-Disposition": f'attachment; filename="disclosure-comparison.{extension}"'})

    @app.get("/api/stats")
    def stats():
        return store.stats()

    frontend = PROJECT_ROOT / "frontend/dist"

    @app.get("/{asset_path:path}", include_in_schema=False)
    def frontend_file(asset_path: str):
        if asset_path == "api" or asset_path.startswith("api/"):
            raise HTTPException(404, "API route not found.")
        path = (frontend / asset_path).resolve()
        if not path.is_relative_to(frontend.resolve()):
            raise HTTPException(404, "File not found.")
        if path.is_file():
            return FileResponse(path)
        if asset_path and Path(asset_path).suffix:
            raise HTTPException(404, "File not found.")
        index = frontend / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse({"message": "Frontend is not built. Run the frontend build; the API is ready.", "api": "/docs"})

    return app
