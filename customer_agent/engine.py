"""A call with a simulated customer: memory, one reply per advisor turn, document uploads."""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .documents import CATEGORIES
from .personas import ROOT, STORE, Persona
from .prompt import system_prompt, turn_note

SESSIONS_DIR = ROOT / "customer_agent_sessions"
SETTINGS_PATH = ROOT / "profile_studio" / "settings.json"
HISTORY_MESSAGES = 80            # verbatim history sent each turn (advisor + customer messages)
DIGEST_CHARS = 4000

_client = None
_client_key = None
_client_lock = threading.Lock()


class AgentError(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def settings() -> dict:
    s = {"customer_model": "gpt-6.1-terra", "customer_effort": "low"}
    try:
        s.update({k: v for k, v in json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).items()
                  if k in s and v})
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    load_dotenv(ROOT / ".env", override=True)
    if os.getenv("CUSTOMER_AGENT_MODEL"):
        s["customer_model"] = os.environ["CUSTOMER_AGENT_MODEL"]
    return s


def _openai():
    global _client, _client_key
    load_dotenv(ROOT / ".env", override=True)
    key = (os.getenv("OPENAI_API_KEY") or os.getenv("OPENAIAPIKEY") or "").strip()
    if not key:
        raise AgentError("OPENAI_API_KEY is not set in Test_Customer_Scripts/.env")
    with _client_lock:
        if _client is None or key != _client_key:
            from openai import OpenAI
            _client = OpenAI(api_key=key, timeout=120, max_retries=0)
            _client_key = key
        return _client


def _complete(model: str, effort: str, messages: list[dict]) -> tuple[dict, str]:
    kwargs = {"model": model, "messages": messages, "response_format": {"type": "json_object"}}
    if re.match(r"^(o\d|gpt-5|gpt-6)", model):
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0.9
    last = None
    for attempt in range(4):
        try:
            resp = _openai().chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status_code", None)
            if status == 400 and "reasoning_effort" in str(exc) and "reasoning_effort" in kwargs:
                kwargs.pop("reasoning_effort")
                continue
            transient = status in (408, 409, 429, 500, 502, 503, 504) or type(exc).__name__ in (
                "RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError")
            if not transient or attempt == 3:
                raise AgentError(f"{type(exc).__name__}: {str(exc)[:300]}") from exc
            last = exc
            time.sleep(1.5 * (2 ** attempt) + random.uniform(0, 0.5))
            continue
        text = (resp.choices[0].message.content or "").strip()
        try:
            return json.loads(text), text
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0)), text
                except json.JSONDecodeError:
                    pass
            return {"reply": text}, text
    raise AgentError(f"No reply from the model: {last}")


def clean_reply(text: str) -> str:
    t = str(text or "").strip()
    t = re.sub(r"^\s*(customer|caller|client|you|me)\s*:\s*", "", t, flags=re.I)   # "Customer: ..."
    t = re.sub(r"\*[^*]{1,80}\*", "", t)                                 # *sighs*
    t = re.sub(r"\[[^\]]{1,80}\]", "", t)                                # [pause]
    t = re.sub(r"\((?:laughs?|sighs?|pause[sd]?|crying|sniffs?|quietly|hesitat\w*)[^)]{0,40}\)", "", t, flags=re.I)
    t = t.strip().strip('"').strip()
    return re.sub(r"\s{2,}", " ", t) or "Sorry, could you say that again?"


class Session:
    def __init__(self, session_id: str, persona: Persona):
        self.id = session_id
        self.persona_id = persona.id
        self.created_at = _now()
        self.history: list[dict] = []           # {role: advisor|customer, text, at, stage, mood, actions}
        self.uploads: list[dict] = []
        self.pending: list[dict] = []
        self.lock = threading.Lock()

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {"session_id": self.id, "persona_id": self.persona_id, "created_at": self.created_at,
                "history": self.history, "uploads": self.uploads, "pending": self.pending}

    @classmethod
    def from_dict(cls, d: dict, persona: Persona) -> "Session":
        s = cls(d["session_id"], persona)
        s.created_at = d.get("created_at") or s.created_at
        s.history, s.uploads, s.pending = d.get("history") or [], d.get("uploads") or [], d.get("pending") or []
        return s

    def save(self) -> None:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = SESSIONS_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', self.id)}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
        for _ in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                time.sleep(0.05)

    # ------------------------------------------------------------ one turn
    def _digest(self, older: list[dict]) -> str:
        said, budget = [], DIGEST_CHARS
        for h in reversed(older):
            if h["role"] != "customer":
                continue
            t = " ".join(h["text"].split())[:220]
            if budget - len(t) < 0:
                break
            budget -= len(t)
            said.append(t)
        if not said:
            return ""
        return ("Earlier in this call (no longer shown word for word) you already said the following. It is all "
                "settled - stay consistent with it and don't present it as new:\n"
                + "\n".join(f"- {t}" for t in reversed(said)))

    def reply(self, persona: Persona, advisor_message: str) -> dict:
        advisor_message = (advisor_message or "").strip()
        if not advisor_message:
            raise AgentError("The advisor message is empty.")
        cfg = settings()
        recent_advisor = " ".join(h["text"] for h in self.history[-2:] if h["role"] == "advisor")
        hits = persona.examples.search(advisor_message + " " + recent_advisor[-300:], k=4)
        stage = persona.examples.guess_stage(hits)
        if stage is None:
            stage = next((h.get("stage") for h in reversed(self.history) if h.get("stage")), None)
        stage_examples = persona.examples.by_stage(stage, k=2) if stage else []

        window = self.history[-HISTORY_MESSAGES:]
        messages = [{"role": "system", "content": system_prompt(persona)}]
        digest = self._digest(self.history[:-HISTORY_MESSAGES]) if len(self.history) > HISTORY_MESSAGES else ""
        if digest:
            messages.append({"role": "system", "content": digest})
        for h in window:
            messages.append({"role": "user" if h["role"] == "advisor" else "assistant", "content": h["text"]})
        note = turn_note(persona, stage, hits, stage_examples, self.uploads, self.pending)
        if note:
            messages.append({"role": "system", "content": note + "\n\nAnswer the advisor's next message in JSON."})
        messages.append({"role": "user", "content": advisor_message})

        started = time.monotonic()
        data, raw = _complete(cfg["customer_model"], cfg["customer_effort"], messages)
        reply = clean_reply(data.get("reply") if isinstance(data, dict) else raw)
        actions = self._resolve_actions(persona, data.get("actions") if isinstance(data, dict) else [])
        model_stage = data.get("stage") if isinstance(data, dict) else None
        try:
            model_stage = int(model_stage) if model_stage not in (None, "", "null") else None
        except (TypeError, ValueError):
            model_stage = None

        self.history.append({"role": "advisor", "text": advisor_message, "at": _now()})
        self.history.append({"role": "customer", "text": reply, "at": _now(), "stage": model_stage or stage,
                             "mood": (data.get("mood") if isinstance(data, dict) else None), "actions": actions,
                             "latency_s": round(time.monotonic() - started, 2), "model": cfg["customer_model"]})
        return {"reply": reply, "actions": actions, "stage": model_stage or stage,
                "mood": data.get("mood") if isinstance(data, dict) else None}

    def _resolve_actions(self, persona: Persona, actions) -> list[dict]:
        out = []
        for a in actions if isinstance(actions, list) else []:
            if not isinstance(a, dict):
                continue
            kind = a.get("type")
            cats = [c for c in (a.get("documents") or []) if c in CATEGORIES]
            if kind not in ("upload", "will_upload_later", "cannot_provide") or not cats:
                continue
            entry = {"type": kind, "documents": cats, "at": _now()}
            if kind == "upload":
                files = [{"key": d["key"], "category": d["category"], "file_name": d["file_name"], "mime": d["mime"],
                          "size": d["size"], "url": f"/api/customers/{persona.id}/documents/{d['key']}"}
                         for d in persona.documents if d["category"] in cats]
                entry["files"] = files
                entry["no_file_on_record"] = [c for c in cats if not any(f["category"] == c for f in files)]
                self.uploads.append(entry)
                self.pending = [p for p in self.pending if not set(p["documents"]) <= set(cats)]
            elif kind == "will_upload_later":
                self.pending.append(entry)
            else:
                entry["reason"] = a.get("reason", "")
            out.append(entry)
        return out


class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def default_id(persona: Persona) -> str:
        return f"default_{persona.id}"

    def get(self, persona: Persona, session_id: str | None, reset: bool = False) -> Session:
        sid = session_id or self.default_id(persona)
        with self._lock:
            if reset:
                s = Session(sid if session_id else f"{persona.id}_{uuid.uuid4().hex[:8]}", persona)
                self._sessions[self.default_id(persona)] = s
                self._sessions[s.id] = s
                return s
            s = self._sessions.get(sid)
            if s is None:
                path = SESSIONS_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', sid)}.json"
                if path.exists():
                    try:
                        s = Session.from_dict(json.loads(path.read_text(encoding="utf-8")), persona)
                    except (json.JSONDecodeError, KeyError):
                        s = None
                if s is None:
                    s = Session(sid, persona)
                self._sessions[sid] = s
            return s

    def find(self, session_id: str) -> Session | None:
        with self._lock:
            s = self._sessions.get(session_id)
        if s:
            return s
        path = SESSIONS_DIR / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', session_id)}.json"
        if path.exists():
            d = json.loads(path.read_text(encoding="utf-8"))
            persona = STORE.get(d.get("persona_id", ""))
            if persona:
                return Session.from_dict(d, persona)
        return None

    def drop(self, persona: Persona) -> None:
        with self._lock:
            s = self._sessions.pop(self.default_id(persona), None)
            if s:
                self._sessions.pop(s.id, None)


SESSIONS = SessionManager()
