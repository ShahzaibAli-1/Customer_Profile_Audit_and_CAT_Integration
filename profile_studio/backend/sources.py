"""Fetching from the Audit tool and the CAT tool.

Reuses fetch_customers.py / fetch_case_export.py (one folder up) so the files on
disk are exactly what those scripts produce.

The Audit API can only list calls by date, and listing costs rows from a 25,000
a day allowance. So every listing is cached in audit_output/case_index.json and a
day is only listed again if it is recent (calls may still be arriving).
"""

from __future__ import annotations

import os
import sys
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import requests
from dotenv import load_dotenv

from store import AUDIT_DIR, CAT_DIR, ROOT, clean_ref, read_json, write_json

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fetch_case_export as cat_mod   # noqa: E402
import fetch_cat_documents as docs_mod   # noqa: E402
import fetch_customers as audit_mod   # noqa: E402

load_dotenv(ROOT / ".env")

INDEX_PATH = AUDIT_DIR / "case_index.json"
REFRESH_RECENT_DAYS = 2          # always re-list today and yesterday
_audit_lock = threading.Lock()   # the Audit API is rate limited: one user of it at a time
_index_lock = threading.Lock()

Log = Callable[[str], None]


class DailyLimitReached(Exception):
    pass


def _env() -> None:
    """Re-read .env so keys added while the server runs are picked up."""
    load_dotenv(ROOT / ".env", override=True)


def audit_key_present() -> bool:
    _env()
    return bool(os.getenv("AUDIT_API_KEY"))


def _client() -> audit_mod.ApiClient:
    _env()
    key = os.getenv("AUDIT_API_KEY")
    if not key:
        raise RuntimeError("AUDIT_API_KEY is not set in .env")
    return audit_mod.ApiClient(key)


# ------------------------------------------------------------------ index
def _load_index() -> dict:
    idx = read_json(INDEX_PATH, None) or {}
    idx.setdefault("days", {})
    idx.setdefault("calls", {})
    return idx


def _row(case: dict) -> dict:
    return {
        "call_id": case.get("call_id"),
        "case_id": case.get("case_id"),
        "customer_ref": audit_mod.customer_ref(case),
        "customer_name": (case.get("customer_name") or "").strip(),
        "advisor": (case.get("advisor") or {}).get("name"),
        "call_date": case.get("call_date"),
        "call_duration": case.get("call_duration"),
        "chosen_solution": case.get("chosen_solution"),
        "average_score": case.get("average_score"),
        "has_submitted_report": case.get("has_submitted_report"),
    }


def _ranges(days: list[date]) -> list[tuple[date, date]]:
    """Group sorted days into contiguous ranges."""
    out: list[tuple[date, date]] = []
    for d in sorted(days):
        if out and d == out[-1][1] + timedelta(days=1):
            out[-1] = (out[-1][0], d)
        else:
            out.append((d, d))
    return out


def ensure_index(start: date, end: date, log: Log = print, department: int = 8) -> None:
    """Make sure every day in [start, end] has been listed (recent days re-listed)."""
    today = datetime.now(timezone.utc).date()
    end = min(end, today)
    if end < start:
        return
    with _index_lock:
        idx = _load_index()
    need = []
    d = start
    while d <= end:
        if d.isoformat() not in idx["days"] or (today - d).days < REFRESH_RECENT_DAYS:
            need.append(d)
        d += timedelta(days=1)
    if not need:
        return
    client = _client()
    with _audit_lock:
        for a, b in _ranges(need):
            log(f"Listing Audit calls {a} -> {b} ...")
            n = 0
            try:
                rows = [_row(c) for c, _ in client.list_cases(department, a, b)]
            except audit_mod.DailyLimitReached as exc:
                raise DailyLimitReached(str(exc)) from exc
            with _index_lock:
                idx = _load_index()
                for r in rows:
                    if r["call_id"] is not None:
                        idx["calls"][str(r["call_id"])] = r
                        n += 1
                day = a
                stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
                while day <= b:
                    idx["days"][day.isoformat()] = stamp
                    day += timedelta(days=1)
                write_json(INDEX_PATH, idx)
            log(f"  {n} calls listed")


def indexed_calls(start: date | None = None, end: date | None = None) -> list[dict]:
    idx = _load_index()
    rows = list(idx["calls"].values())
    if start or end:
        s = (start or date.min).isoformat()
        e = (end or date.max).isoformat() + "T99"
        rows = [r for r in rows if s <= (r.get("call_date") or "") <= e]
    return rows


def fetched_call_ids() -> set[str]:
    m = read_json(AUDIT_DIR / "manifest.json", {}) or {}
    return {cid for cid, e in (m.get("calls") or {}).items() if e.get("complete")}


def find_calls_for_refs(refs: list[str], start: date, end: date, log: Log = print) -> dict[str, list[dict]]:
    ensure_index(start, end, log)
    wanted = {clean_ref(r) for r in refs}
    out: dict[str, list[dict]] = {r: [] for r in wanted}
    for r in indexed_calls(start, end):
        keys = {clean_ref(str(r.get("customer_ref") or "")), clean_ref(str(r.get("case_id") or ""))}
        for ref in wanted & keys:
            out[ref].append(r)
    return out


# ------------------------------------------------------------------ fetching
def fetch_audit_calls(call_ids: list[int], log: Log = print, refetch: bool = False) -> dict:
    """Fetch details (transcript, audit report, CRM, case record) one call at a time. No audio."""
    out_root = AUDIT_DIR
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = audit_mod.Manifest(out_root / "manifest.json")
    client = _client()
    audio_session = requests.Session()
    done, skipped, failed = [], [], []
    with _audit_lock:
        try:
            for cid in call_ids:
                if not refetch and manifest.is_done(cid):
                    skipped.append(cid)
                    log(f"  call {cid}: already fetched")
                    continue
                calls = client.get_details([cid])
                if not calls:
                    failed.append(cid)
                    log(f"  call {cid}: API returned nothing")
                    continue
                entry = audit_mod.save_call(calls[0], out_root, False, audio_session)
                manifest.record(entry["call_id"], entry)
                done.append(cid)
                errs = f" (API notes missing parts: {entry['errors']})" if entry["errors"] else ""
                log(f"  ✓ call {cid} - {entry['customer_name']}{errs}")
        except audit_mod.DailyLimitReached as exc:
            raise DailyLimitReached(str(exc)) from exc
        finally:
            audit_mod.write_index_csv(manifest, out_root)
    return {"fetched": done, "skipped": skipped, "failed": failed}


def cat_url() -> str:
    return os.getenv("CASE_EXPORT_URL", cat_mod.DEFAULT_URL)


def fetch_cat_case(ref: str, log: Log = print, refetch: bool = False) -> dict:
    ref = clean_ref(ref)
    CAT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = CAT_DIR / "manifest.json"
    manifest = read_json(manifest_path, {}) or {}
    if ref in manifest and not refetch:
        log(f"  CAT {ref}: already fetched")
        return {**manifest[ref], "status": "skipped"}
    status, data, err = cat_mod.fetch_case(requests.Session(), cat_url(), ref)
    if data is None:
        log(f"  ✗ CAT {ref}: HTTP {status} {err}")
        return {"status": "failed", "error": f"HTTP {status}: {err}"}
    entry = cat_mod.save_case(CAT_DIR, ref, data)
    manifest = read_json(manifest_path, {}) or {}
    manifest[ref] = entry
    write_json(manifest_path, manifest)
    cat_mod.write_index(CAT_DIR, manifest)
    log(f"  ✓ CAT {ref} - {entry['customer_name']}: {entry['documents']} documents")
    return {**entry, "status": "fetched"}


_docs_client = None
_docs_lock = threading.Lock()


def cat_docs_configured() -> bool:
    _env()
    return bool(os.getenv("CAT_URL") and (os.getenv("CAT_USERNAME") or os.getenv("CAT_EMAIL")) and os.getenv("CAT_PASSWORD"))


def fetch_cat_documents(ref: str, log: Log = print, refetch: bool = False) -> dict:
    """Sign in to the CAT web app and 'Download all' documents for the case."""
    global _docs_client
    with _docs_lock:
        if _docs_client is None:
            _docs_client = docs_mod.client_from_env()
        try:
            return docs_mod.fetch_documents(_docs_client, ref, CAT_DIR, refetch, log)
        except docs_mod.CatDocumentsError:
            _docs_client = None          # drop the session so the next try signs in fresh
            raise


def cat_reachable() -> bool:
    try:
        r = requests.options(cat_url(), timeout=3)
        return r.status_code < 500
    except requests.RequestException:
        return False
