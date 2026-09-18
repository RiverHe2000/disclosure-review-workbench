"""Download the fixed, official-source corpus; never silently replace a changed PDF."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_HOSTS = {"www.commbank.com.au", "www.westpac.com.au", "www.anz.com", "www.nab.com.au"}


def fetch(entry: dict, raw_dir: Path) -> dict:
    url = entry["url"]
    if urlparse(url).scheme != "https" or urlparse(url).hostname not in ALLOWED_HOSTS:
        raise ValueError("Corpus URLs must use one of the four official HTTPS domains")
    filename = entry["filename"]
    if Path(filename).name != filename or not filename.endswith(".pdf"):
        raise ValueError("Invalid corpus filename")
    expected_hash = entry.get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ValueError("Corpus manifest requires a pinned lowercase SHA-256 digest")
    destination = raw_dir / filename
    if destination.exists():
        content = destination.read_bytes()
    else:
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "DisclosureReviewWorkbench/0.1"})
                with urllib.request.urlopen(request, timeout=90) as response:
                    if urlparse(response.url).hostname not in ALLOWED_HOSTS:
                        raise ValueError("Unexpected download redirect domain")
                    content = response.read(80 * 1024 * 1024 + 1)
                if len(content) > 80 * 1024 * 1024:
                    raise ValueError("PDF exceeds 80 MiB limit")
                if not content.startswith(b"%PDF-"):
                    raise ValueError("Response is not a PDF")
                actual_hash = hashlib.sha256(content).hexdigest()
                if actual_hash != expected_hash:
                    raise ValueError(
                        f"SHA-256 mismatch for {filename}: expected {expected_hash}, got {actual_hash}. "
                        "The source changed; no PDF was saved. Review a new corpus version explicitly."
                    )
                temporary = destination.with_suffix(".pdf.part")
                temporary.write_bytes(content)
                temporary.replace(destination)
                break
            except ValueError:
                raise
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(attempt + 1)
    if not content.startswith(b"%PDF-"):
        raise ValueError("Local corpus file is not a PDF")
    actual_hash = hashlib.sha256(content).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(
            f"SHA-256 mismatch for local {filename}: expected {expected_hash}, got {actual_hash}. "
            "Existing file was preserved; restore the pinned corpus before evaluating."
        )
    return {**entry, "sha256": actual_hash, "bytes": len(content),
            "downloaded_or_verified_at": datetime.now(UTC).isoformat()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", nargs="*", help="Optional manifest document IDs")
    args = parser.parse_args()
    manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
    if args.only:
        unknown = set(args.only) - {entry["id"] for entry in manifest}
        if unknown:
            parser.error(f"Unknown document IDs: {sorted(unknown)}")
        manifest = [entry for entry in manifest if entry["id"] in args.only]
    raw_dir = ROOT / "data/raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    receipts = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(entry, pool.submit(fetch, entry, raw_dir)) for entry in manifest]
        for entry, future in futures:
            try:
                result = future.result()
                receipts.append(result)
                print(f"{entry['id']}: {result['bytes']:,} bytes verified", flush=True)
            except Exception as exc:
                receipts.append({**entry, "error": str(exc)})
                print(f"{entry['id']}: FAILED {exc}", flush=True)
    (raw_dir / "receipt.json").write_text(json.dumps(receipts, indent=2) + "\n", encoding="utf-8")
    if any("error" in entry for entry in receipts):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
