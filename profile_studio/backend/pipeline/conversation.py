"""Merge a case's calls into ONE ordered conversation.

A case often has two calls in the Audit tool: the main one and a "-W" one. They are
not always two different conversations:

  * the "-W" call is often a separate recording of part (or all) of the SAME
    conversation - e.g. the leg after the dialler dropped - and the main
    recording already contains it;
  * sometimes it really is a separate call (e.g. a short transfer call later on).

Simply concatenating would replay the same conversation twice. So:

  1. Duplicates - for every pair, measure how much of one transcript is contained in
     the other (fuzzy 3-word-shingle matching, robust to the two copies being
     transcribed slightly differently). A call that is >= 60% contained in another
     is a duplicate; the better copy is kept (submitted audit, not "-W", longest).
  2. Order - the remaining calls are put in time order. The call time recorded in the
     Audit tool is not reliable for main calls, so content decides first: a call that
     opens with a lead hand-over comes first, one that opens with an apology for a
     dropped call comes after; the recorded time breaks ties.
  3. Merge - lines are renumbered across calls; each line keeps its call, its own
     time within that call and its resolved role (ADVISOR / CUSTOMER).
"""

from __future__ import annotations

import re
from datetime import datetime

from .transcript import ADVISOR, CUSTOMER, clock

CONTAINED = 0.60
_LEAD = re.compile(r"\b(got|have) a lead\b|\blead for you\b|\bhere for (you|\d)|\bi'?m here for\b", re.I)
_RECONNECT = re.compile(r"dialler|dialer|(call|line) (just )?(dropped|cut)|technical (issue|problem)|"
                        r"got cut off|lost (you|the call)|sorry about (that|earlier)|calling (you )?back", re.I)


def _words(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).split()


def _shingles(text: str) -> set[tuple]:
    w = _words(text)
    return {tuple(w[i:i + 3]) for i in range(len(w) - 2)}


def containment(b_lines: list[dict], a_lines: list[dict]) -> tuple[float, list[int]]:
    """Share of B's substantial lines that also appear (fuzzily) in A, and where in A they matched."""
    index: dict[tuple, set[int]] = {}
    for ln in a_lines:
        for g in _shingles(ln["text"]):
            index.setdefault(g, set()).add(ln["i"])
    total = hits = 0
    where: list[int] = []
    for ln in b_lines:
        g = _shingles(ln["text"])
        if len(g) < 4:
            continue
        total += 1
        cand: dict[int, int] = {}
        for x in g:
            for i in index.get(x, ()):
                cand[i] = cand.get(i, 0) + 1
        if cand:
            best_i, best = max(cand.items(), key=lambda kv: kv[1])
            if best / len(g) >= 0.5:
                hits += 1
                where.append(best_i)
    return (hits / total if total else 0.0), where


def _opening(lines: list[dict], n: int = 15) -> str:
    return " ".join(ln["text"] for ln in lines[:n])


def _when(info: dict) -> float:
    try:
        return datetime.fromisoformat(str(info.get("call_date")).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _keep_score(p: dict) -> tuple:
    info = p["info"]
    return (bool(info.get("review_status")), not str(info.get("case_id") or "").endswith("-W"), len(p["lines"]))


def build_conversation(prepared: dict[int, dict], log=print) -> dict:
    """prepared: call_id -> {"info", "lines", "roles", ...}. Returns the merged conversation."""
    ids = list(prepared)
    dropped: dict[int, dict] = {}

    # ---- 1. duplicates (largest/best copy first, so it is the one that survives)
    order = sorted(ids, key=lambda c: _keep_score(prepared[c]), reverse=True)
    for i, a in enumerate(order):
        if a in dropped:
            continue
        for b in order[i + 1:]:
            if b in dropped:
                continue
            frac, where = containment(prepared[b]["lines"], prepared[a]["lines"])
            if frac >= CONTAINED:
                la = prepared[a]["lines"]
                span = (f"{clock(la[min(where)]['start'])}-{clock(la[max(where)]['end'])}" if where else "")
                dropped[b] = {"contained_in": a, "containment": round(frac, 2), "span_in_kept": span}
                log(f"  call {b} is the same conversation as part of call {a} "
                    f"({frac:.0%} of its lines are in call {a}{', at ' + span if span else ''}) - not added twice")

    kept = [c for c in ids if c not in dropped]

    # ---- 2. order
    def rank(c):
        op = _opening(prepared[c]["lines"])
        content = 0 if _LEAD.search(op) else (2 if _RECONNECT.search(op) else 1)
        return (content, _when(prepared[c]["info"]))

    kept.sort(key=rank)

    # ---- 3. merge
    lines: list[dict] = []
    calls_meta: list[dict] = []
    multi = len(kept) > 1
    for no, cid in enumerate(kept, 1):
        p = prepared[cid]
        first = len(lines)
        for ln in p["lines"]:
            role = p["roles"].get(ln["speaker"]) or f"SPEAKER_{ln['speaker']}"
            lines.append({
                "i": len(lines), "call_id": cid, "call_no": no, "li": ln["i"],
                "start": ln["start"], "end": ln["end"],
                "t": (f"C{no} " if multi else "") + clock(ln["start"]),
                "speaker": ln["speaker"], "role": role, "text": ln["text"],
            })
        op = _opening(p["lines"])
        calls_meta.append({
            "call_id": cid, "call_no": no, "included": True, "case_id": p["info"].get("case_id"),
            "call_date": p["info"].get("call_date"), "lines": len(p["lines"]),
            "first_line": first, "last_line": len(lines) - 1,
            "opening": ("lead hand-over" if _LEAD.search(op) else "reconnect after a dropped call"
                        if _RECONNECT.search(op) else "normal start"),
            "roles": p["roles"], "roles_how": p.get("roles_how"),
        })
    for cid, d in dropped.items():
        p = prepared[cid]
        calls_meta.append({"call_id": cid, "call_no": None, "included": False, "case_id": p["info"].get("case_id"),
                           "call_date": p["info"].get("call_date"), "lines": len(p["lines"]),
                           "reason": "duplicate recording", **d})
    return {"calls": calls_meta, "lines": lines, "multi_call": multi}


def line_role(line: dict) -> str:
    return line.get("role") or ""


def render_conversation(lines: list[dict], idx: list[int] | None = None, with_index: bool = True) -> str:
    """Numbered lines; a separator is inserted whenever the call changes."""
    sel = lines if idx is None else [lines[i] for i in idx if 0 <= i < len(lines)]
    out, last_call = [], None
    multi = any(ln.get("call_no", 1) != 1 for ln in lines)
    for ln in sel:
        if multi and ln["call_no"] != last_call:
            out.append(f"----- CALL {ln['call_no']} (call id {ln['call_id']}) -----")
            last_call = ln["call_no"]
        prefix = f"{ln['i']}|" if with_index else ""
        out.append(f"{prefix}{ln['t']}|{ln['role']}|{ln['text']}")
    return "\n".join(out)


__all__ = ["build_conversation", "render_conversation", "containment", "ADVISOR", "CUSTOMER"]
