"""Local command line: start services, register corpus, or reproduce evaluation."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .worker import PROJECT_ROOT, default_data_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Disclosure Desk - local evidence review")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    sub = parser.add_subparsers(dest="command", required=True)
    server = sub.add_parser("serve")
    server.add_argument("--port", type=int, default=8000)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--drain", action="store_true")
    worker.add_argument("--model-path")
    ingest = sub.add_parser("ingest", help="Register downloaded official PDFs and enqueue work")
    ingest.add_argument("--manifest", type=Path, default=PROJECT_ROOT / "data/manifest.json")
    ingest.add_argument("--raw-dir", type=Path, default=PROJECT_ROOT / "data/raw")
    ingest.add_argument("--method", choices=["rules", "qwen"], default="rules")
    ingest.add_argument("--ids", nargs="*")
    ingest.add_argument("--pipeline-version")
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--method", choices=["rules", "qwen"], default="rules")
    benchmark.add_argument("--ids", nargs="*")
    benchmark.add_argument("--output", type=Path, default=PROJECT_ROOT / "reports")
    benchmark.add_argument("--model-path")
    rescore = sub.add_parser("rescore", help="Score recorded predictions without running the model")
    rescore.add_argument("report", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.command == "serve":
        import uvicorn

        from .api import create_app

        uvicorn.run(create_app(args.data_dir), host="127.0.0.1", port=args.port)
    elif args.command == "worker":
        from .worker import run_worker

        run_worker(args.data_dir, once=args.once, drain=args.drain, model_path=args.model_path)
    elif args.command == "ingest":
        from .store import Store

        store = Store(args.data_dir)
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        for entry in manifest:
            if args.ids and entry["id"] not in args.ids:
                continue
            path = args.raw_dir / entry["filename"]
            if not path.is_file():
                logging.warning("Not downloaded: %s", entry["id"])
                continue
            doc = store.register_document(path, bank=entry["bank"], year=entry["year"], source_url=entry["url"])
            job = store.enqueue(doc["id"], args.method, pipeline_version=args.pipeline_version)
            print(json.dumps({"manifest_id": entry["id"], "document_id": doc["id"], "job_id": job["id"], "status": job["status"]}))
    elif args.command == "benchmark":
        from .benchmark import run_benchmark

        run_benchmark(PROJECT_ROOT, args.method, args.output, args.ids, args.model_path)
    elif args.command == "rescore":
        from .benchmark import rescore_report

        report = rescore_report(PROJECT_ROOT, args.report)
        print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
