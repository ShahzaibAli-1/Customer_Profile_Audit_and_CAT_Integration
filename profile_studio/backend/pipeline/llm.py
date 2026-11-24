"""Thin OpenAI wrapper: JSON answers, retries, token accounting."""

from __future__ import annotations

import json
import os
import random
import re
import threading
import time

from dotenv import load_dotenv

from store import ROOT

_client = None
_client_key: str | None = None
_client_lock = threading.Lock()


class LLMError(Exception):
    pass


def _current_key() -> str:
    """Re-read .env every time, so a key added or changed while the server runs is picked up."""
    load_dotenv(ROOT / ".env", override=True)
    key = os.getenv("OPENAI_API_KEY") or ""
    if not key:
        for alias in ("OPENAIAPIKEY", "OPENAI_KEY", "OPENAI_APIKEY"):
            if os.getenv(alias):
                key = os.environ[alias]
                break
    return key.strip()


def key_present() -> bool:
    return bool(_current_key())


def client():
    global _client, _client_key
    with _client_lock:
        key = _current_key()
        if not key:
            raise LLMError("OPENAI_API_KEY is not set. Add it to the .env file in Test_Customer_Scripts.")
        if _client is None or key != _client_key:
            from openai import OpenAI
            _client = OpenAI(api_key=key, timeout=900, max_retries=0)
            _client_key = key
        return _client


def list_models() -> list[str]:
    try:
        ids = [m.id for m in client().models.list()]
    except Exception:
        return []
    keep = [i for i in ids if re.match(r"^(gpt-|o\d|chatgpt-)", i)
            and not any(x in i for x in ("audio", "realtime", "tts", "transcribe", "image", "search", "instruct"))]
    return sorted(keep)


def _is_reasoning(model: str) -> bool:
    return bool(re.match(r"^(o\d|gpt-5|gpt-6)", model))


def _transient(exc: Exception) -> bool:
    if type(exc).__name__ in {"RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"}:
        return True
    return getattr(exc, "status_code", None) in {408, 409, 429, 500, 502, 503, 504}


class Usage:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_model: dict[str, dict[str, int]] = {}

    def add(self, model: str, usage) -> None:
        if not usage:
            return
        with self.lock:
            m = self.by_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
            m["calls"] += 1
            m["input_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
            m["output_tokens"] += getattr(usage, "completion_tokens", 0) or 0


def chat_json(model: str, system: str, user: str, usage: Usage | None = None,
              max_attempts: int = 5, effort: str = "medium") -> dict:
    """Ask for a JSON object back. Retries transient failures and unparseable output."""
    kwargs = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    if _is_reasoning(model):
        kwargs["reasoning_effort"] = effort
    else:
        kwargs["temperature"] = 0.2
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            resp = client().chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            # Some models reject a parameter: drop it and go again straight away.
            dropped = False
            for p in ("reasoning_effort", "temperature"):
                if p in kwargs and p in msg and getattr(exc, "status_code", None) == 400:
                    kwargs.pop(p)
                    dropped = True
            if dropped:
                continue
            if not _transient(exc) or attempt + 1 == max_attempts:
                raise LLMError(f"{type(exc).__name__}: {msg[:500]}") from exc
            last = exc
            time.sleep(min(60, 2 * (2 ** attempt)) + random.uniform(0, 1))
            continue
        if usage:
            usage.add(model, resp.usage)
        text = (resp.choices[0].message.content or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except json.JSONDecodeError:
                    pass
            last = exc
    raise LLMError(f"No valid JSON after {max_attempts} attempts: {last}")
