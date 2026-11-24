"""
Download a case's documents from the CAT tool (Spark Case Assessment web app).

Does exactly what you would do in the browser, through the app's own API:
    sign in  ->  search the case number  ->  open the case  ->  Document vault  ->  Download all

For each case it saves, in cat_output/cases/<case>_<name>/documents/:
    <the files, in the same folders as the "Download all" zip (ID/, Income/, Bank Statements/ ...)>
    documents.json    - the document list (type, file name, size, upload date, processing status)
    documents.csv     - same, as a spreadsheet
and records the case in cat_output/documents_manifest.json.

A case is downloaded again only when its document list has changed (or with --refetch).

Usage:
    python fetch_cat_documents.py 436696
    python fetch_cat_documents.py 435864 435898 436216 --refetch

Needs in .env:
    CAT_URL=http://192.168.80.52:2330        (the web app you sign in to)
    CAT_USERNAME=you@company.co.uk
    CAT_PASSWORD=...
    CAT_API_URL=http://192.168.80.52:2530    (optional - found automatically from CAT_URL)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = SCRIPT_DIR / "cat_output"


class CatDocumentsError(Exception):
    pass


def _safe(name) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", str(name or "unknown")).strip(" ._")
    return name[:80] or "unknown"


def clean_ref(ref: str) -> str:
    return re.sub(r"-[A-Za-z]+$", "", str(ref or "").strip())


# --------------------------------------------------------------------------- client
class CatWebClient:
    """Signed-in session against the CAT web app's API."""

    def __init__(self, web_url: str, username: str, password: str, api_url: str | None = None):
        if not (web_url and username and password):
            raise CatDocumentsError("CAT_URL, CAT_USERNAME and CAT_PASSWORD must be set in .env")
        self.web_url = web_url.rstrip("/")
        self.username, self.password = username, password
        self.api_url = (api_url or "").rstrip("/") or self._discover_api_url()
        self.s = requests.Session()
        self.s.headers.update({"Referer": self.web_url + "/", "Origin": self.web_url, "Accept": "application/json"})
        self._logged_in = False

    def _discover_api_url(self) -> str:
        """The web app's JS bundle holds its API base URL (VITE_API_BASE_URL)."""
        try:
            html = requests.get(self.web_url + "/", timeout=20).text
            for src in re.findall(r'src="(/assets/index-[^"]+\.js)"', html):
                js = requests.get(self.web_url + src, timeout=60).text
                m = re.search(r'VITE_API_BASE_URL:"(https?://[^"]+)"', js) or re.search(r'baseURL:"(https?://[^"]+)"', js)
                if m:
                    return m.group(1).rstrip("/")
        except requests.RequestException:
            pass
        raise CatDocumentsError("Could not find the CAT API address - set CAT_API_URL in .env")

    # -- auth
    def login(self) -> None:
        self.s.cookies.clear()
        self.s.get(self.api_url + "/api/auth/me/", timeout=30)        # sets the csrftoken cookie
        r = self.s.post(self.api_url + "/api/auth/login/",
                        json={"email": self.username, "password": self.password},
                        headers={"X-CSRFToken": self.s.cookies.get("csrftoken") or ""}, timeout=30)
        if r.status_code != 200:
            try:
                msg = r.json()
            except ValueError:
                msg = r.text[:200]
            raise CatDocumentsError(f"CAT sign-in failed (HTTP {r.status_code}): {msg}")
        self._logged_in = True

    def _get(self, path: str, **kw) -> requests.Response:
        if not self._logged_in:
            self.login()
        for attempt in range(4):
            try:
                r = self.s.get(self.api_url + path, timeout=kw.pop("timeout", 60), **kw)
            except requests.RequestException as exc:
                if attempt == 3:
                    raise CatDocumentsError(f"Network error talking to CAT: {exc}") from exc
                time.sleep(3 * (attempt + 1))
                continue
            if r.status_code in (401, 403) and attempt == 0:      # session expired -> sign in again
                self.login()
                continue
            if r.status_code >= 500 and attempt < 3:
                time.sleep(3 * (attempt + 1))
                continue
            return r
        return r

    # -- cases
    def find_case(self, ref: str, max_pages: int = 5) -> dict | None:
        """Search like the 'Search by name or case reference' box and pick the exact match."""
        ref = clean_ref(ref)
        for page in range(1, max_pages + 1):
            r = self._get("/api/applications/", params={"search": ref, "page": page})
            if r.status_code != 200:
                raise CatDocumentsError(f"CAT case search failed (HTTP {r.status_code})")
            data = r.json()
            for row in data.get("results") or []:
                if clean_ref(str(row.get("client_reference") or "")) == ref:
                    return row
            if not data.get("has_next"):
                break
        return None

    def case_detail(self, app_id: str) -> dict:
        r = self._get(f"/api/applications/{app_id}/")
        if r.status_code != 200:
            raise CatDocumentsError(f"Could not open CAT case {app_id} (HTTP {r.status_code})")
        return r.json()

    def download_zip(self, app_id: str) -> tuple[bytes, str]:
        r = self._get(f"/api/applications/{app_id}/documents/zip/", timeout=600)
        if r.status_code != 200:
            raise CatDocumentsError(f"'Download all' failed for CAT case {app_id} (HTTP {r.status_code}): {r.text[:200]}")
        m = re.search(r'filename="?([^";]+)"?', r.headers.get("content-disposition") or "")
        return r.content, (m.group(1) if m else f"{app_id}.zip")


# --------------------------------------------------------------------------- saving
def _case_dir(out_root: Path, ref: str, name: str) -> Path:
    base = out_root / "cases"
    if base.exists():
        for existing in sorted(base.glob(f"{_safe(ref)}_*")):
            if existing.is_dir():
                return existing
    return base / f"{_safe(ref)}_{_safe(name)}"


def _extract(zip_bytes: bytes, dest: Path) -> list[dict]:
    """Extract the zip safely (no paths outside dest), dropping the top 'Name #ref/' folder."""
    files = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            parts = [p for p in PurePosixPath(info.filename.replace("\\", "/")).parts if p not in ("", ".", "..")]
            if len(parts) > 1 and "#" in parts[0]:
                parts = parts[1:]
            if not parts:
                continue
            target = dest.joinpath(*[re.sub(r'[<>:"|?*\x00-\x1f]', "_", p) for p in parts])
            if dest.resolve() not in target.resolve().parents:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)
            files.append({"path": target.relative_to(dest).as_posix(), "size": info.file_size})
    return files


def _doc_rows(docs: list[dict]) -> list[dict]:
    keep = ["id", "document_type", "file_name", "file_size", "mime_type", "upload_date", "processing_status",
            "processing_error", "belongs_to", "is_valid", "notes"]
    return [{k: d.get(k) for k in keep} for d in docs if isinstance(d, dict)]


def fetch_documents(client: CatWebClient, ref: str, out_root: Path = DEFAULT_OUT, refetch: bool = False,
                    log=print) -> dict:
    ref = clean_ref(ref)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "documents_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    row = client.find_case(ref)
    if not row:
        log(f"  ✗ CAT documents {ref}: case not found in the CAT tool")
        return {"status": "not_found", "case_ref": ref}
    detail = client.case_detail(row["id"])
    docs = _doc_rows(detail.get("documents") or [])
    signature = sorted(f"{d['id']}:{d.get('file_size')}" for d in docs)
    prev = manifest.get(ref)
    if prev and not refetch and prev.get("signature") == signature:
        log(f"  CAT documents {ref}: already downloaded ({prev['files']} files, unchanged)")
        return {**prev, "status": "skipped"}

    name = row.get("client_name") or ref
    folder = _case_dir(out_root, ref, name) / "documents"
    if not docs:
        folder.mkdir(parents=True, exist_ok=True)
        files = []
        log(f"  CAT documents {ref}: no documents on this case")
    else:
        zip_bytes, zip_name = client.download_zip(row["id"])
        if folder.exists():
            shutil.rmtree(folder)            # replace with the current set
        folder.mkdir(parents=True, exist_ok=True)
        files = _extract(zip_bytes, folder)
        log(f"  ✓ CAT documents {ref} - {name}: {len(files)} files ({len(zip_bytes) / 1e6:.1f} MB)")

    (folder / "documents.json").write_text(json.dumps(docs, indent=2, ensure_ascii=False), encoding="utf-8")
    with open(folder / "documents.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=list(docs[0].keys()) if docs else ["id"])
        w.writeheader()
        w.writerows(docs)

    entry = {
        "case_ref": ref,
        "application_id": row["id"],
        "customer_name": name,
        "cat_status": detail.get("status"),
        "phase": detail.get("phase"),
        "documents": len(docs),
        "files": len(files),
        "failed_processing": sum(1 for d in docs if d.get("processing_status") == "failed"),
        "folder": str(folder.relative_to(out_root)),
        "file_list": files,
        "signature": signature,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest[ref] = entry
    tmp = manifest_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, manifest_path)
    return {**entry, "status": "fetched"}


def client_from_env() -> CatWebClient:
    load_dotenv(SCRIPT_DIR / ".env")
    return CatWebClient(os.getenv("CAT_URL", ""), os.getenv("CAT_USERNAME") or os.getenv("CAT_EMAIL", ""),
                        os.getenv("CAT_PASSWORD", ""), os.getenv("CAT_API_URL"))


def main():
    p = argparse.ArgumentParser(description="Download case documents from the CAT tool ('Download all').")
    p.add_argument("cases", nargs="+", help="Case numbers, e.g. 436696 435898")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="Output folder (default cat_output).")
    p.add_argument("--refetch", action="store_true", help="Download again even if unchanged.")
    args = p.parse_args()
    try:
        client = client_from_env()
        client.login()
    except CatDocumentsError as exc:
        sys.exit(str(exc))
    print(f"Signed in to the CAT tool ({client.api_url}).")
    failed = 0
    for ref in dict.fromkeys(clean_ref(c) for c in args.cases):
        try:
            r = fetch_documents(client, ref, Path(args.out), args.refetch)
            failed += r["status"] == "not_found"
        except CatDocumentsError as exc:
            failed += 1
            print(f"  ✗ CAT documents {ref}: {exc}")
    print(f"Done. Output: {args.out}" + (f" ({failed} failed)" if failed else ""))


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
