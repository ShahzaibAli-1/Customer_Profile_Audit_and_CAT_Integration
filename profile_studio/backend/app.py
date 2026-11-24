"""Customer Profile Studio - API server.

Run:  python -m uvicorn app:app --port 8600   (from profile_studio/backend)
or:   ..\\start.ps1
"""

from __future__ import annotations

import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import jobs
import sources
import store
from pipeline import llm
from pipeline.stages import STAGES
from pipeline.transcript import ADVISOR, build_lines, clock, guess_roles

app = FastAPI(title="Customer Profile Studio")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _reset_stale() -> None:
    """A restart kills running builds; don't leave them looking 'running' forever."""
    if store.PROFILES_DIR.exists():
        for p in store.PROFILES_DIR.iterdir():
            meta = store.read_json(p / "meta.json", None)
            if meta and meta.get("status") in ("running", "queued"):
                meta["status"] = "interrupted"
                meta["error"] = "The server restarted while this profile was being built. Build it again."
                store.write_json(p / "meta.json", meta)


def _date(s: str | None, default: date) -> date:
    if not s:
        return default
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise HTTPException(400, f"Bad date: {s} (use YYYY-MM-DD)")


def _today() -> date:
    return datetime.now(timezone.utc).date()


# --------------------------------------------------------------------- health
@app.get("/api/health")
def health():
    return {
        "audit_key": sources.audit_key_present(),
        "openai_key": llm.key_present(),
        "cat_reachable": sources.cat_reachable(),
        "cat_url": sources.cat_url(),
        "cat_docs_configured": sources.cat_docs_configured(),
        "agent_personas_path": store.get_settings()["agent_personas_path"],
        "agent_personas_exists": Path(store.get_settings()["agent_personas_path"]).exists(),
    }


@app.get("/api/stages")
def stages():
    return STAGES


# ------------------------------------------------------------------- settings
@app.get("/api/settings")
def get_settings():
    return store.get_settings()


@app.put("/api/settings")
def put_settings(update: dict = Body(...)):
    return store.save_settings(update)


@app.get("/api/models")
def models():
    if not llm.key_present():
        return {"models": [], "error": "OPENAI_API_KEY not set"}
    return {"models": llm.list_models()}


# -------------------------------------------------------------------- browse
@app.get("/api/audit/cases")
def browse_audit(start: str | None = None, end: str | None = None, q: str = ""):
    """Customers with calls in the Audit tool for a date range (cached listing)."""
    end_d = _date(end, _today())
    start_d = _date(start, end_d - timedelta(days=6))
    if (end_d - start_d).days > 120:
        raise HTTPException(400, "Pick at most 120 days at a time.")
    logs: list[str] = []
    try:
        sources.ensure_index(start_d, end_d, logs.append)
    except sources.DailyLimitReached:
        raise HTTPException(429, "The Audit tool's daily limit has been reached. Try again tomorrow.")
    rows = sources.indexed_calls(start_d, end_d)
    fetched = sources.fetched_call_ids()
    local = {c["ref"]: c for c in store.list_local_cases()}
    groups: dict[str, dict] = defaultdict(lambda: {"calls": []})
    for r in rows:
        ref = store.clean_ref(str(r.get("customer_ref") or r.get("case_id") or ""))
        g = groups[ref]
        g["ref"] = ref
        g.setdefault("name", r.get("customer_name"))
        g["calls"].append({**r, "fetched": str(r["call_id"]) in fetched})
    out = []
    ql = q.strip().lower()
    for ref, g in groups.items():
        g["calls"].sort(key=lambda c: c.get("call_date") or "")
        g["last_call"] = g["calls"][-1].get("call_date")
        g["advisor"] = g["calls"][-1].get("advisor")
        g["all_fetched"] = all(c["fetched"] for c in g["calls"])
        g["has_cat"] = bool(local.get(ref, {}).get("has_cat"))
        g["profile"] = (local.get(ref) or {}).get("profile", {"status": "none"})
        if ql and ql not in ref.lower() and ql not in (g.get("name") or "").lower() \
                and ql not in (g.get("advisor") or "").lower():
            continue
        out.append(g)
    out.sort(key=lambda g: g["last_call"] or "", reverse=True)
    return {"start": start_d.isoformat(), "end": end_d.isoformat(), "customers": out, "log": logs}


# --------------------------------------------------------------------- local
@app.get("/api/cases")
def cases():
    return store.list_local_cases()


@app.get("/api/cases/{ref}")
def case_detail(ref: str):
    ref = store.clean_ref(ref)
    calls = store.calls_for_case(ref)
    cat = store.load_cat(ref)
    docs = store.documents_entry(ref)
    if not calls and not cat and not docs and not store.profile_dir(ref).exists():
        raise HTTPException(404, "Case not found locally. Fetch it first.")
    cat_summary = None
    if cat:
        meta = cat.get("_meta") or {}
        cat_summary = {
            "status": meta.get("status"), "frozen": meta.get("frozen"), "generated_at": meta.get("generated_at"),
            "fields": cat.get("fields") or [],
            "tables": [{"label": t.get("label"), "section": t.get("section"), "rows": len(t.get("rows") or []),
                        "total_crm": t.get("total_crm"), "total_verified": t.get("total_verified")}
                       for t in cat.get("tables") or [] if isinstance(t, dict)],
            "documents": cat.get("documents") or [],
        }
    name = (cat_summary and next((f.get("value") for f in cat_summary["fields"]
                                  if f.get("field_id") == "full_name" and f.get("value")), None)) \
        or (calls[0]["customer_name"] if calls else "") or (docs or {}).get("customer_name", "")
    docs_summary = None
    if docs:
        docs_summary = {k: docs.get(k) for k in ("files", "documents", "failed_processing", "fetched_at",
                                                  "cat_status", "phase", "application_id")}
    return {"ref": ref, "name": name, "calls": calls, "cat": cat_summary, "documents": docs_summary,
            "profile": store.profile_status(ref)}


@app.get("/api/cases/{ref}/documents")
def case_documents(ref: str):
    """Documents downloaded from the CAT tool ('Download all'), with their list metadata."""
    ref = store.clean_ref(ref)
    entry = store.documents_entry(ref)
    ddir = store.documents_dir(ref)
    if not entry or not ddir:
        return {"downloaded": False, "files": [], "documents": []}
    meta = store.read_json(ddir / "documents.json", []) or []
    by_name = {d.get("file_name"): d for d in meta}
    files = []
    for f in entry.get("file_list") or []:
        name = f["path"].split("/")[-1]
        d = by_name.get(name, {})
        files.append({**f, "name": name, "folder": "/".join(f["path"].split("/")[:-1]),
                      "document_type": d.get("document_type"), "processing_status": d.get("processing_status"),
                      "upload_date": d.get("upload_date"), "mime_type": d.get("mime_type")})
    return {"downloaded": True, "fetched_at": entry.get("fetched_at"), "folder": str(ddir),
            "files": files, "documents": meta, "application_id": entry.get("application_id")}


@app.get("/api/cases/{ref}/documents/file")
def case_document_file(ref: str, path: str, download: bool = False):
    ddir = store.documents_dir(store.clean_ref(ref))
    if not ddir:
        raise HTTPException(404, "No documents downloaded for this case")
    target = (ddir / path).resolve()
    if ddir.resolve() not in target.parents or not target.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(target, filename=target.name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/cases/{ref}/cat")
def case_cat(ref: str):
    data = store.load_cat(store.clean_ref(ref))
    if not data:
        raise HTTPException(404, "No CAT data for this case")
    return data


def _stage_tags(conv: dict) -> dict[int, list[int]]:
    tag: dict[int, list[int]] = defaultdict(list)
    for n, st in (conv.get("stages") or {}).items():
        for a, b in st.get("spans") or []:
            for i in range(a, b + 1):
                tag[i].append(int(n))
    return tag


def _non_customer(conv: dict) -> set[int]:
    nc = set()
    for sp in conv.get("non_customer_spans") or []:
        nc.update(range(sp.get("start", 0), sp.get("end", -1) + 1))
    return nc


@app.get("/api/cases/{ref}/conversation")
def conversation(ref: str):
    """All of a case's calls merged into one ordered conversation (duplicates removed), with stage tags."""
    conv = store.read_json(store.profile_dir(store.clean_ref(ref)) / "conversation.json", None)
    if not conv:
        return {"available": False}
    tag, nc = _stage_tags(conv), _non_customer(conv)
    lines = [{**{k: ln[k] for k in ("i", "call_id", "call_no", "t", "start", "speaker", "role", "text")},
              "stages": sorted(tag.get(ln["i"], [])), "non_customer": ln["i"] in nc} for ln in conv["lines"]]
    return {"available": True, "segmented": bool(conv.get("stages")), "calls": conv["calls"],
            "multi_call": conv.get("multi_call"), "non_customer_spans": conv.get("non_customer_spans") or [],
            "lines": lines}


@app.get("/api/cases/{ref}/calls/{call_id}/transcript")
def transcript(ref: str, call_id: int):
    ref = store.clean_ref(ref)
    conv = store.read_json(store.profile_dir(ref) / "conversation.json", None)
    if conv:
        cmeta = next((c for c in conv["calls"] if c["call_id"] == call_id), None)
        if cmeta and cmeta.get("included"):
            tag, nc = _stage_tags(conv), _non_customer(conv)
            lines = [{"i": ln["li"], "t": clock(ln["start"]), "start": ln["start"], "speaker": ln["speaker"],
                      "role": ln["role"], "text": ln["text"], "stages": sorted(tag.get(ln["i"], [])),
                      "non_customer": ln["i"] in nc} for ln in conv["lines"] if ln["call_id"] == call_id]
            return {"call_id": call_id, "roles": cmeta.get("roles") or {}, "roles_how": cmeta.get("roles_how"),
                    "segmented": bool(conv.get("stages")), "note": None, "lines": lines}
    note = None
    if conv:
        cmeta = next((c for c in conv["calls"] if c["call_id"] == call_id), None)
        if cmeta and not cmeta.get("included"):
            note = (f"This recording is the same conversation as call {cmeta.get('contained_in')} "
                    f"({round((cmeta.get('containment') or 0) * 100)}% of its lines are there, at "
                    f"{cmeta.get('span_in_kept')}), so it isn't added to the conversation a second time.")
    seg = store.read_json(store.profile_dir(ref) / "calls" / f"{call_id}.json", None)
    if seg and not conv:
        lines, roles = seg["lines"], seg["roles"]
        stage_spans = {int(k): v.get("spans") or [] for k, v in seg["stages"].items()}
        non_customer = seg.get("non_customer_spans") or []
        roles_how = seg.get("roles_how")
    else:
        details = store.load_call(ref, call_id)
        if not details:
            raise HTTPException(404, "Call not found")
        lines = build_lines(details.get("transcript") or [])
        roles, roles_how = guess_roles(details.get("transcript") or [], details.get("report"))
        stage_spans, non_customer = {}, []
    tag: dict[int, list[int]] = defaultdict(list)
    for n, spans in stage_spans.items():
        for a, b in spans:
            for i in range(a, b + 1):
                tag[i].append(n)
    nc = set()
    for sp in non_customer:
        nc.update(range(sp.get("start", 0), sp.get("end", -1) + 1))
    out = [{"i": ln["i"], "t": clock(ln["start"]), "start": ln["start"], "speaker": ln["speaker"],
            "role": roles.get(ln["speaker"], f"SPEAKER_{ln['speaker']}"), "text": ln["text"],
            "stages": sorted(tag.get(ln["i"], [])), "non_customer": ln["i"] in nc} for ln in lines]
    return {"call_id": call_id, "roles": roles, "roles_how": roles_how, "segmented": bool(seg and not conv),
            "non_customer_spans": non_customer, "note": note, "lines": out}


@app.get("/api/cases/{ref}/calls/{call_id}/audit")
def audit(ref: str, call_id: int):
    details = store.load_call(store.clean_ref(ref), call_id)
    if not details:
        raise HTTPException(404, "Call not found")
    rep = details.get("report") or {}
    qs = [q for q in rep.get("questions") or [] if not q.get("hidden")]
    kids = defaultdict(list)
    for q in qs:
        if q.get("parent_question_id"):
            kids[q["parent_question_id"]].append(q)

    def slim(q):
        return {k: q.get(k) for k in ("question_id", "order", "question", "status", "score", "max_score",
                                      "timestamps", "reasoning", "edited_by_qa")}
    top = sorted([q for q in qs if not q.get("parent_question_id")], key=lambda q: q.get("order") or 0)
    return {"totals": rep.get("totals"), "submitted": rep.get("submitted"), "source": rep.get("source"),
            "questions": [{**slim(q), "children": [slim(c) for c in sorted(kids.get(q["question_id"], []),
                                                                            key=lambda c: c.get("order") or 0)]}
                          for q in top],
            "crm": details.get("crm") or [], "errors": details.get("errors") or []}


# ------------------------------------------------------------------- profile
def _upgrade_stage(s: dict) -> dict:
    """Old per-call stage entry -> merged-conversation shape (so older profiles still display)."""
    calls = s.get("calls") or []
    best = max(calls, key=lambda c: (bool(c.get("analysis")), len(c.get("spans") or [])), default={})
    return {
        "n": s["n"], "key": s.get("key"), "name": s["name"], "group": s["group"],
        "covered": any(c.get("covered") for c in calls), "spans": [],
        "time": [f"call {c['call_id']} {t}" for c in calls for t in c.get("time") or []],
        "segment_note": best.get("segment_note"),
        "audits": [{"call_id": c["call_id"], "included": True, "status": c.get("audit_status"),
                    "score": (c.get("audit_score") or [None, None])[0],
                    "max_score": (c.get("audit_score") or [None, None])[1], "question": c.get("audit_question"),
                    "reasoning": c.get("audit_reasoning"), "sub_questions": c.get("audit_sub_questions")}
                   for c in calls if c.get("audit_status")],
        "analysis": best.get("analysis"), "error": best.get("error"), "legacy": True,
    }


@app.get("/api/cases/{ref}/profile")
def profile(ref: str):
    ref = store.clean_ref(ref)
    pdir = store.profile_dir(ref)
    stages = store.read_json(pdir / "stages.json", None)
    if isinstance(stages, list) and stages and "calls" in stages[0]:
        stages = [_upgrade_stage(s) for s in stages]       # profile built before calls were merged
    return {"meta": store.read_json(pdir / "meta.json", {"status": "none"}),
            "deep": store.read_json(pdir / "deep_profile.json", None),
            "report_available": (pdir / "customer_profile.md").exists(),
            "stages": stages,
            "persona": store.read_json(pdir / "persona.json", None),
            "persona_full": store.read_json(pdir / "persona_full.json", None)}


@app.get("/api/cases/{ref}/report.md")
def profile_report(ref: str):
    ref = store.clean_ref(ref)
    f = store.profile_dir(ref) / "customer_profile.md"
    if not f.exists():
        raise HTTPException(404, "Build the profile first")
    return FileResponse(f, media_type="text/markdown", filename=f"customer_profile_{ref}.md")


@app.put("/api/cases/{ref}/persona")
def save_persona(ref: str, persona: dict = Body(...)):
    ref = store.clean_ref(ref)
    if not isinstance(persona.get("profile"), dict) or not persona.get("id"):
        raise HTTPException(400, "Persona must have an id and a profile object")
    pdir = store.profile_dir(ref)
    store.write_json(pdir / "persona.json", persona)
    full = store.read_json(pdir / "persona_full.json", {}) or {}
    full["persona"] = persona
    store.write_json(pdir / "persona_full.json", full)
    meta = store.read_json(pdir / "meta.json", {}) or {}
    meta["edited_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.write_json(pdir / "meta.json", meta)
    return {"ok": True}


@app.post("/api/cases/{ref}/export-agent")
def export_agent(ref: str):
    """Add/replace this persona in the AI Call Agent's transcript_personas.json (backup kept)."""
    ref = store.clean_ref(ref)
    persona = store.read_json(store.profile_dir(ref) / "persona.json", None)
    if not persona:
        raise HTTPException(404, "Build the profile first")
    path = Path(store.get_settings()["agent_personas_path"])
    if not path.parent.exists():
        raise HTTPException(400, f"Agent personas folder not found: {path.parent}")
    rows = store.read_json(path, []) if path.exists() else []
    if not isinstance(rows, list):
        raise HTTPException(500, f"{path} is not a JSON list - not touching it")
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(path, path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}"))
    suffix = f"-{ref}"
    replaced = any(r.get("id") == persona["id"] or str(r.get("id", "")).endswith(suffix) for r in rows)
    rows = [r for r in rows if r.get("id") != persona["id"] and not str(r.get("id", "")).endswith(suffix)]
    rows.append(persona)
    store.write_json(path, rows)
    return {"ok": True, "path": str(path), "replaced": replaced, "count": len(rows), "id": persona["id"],
            "note": "Restart the customer agent service to load the new persona."}


# ---------------------------------------------------------------------- jobs
class FetchRequest(BaseModel):
    refs: list[str] = Field(min_length=1)
    start: str | None = None
    end: str | None = None
    include_cat: bool = True
    include_documents: bool = True
    include_audit: bool = True
    build_profile: bool = True
    refetch: bool = False
    quality: str | None = None
    anonymise: bool | None = None


class ProfileRequest(BaseModel):
    refs: list[str] = Field(min_length=1)
    quality: str | None = None
    anonymise: bool | None = None
    persona_only: bool = False


@app.post("/api/jobs/fetch")
def job_fetch(req: FetchRequest):
    if req.include_audit and not sources.audit_key_present():
        raise HTTPException(400, "AUDIT_API_KEY is not set in .env")
    if req.build_profile and not llm.key_present():
        raise HTTPException(400, "OPENAI_API_KEY is not set in .env - untick 'Build profile' or add the key")
    end = _date(req.end, _today())
    start = _date(req.start, end - timedelta(days=int(store.get_settings()["lookup_days"])))
    return jobs.start_fetch(req.refs, start, end, req.include_cat, req.build_profile, req.refetch,
                            req.quality, req.anonymise, req.include_documents, req.include_audit)


@app.post("/api/jobs/profile")
def job_profile(req: ProfileRequest):
    if not llm.key_present():
        raise HTTPException(400, "OPENAI_API_KEY is not set in .env")
    return [jobs.start_profile(r, req.quality, req.anonymise, req.persona_only) for r in req.refs]


@app.get("/api/jobs")
def job_list():
    return jobs.list_jobs()


@app.get("/api/jobs/{job_id}")
def job_get(job_id: str):
    j = jobs.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    return j


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str):
    return {"ok": jobs.cancel_job(job_id)}


# ------------------------------------------------------------------ frontend
if store.FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=store.FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"detail": "Not found"}, status_code=404)
        f = store.FRONTEND_DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(store.FRONTEND_DIST / "index.html")
