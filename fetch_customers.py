"""
Fetch SFM customer calls from the Sp4rk case export API.

For every call it saves:  transcript, audio file, full compliance/audit report,
CRM results and the SFM case record.

Calls that were already fetched are skipped (tracked in audit_output/manifest.json),
so you can re-run the script as often as you like and only new calls are pulled.

Usage:
    python fetch_customers.py --from 2026-09-01 --to 2026-09-30
    python fetch_customers.py                       # defaults to the last 7 days
    python fetch_customers.py --from 2026-09-01 --to 2026-09-30 --audio   # include recordings
    python fetch_customers.py --from 2026-09-01 --to 2026-09-01 --limit 5
    python fetch_customers.py --from 2026-09-01 --to 2026-09-30 --customers 10
    python fetch_customers.py --from 2026-09-01 --to 2026-11-23 --case 433597

Requires AUDIT_API_KEY in .env (never hard-code the key).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import load_dotenv

BASE_URL = "https://sp4rk.co.uk/api/export"
DEFAULT_DEPARTMENT_ID = 8          # SFM
MAX_DAYS_PER_LIST_CALL = 92
SCRIPT_DIR = Path(__file__).resolve().parent


class DailyLimitReached(Exception):
    pass


# --------------------------------------------------------------------------- #
# HTTP helpers
# --------------------------------------------------------------------------- #
class ApiClient:
    def __init__(self, api_key: str):
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Accept": "application/json"})

    def _request(self, method: str, path: str, max_5xx_retries: int = 4, **kwargs) -> dict:
        url = f"{BASE_URL}{path}"
        attempts = 0
        while True:
            attempts += 1
            try:
                resp = self.session.request(method, url, timeout=180, **kwargs)
            except requests.RequestException as exc:
                if attempts >= 5:
                    raise
                wait = 10 * attempts
                print(f"  network error ({exc}); retrying in {wait}s")
                time.sleep(wait)
                continue

            body_text = resp.text or ""
            if "daily_limit_reached" in body_text:
                raise DailyLimitReached(body_text[:500])

            if resp.status_code == 429:
                wait = _retry_after_seconds(resp)
                print(f"  server busy (429) - waiting {wait}s as instructed")
                time.sleep(wait)
                continue

            if resp.status_code >= 500 and attempts <= max_5xx_retries:
                wait = 15 * attempts
                print(f"  server error {resp.status_code}; retrying in {wait}s")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            payload = resp.json()
            if isinstance(payload, dict) and payload.get("success") is False:
                raise RuntimeError(f"API error: {payload.get('error')}")

            # Be polite: if we've used up the per-window quota, wait for reset.
            remaining = resp.headers.get("RateLimit-Remaining")
            reset = resp.headers.get("RateLimit-Reset")
            if remaining is not None and reset is not None:
                try:
                    if int(remaining) <= 0:
                        wait = int(reset) + 1
                        print(f"  rate-limit window used up - pausing {wait}s")
                        time.sleep(wait)
                except ValueError:
                    pass
            return payload

    def list_cases(self, department_id: int, start: date, end: date):
        """Yield (case, max_batch) for every case in the date range."""
        for chunk_start, chunk_end in _date_chunks(start, end):
            cursor = None
            while True:
                params = {
                    "department_id": department_id,
                    "from": chunk_start.isoformat(),
                    "to": chunk_end.isoformat(),
                }
                if cursor:
                    params["cursor"] = cursor
                data = self._request("GET", "/cases", params=params)["data"]
                max_batch = int(data.get("max_batch") or 25)
                for case in data.get("cases", []):
                    yield case, max_batch
                cursor = data.get("next_cursor")
                if not cursor:
                    break

    def get_details(self, call_ids: list[int]) -> list[dict]:
        """Fetch details. If the server fails on a multi-id batch ("export failed"),
        split it in half and retry, down to single calls."""
        try:
            data = self._request("POST", "/cases/details",
                                 max_5xx_retries=1 if len(call_ids) > 1 else 3,
                                 json={"call_ids": call_ids})["data"]
            return data.get("calls", [])
        except (requests.HTTPError, RuntimeError) as exc:
            if len(call_ids) == 1:
                print(f"  ! call {call_ids[0]} failed: {exc}")
                return []
            mid = len(call_ids) // 2
            print(f"  batch of {len(call_ids)} failed - splitting into {mid} + {len(call_ids) - mid}")
            return self.get_details(call_ids[:mid]) + self.get_details(call_ids[mid:])


def _retry_after_seconds(resp: requests.Response) -> int:
    for header in ("Retry-After", "RateLimit-Reset"):
        value = resp.headers.get(header)
        if value:
            try:
                return max(1, int(float(value)))
            except ValueError:
                pass
    return 60


def _date_chunks(start: date, end: date):
    cur = start
    while cur <= end:
        chunk_end = min(cur + timedelta(days=MAX_DAYS_PER_LIST_CALL - 1), end)
        yield cur, chunk_end
        cur = chunk_end + timedelta(days=1)


# --------------------------------------------------------------------------- #
# Manifest (tracks what has already been fetched)
# --------------------------------------------------------------------------- #
class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"calls": {}}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
            self.data.setdefault("calls", {})

    def is_done(self, call_id) -> bool:
        entry = self.data["calls"].get(str(call_id))
        return bool(entry and entry.get("complete"))

    def record(self, call_id, entry: dict):
        self.data["calls"][str(call_id)] = entry
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


# --------------------------------------------------------------------------- #
# Saving a call
# --------------------------------------------------------------------------- #
def _safe(name: str) -> str:
    name = re.sub(r"[^\w\-. ]+", "_", str(name or "unknown")).strip(" ._")
    return name[:80] or "unknown"


def _write_json(path: Path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def _transcript_text(transcript) -> str:
    if not transcript:
        return ""
    lines = []
    for seg in transcript:
        if isinstance(seg, dict):
            ts = seg.get("timestamp", "")
            spk = seg.get("speaker", "?")
            lines.append(f"[{ts}] Speaker {spk}: {seg.get('text', '')}")
        else:
            lines.append(str(seg))
    return "\n".join(lines)


def _report_text(report) -> str:
    """Human-readable audit summary from the compliance report."""
    if not isinstance(report, dict):
        return ""
    out = [f"Source: {report.get('source')}"]
    totals = report.get("totals")
    if totals:
        out.append("\n== Totals ==")
        for k, v in totals.items():
            out.append(f"{k}: {v}")
    submitted = report.get("submitted")
    if submitted:
        out.append("\n== Submitted ==")
        for k, v in submitted.items():
            out.append(f"{k}: {v}")
    questions = report.get("questions") or []
    out.append(f"\n== Questions ({len(questions)}) ==")
    for q in questions:
        if not isinstance(q, dict):
            out.append(str(q))
            continue
        qid = q.get("question_id") or q.get("id") or ""
        label = q.get("question_label") or q.get("question") or q.get("label") or ""
        status = q.get("status", "")
        out.append(f"\n[{qid}] {label}\n  Status: {status}")
        for key in ("reasoning", "evidence"):
            if q.get(key):
                val = q[key]
                if not isinstance(val, str):
                    val = json.dumps(val, ensure_ascii=False)
                out.append(f"  {key.capitalize()}: {val}")
    return "\n".join(out)


def _download_audio(session: requests.Session, url: str, dest_dir: Path, attempts: int = 6) -> Path:
    """Download audio, resuming from where it stopped if the connection drops."""
    ext = Path(urlparse(url).path).suffix or ".mp3"
    dest = dest_dir / f"audio{ext}"
    tmp = dest.with_suffix(dest.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    for attempt in range(1, attempts + 1):
        have = tmp.stat().st_size if tmp.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with session.get(url, stream=True, timeout=(30, 120), headers=headers) as r:
                if r.status_code == 416:          # already have the whole file
                    break
                r.raise_for_status()
                mode = "ab" if (have and r.status_code == 206) else "wb"
                with open(tmp, mode) as fh:
                    for chunk in r.iter_content(chunk_size=1 << 16):
                        fh.write(chunk)
            break
        except requests.HTTPError:
            raise                                  # e.g. 403 = link expired -> caller refreshes link
        except requests.RequestException as exc:
            if attempt == attempts:
                raise
            size_mb = (tmp.stat().st_size if tmp.exists() else 0) / 1e6
            print(f"    audio connection dropped at {size_mb:.0f} MB ({exc.__class__.__name__}); "
                  f"resuming (attempt {attempt + 1}/{attempts})")
            time.sleep(5 * attempt)
    os.replace(tmp, dest)
    return dest


def customer_ref(info: dict) -> str:
    """Stable customer number. case_id can carry suffixes like '435898-W' for a second
    call on the same customer, so prefer the Aryza customer ref / case_key."""
    for value in (info.get("aryza_customer_ref"), str(info.get("case_key") or "").split("::")[0],
                  info.get("client_id"), info.get("case_id")):
        if value and str(value).strip() and str(value) != "None":
            return str(value).strip()
    return "unknown"


def customer_dir(out_root: Path, info: dict) -> Path:
    """One folder per customer; reuse an existing folder for this ref even if the
    name is spelt differently on another call."""
    base = out_root / "customers"
    ref = _safe(customer_ref(info))
    if base.exists():
        for existing in sorted(base.glob(f"{ref}_*")):
            if existing.is_dir():
                return existing
    return base / f"{ref}_{_safe(info.get('customer_name'))}"


def save_call(call: dict, out_root: Path, download_audio: bool, audio_session: requests.Session) -> dict:
    info = call.get("call") or {}
    call_id = call.get("call_id") or info.get("call_id")
    call_dir = customer_dir(out_root, info) / f"call_{call_id}"
    call_dir.mkdir(parents=True, exist_ok=True)

    # Full raw payload (everything the API returned) - the source of truth.
    _write_json(call_dir / "details.json", call)

    # Convenient split-out files.
    _write_json(call_dir / "case_record.json", info)
    if call.get("transcript") is not None:
        (call_dir / "transcript.txt").write_text(_transcript_text(call["transcript"]), encoding="utf-8")
    if call.get("report") is not None:
        _write_json(call_dir / "audit_report.json", call["report"])
        (call_dir / "audit_report.txt").write_text(_report_text(call["report"]), encoding="utf-8")
    if call.get("crm") is not None:
        _write_json(call_dir / "crm.json", call["crm"])

    errors = call.get("errors") or []
    if errors:
        _write_json(call_dir / "errors.json", errors)

    audio_status = "skipped"
    audio_url = (call.get("audio") or {}).get("url")
    if download_audio:
        if audio_url:
            try:
                _download_audio(audio_session, audio_url, call_dir)
                audio_status = "downloaded"
            except requests.RequestException as exc:
                audio_status = f"failed: {exc}"
        else:
            audio_status = "missing"   # API reported no audio for this call

    # A call is "complete" unless the audio download failed (then we re-request it next run
    # to get a fresh link, since links expire after 2 hours).
    complete = not audio_status.startswith("failed")
    return {
        "call_id": call_id,
        "case_id": info.get("case_id"),
        "customer_ref": customer_ref(info),
        "customer_name": info.get("customer_name"),
        "advisor": (info.get("advisor") or {}).get("name"),
        "call_date": info.get("call_date"),
        "chosen_solution": info.get("chosen_solution"),
        "average_score": info.get("average_score"),
        "folder": str(call_dir.relative_to(out_root)),
        "audio": audio_status,
        "errors": errors,
        "complete": complete,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_index_csv(manifest: Manifest, out_root: Path):
    rows = list(manifest.data["calls"].values())
    rows.sort(key=lambda r: (r.get("call_date") or "", r.get("call_id") or 0))
    fields = ["call_id", "customer_ref", "case_id", "customer_name", "advisor", "call_date",
              "chosen_solution", "average_score", "audio", "complete", "folder", "fetched_at"]
    with open(out_root / "index.csv", "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args():
    today = datetime.now(timezone.utc).date()
    p = argparse.ArgumentParser(description="Fetch SFM customer calls (transcript, audio, audit, CRM).")
    p.add_argument("--from", dest="start", default=(today - timedelta(days=7)).isoformat(),
                   help="Start date YYYY-MM-DD (UTC). Default: 7 days ago.")
    p.add_argument("--to", dest="end", default=today.isoformat(),
                   help="End date YYYY-MM-DD (UTC). Default: today.")
    p.add_argument("--department", type=int, default=DEFAULT_DEPARTMENT_ID, help="Department id (SFM = 8).")
    p.add_argument("--out", default=str(SCRIPT_DIR / "audit_output"), help="Output folder.")
    p.add_argument("--audio", action="store_true",
                   help="Also download the call audio (off by default - files are large and slow).")
    p.add_argument("--no-audio", action="store_true", help=argparse.SUPPRESS)  # kept for old commands
    p.add_argument("--case", dest="cases", nargs="+", default=[],
                   help="Only fetch these case number(s), e.g. --case 433597 402568. "
                        "Searched within the --from/--to date range.")
    p.add_argument("--customers", type=int, default=0,
                   help="Only fetch this many customers in total (0 = all). All of each "
                        "chosen customer's not-yet-fetched calls in the date range are fetched.")
    p.add_argument("--limit", type=int, default=0, help="Only fetch this many new calls (0 = all).")
    p.add_argument("--batch-size", type=int, default=1,
                   help="Calls per details request. Default 1 (one by one) because the API currently "
                        "fails on multi-call requests. Capped at the API's max_batch.")
    p.add_argument("--refetch", action="store_true", help="Ignore the manifest and fetch everything again.")
    return p.parse_args()


def main():
    args = parse_args()
    load_dotenv(SCRIPT_DIR / ".env")
    api_key = os.getenv("AUDIT_API_KEY")
    if not api_key:
        sys.exit("AUDIT_API_KEY is not set (put it in .env).")

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        sys.exit("--to must be on or after --from")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(out_root / "manifest.json")
    client = ApiClient(api_key)
    audio_session = requests.Session()   # no API key sent to S3

    print(f"Listing department {args.department} cases {start} -> {end} ...")
    wanted_cases = {c.strip() for c in args.cases}
    if wanted_cases:
        print(f"Looking for case number(s): {', '.join(sorted(wanted_cases))}")
    pending: list[int] = []
    customer_of: dict[int, str] = {}
    seen: set[int] = set()
    already = 0
    max_batch = 25
    try:
        for case, mb in client.list_cases(args.department, start, end):
            max_batch = mb
            cid = case.get("call_id")
            if cid is None or cid in seen:
                continue
            if wanted_cases and not wanted_cases & (
                {str(case.get(f)).strip() for f in ("case_id", "aryza_customer_ref", "client_id")}
                | {str(case.get("case_key") or "").split("::")[0]}
            ):
                continue
            seen.add(cid)
            if not args.refetch and manifest.is_done(cid):
                already += 1
                continue
            pending.append(cid)
            customer_of[cid] = customer_ref(case)
    except DailyLimitReached as exc:
        print(f"Daily limit reached while listing - stopping. ({exc})")
        if not pending:
            return

    customers_with_new = list(dict.fromkeys(customer_of[c] for c in pending))
    if args.customers:
        chosen = set(customers_with_new[: args.customers])
        pending = [c for c in pending if customer_of[c] in chosen]
    if args.limit:
        pending = pending[: args.limit]
    n_customers = len({customer_of[c] for c in pending})

    print(f"Found {len(seen)} calls: {already} already fetched, {len(pending)} to fetch "
          f"across {n_customers} customer(s) "
          f"({len(customers_with_new)} customer(s) have new calls).")
    batch_size = max(1, min(args.batch_size, max_batch))
    print(f"Fetching {'one call at a time' if batch_size == 1 else f'{batch_size} calls per request'}.")

    if wanted_cases and not seen:
        print(f"No calls found for case(s) {', '.join(sorted(wanted_cases))} between {start} and {end}. "
              f"Try a wider --from/--to range.")
    fetched = failed_audio = 0
    try:
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            print(f"Fetching details {i + 1}-{i + len(batch)} of {len(pending)} ...")
            calls = client.get_details(batch)
            returned = set()
            for call in calls:
                entry = save_call(call, out_root, args.audio, audio_session)
                if not entry["complete"]:
                    # Audio link probably expired (2h lifetime) - get a fresh one and retry once.
                    print(f"  audio failed for call {entry['call_id']} ({entry['audio']}); refreshing link")
                    fresh = client.get_details([entry["call_id"]])
                    if fresh:
                        entry = save_call(fresh[0], out_root, args.audio, audio_session)
                returned.add(entry["call_id"])
                manifest.record(entry["call_id"], entry)
                fetched += 1
                if not entry["complete"]:
                    failed_audio += 1
                note = f" errors={entry['errors']}" if entry["errors"] else ""
                print(f"  ✓ call {entry['call_id']} - {entry['customer_name']} "
                      f"(audio: {entry['audio']}){note}")
            missing = set(batch) - returned
            if missing:
                print(f"  ! API returned nothing for call ids: {sorted(missing)}")
    except DailyLimitReached as exc:
        print(f"Daily limit reached - stopping. Re-run tomorrow to continue. ({exc})")
    finally:
        write_index_csv(manifest, out_root)

    print(f"\nDone. Fetched {fetched} new call(s); skipped {already} already fetched.")
    if failed_audio:
        print(f"{failed_audio} audio download(s) failed - re-run to retry them with fresh links.")
    print(f"Output: {out_root}")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
