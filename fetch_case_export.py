"""
Fetch case data from the CAT case export API (internal network).

    POST http://192.168.80.52:2510/api/case-export/   body: {"id": "<CAT case reference>"}

CAT data is kept completely separate from the audit tool data (audit_output/).
For each case it saves, in cat_output/cases/<case>_<name>/:
    case_export.json          - the full API response (source of truth)
    meta.json                 - _meta block (status, frozen, generated_at, schema)
    fields.json / fields.csv  - the 47 headline fields (label, value, section)
    tables.json + tables/*.csv- creditors, expenditure, bank audit, income, ...
    sections.json             - all detailed sections (creditors, benefits, IDU, UC, ...)
    case_profile.json         - applicants, household, debts, assets, IVA schedule
    income_calculation.json   - income calculation workings
    call_sheet_handover.json  - call sheet handover notes
    questions.json            - drafter questions
    documents.json / .csv     - documents on the case (id, file name, type, date, status)
    document_links.txt        - every http(s) link in the response (e.g. IDU report PDF)
    summary.txt               - quick human-readable overview

Cases already fetched are skipped (tracked in cat_output/manifest.json).
cat_output/index.csv lists every fetched case.

Usage:
    python fetch_case_export.py 435864 435898 436216
    python fetch_case_export.py --file cases.txt          # one case number per line
    python fetch_case_export.py 435864 --refetch          # fetch again even if done

The API URL can be changed with CASE_EXPORT_URL in .env.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_URL = "http://192.168.80.52:2510/api/case-export/"


def _safe(name) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", str(name or "unknown")).strip(" ._")
    return name[:80] or "unknown"


def _write_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def normalise_case(ref: str) -> str:
    """The API wants the plain CAT reference: '435864-W' -> '435864'."""
    ref = str(ref).strip()
    return re.sub(r"-[A-Za-z]+$", "", ref)


def fetch_case(session: requests.Session, url: str, case_ref: str) -> tuple[int, dict | None, str]:
    """Return (status_code, json_or_None, error_text). Retries on network/5xx errors."""
    for attempt in range(1, 5):
        try:
            r = session.post(url, json={"id": case_ref}, timeout=180)
        except requests.RequestException as exc:
            if attempt == 4:
                return 0, None, f"network error: {exc}"
            time.sleep(5 * attempt)
            continue
        if r.status_code == 429:
            time.sleep(int(float(r.headers.get("Retry-After", 30))))
            continue
        if r.status_code >= 500 and attempt < 4:
            time.sleep(5 * attempt)
            continue
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code == 200 and isinstance(body, dict):
            return 200, body, ""
        err = (body or {}).get("error") if isinstance(body, dict) else None
        return r.status_code, None, err or r.text[:300]
    return 0, None, "gave up after retries"


def find_links(obj, path="") -> list[tuple[str, str]]:
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += find_links(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += find_links(v, f"{path}[{i}]")
    elif isinstance(obj, str) and re.match(r"https?://", obj):
        out.append((path, obj))
    return out


def customer_name(data: dict) -> str:
    for f in data.get("fields") or []:
        if isinstance(f, dict) and f.get("field_id") == "full_name" and f.get("value"):
            return str(f["value"])
    try:
        dpa = data["case_profile"]["applicants"][0]["dpa"]
        if dpa.get("full_name"):
            return dpa["full_name"]
    except (KeyError, IndexError, TypeError):
        pass
    return "unknown"


def customer_dir(out_root: Path, case_ref: str, name: str) -> Path:
    """One folder per case; reuse an existing one for this case number."""
    base = out_root / "cases"
    if base.exists():
        for existing in sorted(base.glob(f"{_safe(case_ref)}_*")):
            if existing.is_dir():
                return existing
    return base / f"{_safe(case_ref)}_{_safe(name)}"


def summary_text(case_ref: str, data: dict, links) -> str:
    meta = data.get("_meta") or {}
    docs = data.get("documents") or []
    lines = [
        f"Case: {case_ref}",
        f"Customer: {customer_name(data)}",
        f"Status: {meta.get('status')}   Frozen: {meta.get('frozen')}   Generated: {meta.get('generated_at')}",
        f"Schema version: {meta.get('schema_version')}",
        "",
        f"Fields: {len(data.get('fields') or [])}   Tables: {len(data.get('tables') or [])}   "
        f"Sections: {len(data.get('sections') or {})}",
        "",
        f"== Documents ({len(docs)}) ==",
    ]
    for d in docs:
        lines.append(f"  [{d.get('document_type')}] {d.get('file_name')}  "
                     f"(uploaded {d.get('uploaded_at')}, {d.get('processing_status')}, id {d.get('document_id')})")
    lines += ["", f"== Links ({len(links)}) =="] + [f"  {p}: {u}" for p, u in links]
    tables = [t for t in data.get("tables") or [] if isinstance(t, dict) and t.get("label")]
    if tables:
        lines += ["", "== Tables =="]
    for t in tables:
        if True:
            extra = ""
            if "total_crm" in t or "total_verified" in t:
                extra = f"  CRM total {t.get('total_crm')}, verified total {t.get('total_verified')}"
            lines.append(f"  {t['label']}: {len(t.get('rows') or [])} rows{extra}")
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list, preferred: list[str] | None = None):
    rows = [r for r in rows if isinstance(r, dict)]
    cols = list(preferred or [])
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in r.items()})


def save_case(out_root: Path, case_ref: str, data: dict) -> dict:
    name = customer_name(data)
    folder = customer_dir(out_root, case_ref, name)
    folder.mkdir(parents=True, exist_ok=True)

    docs = data.get("documents") or []
    links = find_links(data)

    # Full response
    _write_json(folder / "case_export.json", data)

    # One file per top-level block
    blocks = {
        "_meta": "meta.json", "fields": "fields.json", "tables": "tables.json",
        "sections": "sections.json", "case_profile": "case_profile.json",
        "income_calculation": "income_calculation.json",
        "call_sheet_handover": "call_sheet_handover.json", "questions": "questions.json",
        "documents": "documents.json",
    }
    for key, fname in blocks.items():
        if key in data:
            _write_json(folder / fname, data[key])
    for key in data:                       # anything new the API adds later
        if key not in blocks:
            _write_json(folder / f"{_safe(key.lstrip('_'))}.json", data[key])

    # Spreadsheet versions
    _write_csv(folder / "fields.csv", data.get("fields") or [],
               ["section", "field_id", "label", "value", "type", "priority", "method"])
    _write_csv(folder / "documents.csv", docs,
               ["document_id", "file_name", "document_type", "belongs_to", "uploaded_at", "processing_status"])
    tables = [t for t in data.get("tables") or [] if isinstance(t, dict)]
    if tables:
        (folder / "tables").mkdir(exist_ok=True)
        for i, t in enumerate(tables, 1):
            _write_csv(folder / "tables" / f"{_safe(t.get('label') or t.get('section') or i)}.csv",
                       t.get("rows") or [])

    (folder / "document_links.txt").write_text(
        "\n".join(f"{p}\t{u}" for p, u in links) + ("\n" if links else ""), encoding="utf-8")
    (folder / "summary.txt").write_text(summary_text(case_ref, data, links), encoding="utf-8")

    meta = data.get("_meta") or {}
    return {
        "case_ref": case_ref,
        "customer_name": name,
        "status": meta.get("status"),
        "frozen": meta.get("frozen"),
        "generated_at": meta.get("generated_at"),
        "documents": len(docs),
        "failed_documents": sum(1 for d in docs if isinstance(d, dict) and d.get("processing_status") == "failed"),
        "links": len(links),
        "folder": str(folder.relative_to(out_root)),
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_index(out_root: Path, manifest: dict):
    _write_csv(out_root / "index.csv", sorted(manifest.values(), key=lambda r: r["case_ref"]),
               ["case_ref", "customer_name", "status", "frozen", "generated_at", "documents",
                "failed_documents", "links", "folder", "fetched_at"])


def main():
    p = argparse.ArgumentParser(description="Fetch CAT case export data for case numbers.")
    p.add_argument("cases", nargs="*", help="Case numbers, e.g. 435864 435898")
    p.add_argument("--file", help="Text file with one case number per line.")
    p.add_argument("--out", default=str(SCRIPT_DIR / "cat_output"), help="Output folder.")
    p.add_argument("--refetch", action="store_true", help="Fetch again even if already fetched.")
    args = p.parse_args()

    load_dotenv(SCRIPT_DIR / ".env")
    url = os.getenv("CASE_EXPORT_URL", DEFAULT_URL)

    refs = list(args.cases)
    if args.file:
        refs += [ln.strip() for ln in Path(args.file).read_text(encoding="utf-8").splitlines()]
    refs = list(dict.fromkeys(normalise_case(r) for r in refs if r and r.strip()))
    if not refs:
        sys.exit("Give at least one case number, e.g.: python fetch_case_export.py 435864")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest_path = out_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}

    session = requests.Session()
    done = skipped = 0
    failed: list[tuple[str, str]] = []
    print(f"Fetching {len(refs)} case(s) from {url} one at a time ...")
    for ref in refs:
        if ref in manifest and not args.refetch:
            print(f"  - {ref}: already fetched, skipping")
            skipped += 1
            continue
        status, data, err = fetch_case(session, url, ref)
        if data is None:
            print(f"  ✗ {ref}: failed (HTTP {status}) {err}")
            failed.append((ref, f"HTTP {status}: {err}"))
            continue
        entry = save_case(out_root, ref, data)
        manifest[ref] = entry
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        done += 1
        print(f"  ✓ {ref} - {entry['customer_name']}: {entry['documents']} documents, "
              f"{entry['links']} links -> {entry['folder']}")

    write_index(out_root, manifest)
    print(f"\nOutput: {out_root}")
    print(f"Done. Fetched {done}, skipped {skipped} already fetched, failed {len(failed)}.")
    for ref, err in failed:
        print(f"  failed: {ref} - {err}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
