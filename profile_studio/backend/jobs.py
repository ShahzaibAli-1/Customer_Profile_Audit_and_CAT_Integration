"""Background jobs (fetching, profile building) with progress and logs the UI can poll."""

from __future__ import annotations

import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import sources
import store
from pipeline import builder

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_fetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fetch")      # Audit API: one at a time
_profile_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="profile")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new(kind: str, title: str, refs: list[str], params: dict) -> dict:
    job = {"id": uuid.uuid4().hex[:10], "kind": kind, "title": title, "refs": refs, "params": params,
           "status": "queued", "progress": {"done": 0, "total": 0, "message": "Queued"},
           "logs": [], "created_at": _now(), "started_at": None, "finished_at": None,
           "result": None, "error": None, "cancel": False}
    with _lock:
        _jobs[job["id"]] = job
    return job


def _log(job: dict, msg: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    with _lock:
        job["logs"].append(f"{stamp}  {msg}")
        if len(job["logs"]) > 2000:
            job["logs"] = job["logs"][-2000:]


def _progress(job: dict, done: int, total: int, message: str) -> None:
    with _lock:
        job["progress"] = {"done": done, "total": total, "message": message}


def public(job: dict, with_logs: bool = True) -> dict:
    out = {k: v for k, v in job.items() if k != "cancel"}
    if not with_logs:
        out["logs"] = job["logs"][-3:]
    return out


def list_jobs() -> list[dict]:
    with _lock:
        jobs = sorted(_jobs.values(), key=lambda j: j["created_at"], reverse=True)
        return [public(j, with_logs=False) for j in jobs[:50]]


def get_job(job_id: str) -> dict | None:
    with _lock:
        j = _jobs.get(job_id)
        return public(j) if j else None


def cancel_job(job_id: str) -> bool:
    with _lock:
        j = _jobs.get(job_id)
        if not j or j["status"] not in ("queued", "running"):
            return False
        j["cancel"] = True
        if j["status"] == "queued":
            j["status"] = "cancelled"
        return True


def _run(job: dict, fn) -> None:
    with _lock:
        if job["status"] == "cancelled":
            return
        job["status"], job["started_at"] = "running", _now()
    try:
        job["result"] = fn(job)
        with _lock:
            job["status"] = "cancelled" if job["cancel"] else "done"
    except Exception as exc:  # noqa: BLE001
        _log(job, f"ERROR: {exc}")
        traceback.print_exc()
        with _lock:
            job["status"], job["error"] = "failed", str(exc)[:1000]
    finally:
        with _lock:
            job["finished_at"] = _now()


# ----------------------------------------------------------------- fetch job
def start_fetch(refs: list[str], start: date, end: date, include_cat: bool = True,
                build_profile: bool = False, refetch: bool = False, quality: str | None = None,
                anonymise: bool | None = None, include_documents: bool = True,
                include_audit: bool = True) -> dict:
    refs = list(dict.fromkeys(store.clean_ref(r) for r in refs if str(r).strip()))
    job = _new("fetch", f"Fetch {len(refs)} case(s)", refs,
               {"from": start.isoformat(), "to": end.isoformat(), "include_cat": include_cat,
                "include_documents": include_documents, "include_audit": include_audit,
                "build_profile": build_profile, "refetch": refetch})

    def work(job):
        log = lambda m: _log(job, m)  # noqa: E731
        _progress(job, 0, 1, "Looking up calls")
        found: dict = {}
        if include_audit:
            log(f"Looking up {', '.join(refs)} in the Audit tool between {start} and {end}")
            found = sources.find_calls_for_refs(refs, start, end, log)
        call_ids = []
        for ref in refs:
            calls = sorted(found.get(ref, []), key=lambda r: r.get("call_date") or "")
            if calls:
                log(f"  {ref}: {len(calls)} call(s) - " + ", ".join(
                    f"{c['call_id']} ({str(c.get('call_date'))[:10]}, {c.get('customer_name')})" for c in calls))
                call_ids += [c["call_id"] for c in calls]
            elif include_audit:
                log(f"  {ref}: no calls in the Audit tool between {start} and {end} - try a wider date range")
        total = len(call_ids) + (len(refs) if include_cat else 0) + (len(refs) if include_documents else 0)
        done = 0
        _progress(job, done, total, "Fetching calls")
        summary = {"calls_fetched": [], "calls_skipped": [], "calls_failed": [], "cat": {}, "documents": {},
                   "not_found": []}
        summary["not_found"] = [r for r in refs if include_audit and not found.get(r)]
        for cid in call_ids:
            if job["cancel"]:
                log("Cancelled.")
                return summary
            r = sources.fetch_audit_calls([cid], log, refetch=refetch)
            summary["calls_fetched"] += r["fetched"]
            summary["calls_skipped"] += r["skipped"]
            summary["calls_failed"] += r["failed"]
            done += 1
            _progress(job, done, total, f"Audit call {cid}")
        cancelled = lambda: bool(job["cancel"])  # noqa: E731
        cat_asked = False                       # CAT has been asked something: pause before the next ask
        if include_cat:
            for ref in refs:
                if job["cancel"]:
                    log("Cancelled.")
                    return summary
                if cat_asked:
                    sources.cat_pause(log, f"before CAT case {ref}", cancelled)
                cat_asked = True
                try:
                    summary["cat"][ref] = sources.fetch_cat_case(ref, log, refetch=refetch).get("status")
                except Exception as exc:  # noqa: BLE001
                    summary["cat"][ref] = "failed"
                    log(f"  ✗ CAT {ref}: {exc}")
                done += 1
                _progress(job, done, total, f"CAT case {ref}")
        if include_documents:
            if not sources.cat_docs_configured():
                log("  CAT documents skipped: set CAT_URL, CAT_USERNAME and CAT_PASSWORD in .env")
            else:
                for ref in refs:
                    if job["cancel"]:
                        log("Cancelled.")
                        return summary
                    if cat_asked:
                        sources.cat_pause(log, f"before CAT documents {ref}", cancelled)
                        if job["cancel"]:
                            log("Cancelled.")
                            return summary
                    cat_asked = True
                    _progress(job, done, total, f"Downloading CAT documents {ref}")
                    names = [c.get("customer_name") for c in found.get(ref, []) if c.get("customer_name")]
                    try:
                        r = sources.fetch_cat_documents(ref, log, refetch=refetch, names=names)
                        summary["documents"][ref] = r.get("status")
                    except Exception as exc:  # noqa: BLE001
                        summary["documents"][ref] = "failed"
                        log(f"  ✗ CAT documents {ref}: {exc}")
                    done += 1
                    _progress(job, done, total, f"CAT documents {ref}")
        log(f"Fetched {len(summary['calls_fetched'])} call(s), {len(summary['calls_skipped'])} already had, "
            f"{len(summary['calls_failed'])} failed.")
        if summary["documents"]:
            by = {}
            for ref, status in summary["documents"].items():
                by.setdefault(status, []).append(ref)
            words = {"fetched": "downloaded", "skipped": "unchanged", "not_found": "not found", "failed": "failed"}
            log("CAT documents: " + ", ".join(f"{len(v)} {words.get(k, k)}" + (f" ({', '.join(v)})" if k in ("not_found", "failed") else "")
                                             for k, v in by.items()) + ".")
        if build_profile:
            for ref in refs:
                if store.call_dirs(ref):
                    pj = start_profile(ref, quality=quality, anonymise=anonymise)
                    log(f"Queued profile build for {ref} (job {pj['id']})")
        return summary

    _fetch_pool.submit(_run, job, work)
    return public(job)


# --------------------------------------------------------------- profile job
def start_profile(ref: str, quality: str | None = None, anonymise: bool | None = None,
                  persona_only: bool = False) -> dict:
    ref = store.clean_ref(ref)
    title = f"{'Rebuild profile only' if persona_only else 'Build profile'} {ref}"
    job = _new("profile", title, [ref], {"quality": quality, "anonymise": anonymise, "persona_only": persona_only})
    meta_path = store.profile_dir(ref) / "meta.json"
    meta = store.read_json(meta_path, {}) or {}
    if meta.get("status") != "running":
        store.write_json(meta_path, {**meta, "ref": ref, "status": "queued", "error": None})

    def work(job):
        return builder.build_profile(ref, store.get_settings(), lambda m: _log(job, m),
                                     lambda d, t, m: _progress(job, d, t, m),
                                     anonymise=anonymise, quality=quality, persona_only=persona_only)

    _profile_pool.submit(_run, job, work)
    return public(job)
