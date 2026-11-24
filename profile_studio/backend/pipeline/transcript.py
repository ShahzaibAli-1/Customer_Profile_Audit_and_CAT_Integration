"""Turning a call's raw data into numbered lines, speaker roles and audit hints."""

from __future__ import annotations

import json
import re
from collections import Counter

from .stages import STAGE_BY_N

ADVISOR, CUSTOMER = "ADVISOR", "CUSTOMER"


def _secs(ts: str) -> tuple[float, float]:
    try:
        a, b = str(ts).split("-", 1)
        return float(a), float(b)
    except (ValueError, AttributeError):
        return 0.0, 0.0


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def build_lines(transcript: list[dict], merge_gap: float = 1.5) -> list[dict]:
    """Merge consecutive fragments from the same speaker into one numbered line."""
    lines: list[dict] = []
    for seg in transcript or []:
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start, end = _secs(seg.get("timestamp", ""))
        spk = str(seg.get("speaker") or "?")
        if lines and lines[-1]["speaker"] == spk and start - lines[-1]["end"] <= merge_gap \
                and len(lines[-1]["text"]) < 600:
            lines[-1]["text"] += " " + text
            lines[-1]["end"] = max(lines[-1]["end"], end)
        else:
            lines.append({"i": len(lines), "start": start, "end": end, "speaker": spk, "text": text})
    return lines


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def guess_roles(transcript: list[dict], report: dict | None) -> tuple[dict[str, str], str]:
    """Map raw speaker letters to ADVISOR/CUSTOMER using the audit's labelled evidence.

    Returns (mapping, how) where how says what the guess was based on.
    """
    votes: dict[str, Counter] = {}
    by_text: dict[str, set[str]] = {}
    for seg in transcript or []:
        t = _norm(str(seg.get("text") or ""))
        if len(t) >= 12:
            by_text.setdefault(t, set()).add(str(seg.get("speaker")))
    evidence = ""
    for q in (report or {}).get("questions") or []:
        ev = q.get("evidence")
        if isinstance(ev, str) and len(ev) > len(evidence):
            evidence = ev
    for m in re.finditer(r"\]\s*\[(ADVISOR|CLIENT|CUSTOMER)\]\s*(.+)", evidence):
        role = ADVISOR if m.group(1) == "ADVISOR" else CUSTOMER
        t = _norm(m.group(2))
        for spk in by_text.get(t, ()):
            votes.setdefault(spk, Counter())[role] += 1
    mapping = {spk: c.most_common(1)[0][0] for spk, c in votes.items() if c}
    speakers = {str(s.get("speaker")) for s in transcript or []}
    if len(mapping) == 1 and len(speakers) == 2:
        (known, role), = mapping.items()
        other = (speakers - {known}).pop()
        mapping[other] = CUSTOMER if role == ADVISOR else ADVISOR
    if len(set(mapping.values())) == 2:
        return mapping, f"audit evidence ({sum(sum(c.values()) for c in votes.values())} matched lines)"
    return {}, "none"


def _ranges(ts) -> list[list[float]]:
    out = []
    for a, b in re.findall(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)", str(ts or "")):
        a, b = float(a), float(b)
        if b >= a:
            out.append([a, b])
    return out


def _reasoning_text(reasoning, limit: int = 1800) -> str:
    items = reasoning
    if isinstance(reasoning, str):
        try:
            items = json.loads(reasoning)
        except json.JSONDecodeError:
            return reasoning[:limit]
    parts = []
    for it in items or []:
        if isinstance(it, dict):
            parts.append(f"{it.get('heading', '')}: {it.get('comment', '')}".strip(": "))
        else:
            parts.append(str(it))
    return "\n".join(parts)[:limit]


def audit_by_stage(report: dict | None) -> dict[int, dict]:
    """Audit result for each stage n (1-32), from the top-level question with order n."""
    qs = (report or {}).get("questions") or []
    children: dict = {}
    for q in qs:
        if q.get("parent_question_id"):
            children.setdefault(q["parent_question_id"], []).append(q)
    out: dict[int, dict] = {}
    for q in qs:
        if q.get("parent_question_id") or q.get("hidden"):
            continue
        n = q.get("order")
        if not isinstance(n, int) or n not in STAGE_BY_N:
            continue
        out[n] = {
            "question_id": q.get("question_id"),
            "question": (q.get("question") or "").strip(),
            "status": q.get("status"),
            "score": q.get("score"),
            "max_score": q.get("max_score"),
            "ranges": _ranges(q.get("timestamps")),
            "reasoning": _reasoning_text(q.get("reasoning")),
            "sub_questions": [
                {"question": (c.get("question") or "").strip()[:300], "status": c.get("status"),
                 "score": c.get("score"), "max_score": c.get("max_score")}
                for c in sorted(children.get(q.get("question_id"), []), key=lambda c: c.get("order") or 0)
            ],
        }
    return out


def crm_checks(report: dict | None) -> list[dict]:
    """The CRM match questions (order > 32): useful facts for the persona."""
    out = []
    for q in (report or {}).get("questions") or []:
        if q.get("parent_question_id") or not isinstance(q.get("order"), int) or q["order"] <= 32:
            continue
        out.append({"question": (q.get("question") or "").strip(), "status": q.get("status"),
                    "reasoning": _reasoning_text(q.get("reasoning"), 900)})
    return out


def lines_in_seconds(lines: list[dict], ranges: list[list[float]]) -> list[int]:
    hit = []
    for ln in lines:
        for a, b in ranges:
            if ln["end"] >= a and ln["start"] <= b:
                hit.append(ln["i"])
                break
    return hit


def render(lines: list[dict], roles: dict[str, str] | None = None, idx: list[int] | None = None,
           with_index: bool = True) -> str:
    roles = roles or {}
    sel = lines if idx is None else [lines[i] for i in idx if 0 <= i < len(lines)]
    out = []
    for ln in sel:
        who = roles.get(ln["speaker"], f"SPEAKER_{ln['speaker']}")
        prefix = f"{ln['i']}|" if with_index else ""
        out.append(f"{prefix}{clock(ln['start'])}|{who}|{ln['text']}")
    return "\n".join(out)
