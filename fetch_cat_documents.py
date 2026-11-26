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

Pacing (optional, in .env). CAT is asked politely, like a person clicking through it:
    CAT_REQUEST_GAP_S=1.5      at least this long between any two requests to CAT
    CAT_CASE_GAP_S=3-5         a pause of 3-5 seconds (random) before each case
    CAT_SEARCH_ATTEMPTS=3      a case CAT does not find is searched again, after a longer pause
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
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


# The case number can sit in any of these on a search result row.
REF_FIELDS = ("client_reference", "case_reference", "reference", "client_id", "case_id", "crm_reference",
              "crm_id", "external_reference")


def row_matches(row: dict, ref: str) -> bool:
    """True if a search result row is this case: its reference is the case number, alone or with a
    prefix/suffix that isn't more digits ('431037', '431037-W', 'TIG431037', '431037/1')."""
    ref = clean_ref(ref)
    if not ref:
        return False
    pattern = re.compile(rf"(?<!\d){re.escape(ref)}(?!\d)")
    for field in REF_FIELDS:
        value = str(row.get(field) or "").strip()
        if value and (clean_ref(value) == ref or pattern.search(value)):
            return True
    return False


def _row_ref(row: dict) -> str:
    return next((str(row[f]) for f in REF_FIELDS if row.get(f)), "?")


def _name_key(name: str) -> str:
    return " ".join(re.sub(r"[^a-z ]+", " ", str(name or "").lower()).split())


def parse_gap(text: str | None, default: tuple[float, float]) -> tuple[float, float]:
    """'4' -> (4, 4); '3-5' -> (3, 5). Bad or empty values give the default."""
    nums = re.findall(r"\d+(?:\.\d+)?", str(text or ""))
    if not nums:
        return default
    lo, hi = float(nums[0]), float(nums[1] if len(nums) > 1 else nums[0])
    return (min(lo, hi), max(lo, hi))


# --------------------------------------------------------------------------- client
class CatWebClient:
    """Signed-in session against the CAT web app's API.

    Requests are paced: at least `request_gap` seconds between any two, and `pause_case()`
    waits `case_gap` (3-5 s by default) before each case, so CAT is never hit back-to-back."""

    def __init__(self, web_url: str, username: str, password: str, api_url: str | None = None,
                 request_gap: float = 1.5, case_gap: tuple[float, float] = (3.0, 5.0),
                 search_attempts: int = 3, sleep=time.sleep):
        if not (web_url and username and password):
            raise CatDocumentsError("CAT_URL, CAT_USERNAME and CAT_PASSWORD must be set in .env")
        self.web_url = web_url.rstrip("/")
        self.username, self.password = username, password
        self.request_gap = max(0.0, request_gap)
        self.case_gap = case_gap
        self.search_attempts = max(1, search_attempts)
        self._sleep = sleep
        self._last_request = 0.0
        self.last_search: dict = {"terms": [], "seen": []}
        self.s = requests.Session()
        self.s.headers.update({"Referer": self.web_url + "/", "Origin": self.web_url, "Accept": "application/json"})
        self._logged_in = False
        self.api_url = (api_url or "").rstrip("/") or self._discover_api_url()

    # -- pacing
    def _pace(self) -> None:
        wait = self._last_request + self.request_gap - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    def pause_case(self, log=None, why: str = "") -> float:
        """Wait 3-5 s (CAT_CASE_GAP_S) before the next case."""
        secs = round(random.uniform(*self.case_gap), 1)
        if secs > 0:
            if log and why:
                log(f"  … waiting {secs:g}s {why}")
            self._sleep(secs)
        return secs

    def _discover_api_url(self) -> str:
        """The web app's JS bundle holds its API base URL (VITE_API_BASE_URL)."""
        for attempt in range(3):
            try:
                html = requests.get(self.web_url + "/", timeout=(10, 60)).text
                for src in re.findall(r'src="(/assets/index-[^"]+\.js)"', html):
                    js = requests.get(self.web_url + src, timeout=(10, 120)).text
                    m = (re.search(r'VITE_API_BASE_URL:"(https?://[^"]+)"', js)
                         or re.search(r'baseURL:"(https?://[^"]+)"', js))
                    if m:
                        return m.group(1).rstrip("/")
            except requests.RequestException:
                pass
            self._sleep(3 * (attempt + 1))
        raise CatDocumentsError("Could not find the CAT API address - set CAT_API_URL in .env "
                                "(e.g. CAT_API_URL=http://192.168.80.52:2530)")

    # -- auth
    def login(self) -> None:
        self.s.cookies.clear()
        try:
            self._pace()
            self.s.get(self.api_url + "/api/auth/me/", timeout=(10, 60))        # sets the csrftoken cookie
            self._pace()
            r = self.s.post(self.api_url + "/api/auth/login/",
                            json={"email": self.username, "password": self.password},
                            headers={"X-CSRFToken": self.s.cookies.get("csrftoken") or ""}, timeout=(10, 60))
        except requests.RequestException as exc:
            raise CatDocumentsError(f"Could not reach CAT to sign in: {exc}") from exc
        if r.status_code != 200:
            try:
                msg = r.json()
            except ValueError:
                msg = r.text[:200]
            raise CatDocumentsError(f"CAT sign-in failed (HTTP {r.status_code}): {msg}")
        self._logged_in = True

    def _get(self, path: str, timeout=(10, 90), **kw) -> requests.Response:
        """GET with pacing, a fresh sign-in on 401/403, and waits on 429/5xx/network errors."""
        if not self._logged_in:
            self.login()
        signed_in_again = False
        r, last_error = None, None
        for attempt in range(5):
            self._pace()
            try:
                r = self.s.get(self.api_url + path, timeout=timeout, **kw)
            except requests.RequestException as exc:
                r, last_error = None, exc
                self._sleep(5 * (attempt + 1))
                continue
            if r.status_code in (401, 403) and not signed_in_again:      # session expired -> sign in again
                signed_in_again = True
                self.login()
                continue
            if r.status_code == 429:                                    # too many requests -> back off
                try:
                    wait = float(r.headers.get("Retry-After") or 0)
                except ValueError:
                    wait = 0
                self._sleep(min(120.0, max(wait, 10.0 * (attempt + 1))))
                continue
            if r.status_code >= 500:
                self._sleep(5 * (attempt + 1))
                continue
            return r
        if r is None:
            raise CatDocumentsError(f"Network error talking to CAT: {last_error}") from last_error
        return r

    def _get_json(self, path: str, what: str, **kw) -> dict:
        for attempt in range(3):
            r = self._get(path, **kw)
            if r.status_code != 200:
                raise CatDocumentsError(f"{what} failed (HTTP {r.status_code}): {r.text[:200]}")
            try:
                return r.json()
            except ValueError:                       # half a response / an HTML error page -> ask again
                self._sleep(5 * (attempt + 1))
        raise CatDocumentsError(f"{what}: CAT did not send valid JSON")

    # -- cases
    def search(self, term: str, max_pages: int = 20) -> list[dict]:
        """Every row the 'Search by name or case reference' box returns for `term`."""
        rows: list[dict] = []
        for page in range(1, max_pages + 1):
            data = self._get_json("/api/applications/", f"CAT case search '{term}'",
                                  params={"search": term, "page": page})
            if isinstance(data, list):               # an unpaginated list
                return rows + [r for r in data if isinstance(r, dict)]
            rows += [r for r in data.get("results") or [] if isinstance(r, dict)]
            if not (data.get("has_next") or data.get("next")):
                break
        return rows

    def find_case(self, ref: str, names: tuple[str, ...] | list[str] = (), log=None) -> dict | None:
        """Find the case the way you would in the browser: search the case number, then the
        customer's name, and pick the row whose reference is this case. A case CAT does not
        return is searched again after a longer pause (CAT_SEARCH_ATTEMPTS)."""
        ref = clean_ref(ref)
        terms = list(dict.fromkeys(t for t in [ref, *(n.strip() for n in names if n)] if t))
        self.last_search: dict = {"terms": terms, "seen": []}
        for attempt in range(self.search_attempts):
            if attempt:
                secs = round(random.uniform(*self.case_gap) * (attempt + 1), 1)
                if log:
                    log(f"  … CAT did not return case {ref} yet - searching again in {secs:g}s "
                        f"(try {attempt + 1} of {self.search_attempts})")
                self._sleep(secs)
            for term in terms:
                rows = self.search(term)
                for row in rows:
                    if row_matches(row, ref):
                        return row
                for row in rows:
                    seen = f"{_row_ref(row)} ({row.get('client_name') or '?'})"
                    if seen not in self.last_search["seen"]:
                        self.last_search["seen"].append(seen)
        return None

    def case_detail(self, app_id: str) -> dict:
        return self._get_json(f"/api/applications/{app_id}/", f"Opening CAT case {app_id}")

    def document_list(self, app_id: str) -> list[dict]:
        """The Document vault list, for when the case detail comes back without `documents`."""
        r = self._get(f"/api/applications/{app_id}/documents/")
        if r.status_code != 200:
            return []
        try:
            data = r.json()
        except ValueError:
            return []
        rows = data.get("results") if isinstance(data, dict) else data
        return [d for d in rows or [] if isinstance(d, dict)]

    def download_zip(self, app_id: str, expected: int = 0) -> tuple[bytes, str]:
        """'Download all'. A zip that is cut short, or holds fewer files than the vault lists,
        is downloaded again (after a pause) before giving up."""
        last = ""
        for attempt in range(3):
            if attempt:
                self._sleep(random.uniform(*self.case_gap) * (attempt + 1))
            r = self._get(f"/api/applications/{app_id}/documents/zip/", timeout=(10, 600))
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:200]}"
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    count = sum(1 for i in z.infolist() if not i.is_dir())
            except zipfile.BadZipFile:
                last = f"the zip was incomplete ({len(r.content)} bytes)"
                continue
            if expected and count < expected and attempt < 2:
                last = f"the zip held {count} of {expected} files"
                continue
            m = re.search(r'filename="?([^";]+)"?', r.headers.get("content-disposition") or "")
            return r.content, (m.group(1) if m else f"{app_id}.zip")
        raise CatDocumentsError(f"'Download all' failed for CAT case {app_id}: {last}")


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


def known_names(ref: str, out_root: Path = DEFAULT_OUT) -> list[str]:
    """The customer's name as the CAT case export saved it, to search CAT by name too."""
    names = []
    try:
        export = json.loads((out_root / "manifest.json").read_text(encoding="utf-8"))
        names.append((export.get(clean_ref(ref)) or {}).get("customer_name") or "")
    except (OSError, ValueError):
        pass
    base = out_root / "cases"
    if base.exists():
        for d in base.glob(f"{_safe(clean_ref(ref))}_*"):
            names.append(d.name.split("_", 1)[1] if "_" in d.name else "")
    return [n for n in dict.fromkeys(n.strip() for n in names) if n and n != "unknown"]


def fetch_documents(client: CatWebClient, ref: str, out_root: Path = DEFAULT_OUT, refetch: bool = False,
                    log=print, names: list[str] | None = None) -> dict:
    ref = clean_ref(ref)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "documents_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    names = list(dict.fromkeys([*(names or []), *known_names(ref, out_root)]))
    row = client.find_case(ref, names, log)
    if not row:
        tried = ", ".join(f"'{t}'" for t in client.last_search["terms"])
        seen = client.last_search["seen"]
        detail = (f"; CAT returned only: {', '.join(seen[:6])}" + (" …" if len(seen) > 6 else "")) if seen \
            else "; CAT returned no cases at all"
        log(f"  ✗ CAT documents {ref}: case not found in the CAT tool after {client.search_attempts} "
            f"searches for {tried}{detail}")
        return {"status": "not_found", "case_ref": ref, "searched": client.last_search}
    detail = client.case_detail(row["id"])
    raw_docs = detail.get("documents")
    if not raw_docs:
        raw_docs = client.document_list(row["id"])
    docs = _doc_rows(raw_docs or [])
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
        zip_bytes, zip_name = client.download_zip(row["id"], expected=len(docs))
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
    try:
        request_gap = float(os.getenv("CAT_REQUEST_GAP_S") or 1.5)
    except ValueError:
        request_gap = 1.5
    try:
        attempts = int(os.getenv("CAT_SEARCH_ATTEMPTS") or 3)
    except ValueError:
        attempts = 3
    return CatWebClient(os.getenv("CAT_URL", ""), os.getenv("CAT_USERNAME") or os.getenv("CAT_EMAIL", ""),
                        os.getenv("CAT_PASSWORD", ""), os.getenv("CAT_API_URL"),
                        request_gap=min(max(request_gap, 0.0), 30.0),
                        case_gap=parse_gap(os.getenv("CAT_CASE_GAP_S"), (3.0, 5.0)),
                        search_attempts=min(max(attempts, 1), 6))


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
    for i, ref in enumerate(dict.fromkeys(clean_ref(c) for c in args.cases)):
        client.pause_case(print, f"before CAT documents {ref}" if i else "before the first case")
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
