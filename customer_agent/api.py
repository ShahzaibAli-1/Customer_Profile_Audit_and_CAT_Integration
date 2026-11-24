"""HTTP API - compatible with the AI Call Agent's customer-agent contract (backend/a2a_runner.py).

  GET    /api/customers                    training personas list (empty here - see new-customers)
  GET    /api/new-customers                the real-case personas
  GET    /api/customers/{id}               one persona (also /api/new-customers/{id}); {id} = persona id or case no.
  POST   /api/call                         {customer_id, advisor_opening} -> start a call, first reply
  POST   /api/call/{id}                    {message, session_id} -> the customer's next reply
  DELETE /api/conversation/{id}            forget the customer's current call
  GET    /api/conversation/{id}            the current call so far
  GET    /api/sessions/{session_id}        a call by session id (transcript, uploads)
  GET    /api/customers/{id}/documents     the documents this customer has
  GET    /api/customers/{id}/documents/{key}   one document file (what an upload action points to)
  GET    /api/customer-agent/health

Replies carry extra fields the AI Call Agent can ignore or use: "actions" (uploads etc.),
"stage" (which script stage the customer thinks this is) and "mood".
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .documents import CATEGORIES, summary
from .engine import SESSIONS, AgentError, settings
from .personas import STORE, Persona

router = APIRouter()

DEFAULT_OPENING = ("Hello, you're through to Debt Advisory Services. Can you tell me a little about what's "
                   "happening with your debts today?")


class CallRequest(BaseModel):
    customer_id: str
    advisor_opening: str | None = None
    session_id: str | None = None


class MessageRequest(BaseModel):
    message: str
    session_id: str | None = None


def _persona(customer_id: str) -> Persona:
    p = STORE.get(customer_id)
    if not p:
        raise HTTPException(404, f"Customer not found: {customer_id}")
    return p


def _row(p: Persona) -> dict:
    pr = p.profile
    return {
        "id": p.id, "ref": p.ref, "label": p.label, "segment": p.segment, "risk": p.risk, "stage": p.stage,
        "totalDebt": pr.get("total_debt", ""), "affordablePayment": pr.get("affordable_payment", ""),
        "biggestConcern": pr.get("biggest_concern", ""), "mood": pr.get("mood", ""),
        "personality": pr.get("personality_traits") or pr.get("personality", ""),
        "sourcePattern": p.source_pattern, "promptName": p.name, "gender": p.gender,
        "voiceId": "", "voiceName": "", "isTranscript": True, "dynamic": False,
        "difficulty": p.difficulty, "documents": {c: len(d) for c, d in summary(p.documents).items()},
    }


def _detail(p: Persona) -> dict:
    return {**_row(p), "profile": p.profile}


@router.get("/api/customer-agent/health")
def health():
    s = settings()
    return {"ok": True, "personas": len(STORE.all()), "model": s["customer_model"], "effort": s["customer_effort"]}


@router.get("/api/customers")
def list_training() -> list[dict]:
    return []


@router.get("/api/new-customers")
def list_customers() -> list[dict]:
    return [_row(p) for p in STORE.all()]


@router.get("/api/customers/{customer_id}")
@router.get("/api/new-customers/{customer_id}")
def get_customer(customer_id: str) -> dict:
    return _detail(_persona(customer_id))


@router.get("/api/customers/{customer_id}/documents")
def documents(customer_id: str) -> dict:
    p = _persona(customer_id)
    return {"customer_id": p.id, "categories": CATEGORIES,
            "documents": [{k: d[k] for k in ("key", "category", "document_type", "file_name", "mime", "size")}
                          | {"url": f"/api/customers/{p.id}/documents/{d['key']}"} for d in p.documents]}


@router.get("/api/customers/{customer_id}/documents/{key}")
def document_file(customer_id: str, key: str):
    p = _persona(customer_id)
    d = next((d for d in p.documents if d["key"] == key), None)
    if not d or not Path(d["path"]).is_file():
        raise HTTPException(404, "Document not found")
    return FileResponse(d["path"], media_type=d["mime"], filename=d["file_name"])


def _response(p: Persona, session, out: dict, message: str | None = None, opening: str | None = None) -> dict:
    turns = sum(1 for h in session.history if h["role"] == "customer")
    body = {
        "session_id": session.id, "customer_id": p.id, "customer_name": p.name, "reply": out["reply"],
        "actions": out["actions"], "stage": out.get("stage"), "mood": out.get("mood"), "turns": turns,
        "uploads": session.uploads, "voice_id": "", "gender": p.gender, "voice_enabled": False, "voice_url": None,
    }
    if opening is not None:
        body["advisor_opening"] = opening
    if message is not None:
        body["message"] = message
    return body


@router.post("/api/call")
def start_call(req: CallRequest) -> dict:
    p = _persona(req.customer_id)
    session = SESSIONS.get(p, req.session_id, reset=True)
    opening = (req.advisor_opening or "").strip() or DEFAULT_OPENING
    with session.lock:
        try:
            out = session.reply(p, opening)
        except AgentError as exc:
            raise HTTPException(502, str(exc))
        session.save()
    return _response(p, session, out, opening=opening)


@router.post("/api/call/{customer_id}")
def send_message(customer_id: str, req: MessageRequest) -> dict:
    p = _persona(customer_id)
    session = SESSIONS.get(p, req.session_id)
    with session.lock:
        try:
            out = session.reply(p, req.message)
        except AgentError as exc:
            raise HTTPException(502, str(exc))
        session.save()
    return _response(p, session, out, message=req.message)


@router.delete("/api/conversation/{customer_id}")
def reset(customer_id: str) -> dict:
    p = _persona(customer_id)
    SESSIONS.drop(p)
    return {"ok": True}


@router.get("/api/conversation/{customer_id}")
def conversation(customer_id: str, session_id: str | None = None) -> dict:
    p = _persona(customer_id)
    s = SESSIONS.get(p, session_id)
    return s.to_dict() | {"customer_name": p.name}


@router.get("/api/sessions/{session_id}")
def session(session_id: str) -> dict:
    s = SESSIONS.find(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s.to_dict()
