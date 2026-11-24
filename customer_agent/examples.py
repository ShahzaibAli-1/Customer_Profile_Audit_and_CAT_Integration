"""Real advisor -> customer exchanges from the customer's own call, and finding the right ones.

Every time the advisor speaks, the most similar things the REAL advisor said in the
real call are found (TF-IDF), and the real customer's answers to them are shown to the
model as examples of how this person actually responds. This is what makes each
simulated customer sound like themselves rather than like a generic polite caller.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from difflib import SequenceMatcher

STOP = set("""a an the and or but if so to of in on at for with from by is are was were be been being am i you he she
it we they me him her us them my your his its our their this that these those there here what which who whom whose
when where why how do does did done have has had having can could would should will shall may might must not no
yes yeah okay ok right just um uh er erm like well oh really very also then than too all any some each just
let lets i'm you're it's that's don't can't i've you've we're they're i'll you'll going get got go know""".split())
COMMON_ADDRESS = {"road", "street", "lane", "close", "avenue", "drive", "way", "flat", "house", "court", "place",
                  "crescent", "gardens", "grove", "park", "view", "hill", "the", "and", "north", "south", "east", "west"}


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z']+", text.lower()) if w not in STOP and len(w) > 2]


def _contains_identity(text: str, banned: set[str]) -> bool:
    if not banned:
        return False
    words = set(re.findall(r"[a-z0-9']+", text.lower()))
    compact = re.sub(r"[^a-z0-9]", "", text.lower())
    for b in banned:
        if b in COMMON_ADDRESS:
            continue
        if (" " not in b and b in words) or (len(b) >= 6 and b in compact):
            return True
    return False


def _name_variant(word: str, name: str) -> bool:
    """'jody' ~ 'jodie', 'jodi' ~ 'jodie': transcripts spell names however they sound."""
    if word == name:
        return True
    if len(name) < 4 or len(word) < 3 or word[:3] != name[:3] or abs(len(word) - len(name)) > 2:
        return False
    return SequenceMatcher(None, word, name).ratio() >= 0.6


def replace_names(text: str, real_names: dict[str, str], persona_name: str) -> str:
    """Real names (and spelling variants) -> the persona's name. real_names: token -> 'first' | 'last'."""
    if not real_names or not persona_name:
        return text
    parts = persona_name.split()
    p_first, p_last = parts[0], (parts[-1] if len(parts) > 1 else parts[0])

    def sub(m):
        w = m.group(0)
        for n, kind in real_names.items():
            if _name_variant(w.lower(), n):
                return p_first if kind == "first" else p_last
        return w
    return re.sub(r"[A-Za-z][A-Za-z']+", sub, text)


def build_exchanges(conv: dict, banned: set[str], real_names: dict[str, str] | None = None,
                    persona_name: str = "") -> list[dict]:
    real_names = real_names or {}
    lines = conv.get("lines") or []
    stage_of: dict[int, list[int]] = {}
    for n, st in (conv.get("stages") or {}).items():
        for a, b in st.get("spans") or []:
            for i in range(a, b + 1):
                stage_of.setdefault(i, []).append(int(n))
    skip = set()
    for sp in conv.get("non_customer_spans") or []:
        skip.update(range(sp.get("start", 0), sp.get("end", -1) + 1))

    blocks: list[dict] = []          # consecutive lines from one side
    for ln in lines:
        if ln["i"] in skip:
            continue
        role = "advisor" if ln.get("role") == "ADVISOR" else "customer" if ln.get("role") == "CUSTOMER" else None
        if not role:
            continue
        if blocks and blocks[-1]["role"] == role and blocks[-1]["call_no"] == ln.get("call_no"):
            blocks[-1]["text"] += " " + ln["text"]
            blocks[-1]["last"] = ln["i"]
        else:
            blocks.append({"role": role, "text": ln["text"], "first": ln["i"], "last": ln["i"],
                           "call_no": ln.get("call_no")})

    out = []
    for prev, cur in zip(blocks, blocks[1:]):
        if prev["role"] != "advisor" or cur["role"] != "customer":
            continue
        stages = sorted(set(stage_of.get(cur["first"], []) + stage_of.get(prev["last"], [])))
        if (banned or real_names) and 1 in stages:   # identity checks: never shown for an anonymised persona
            continue
        adv = replace_names(prev["text"].strip(), real_names, persona_name)
        cust = replace_names(cur["text"].strip(), real_names, persona_name)
        if len(cust) < 2 or _contains_identity(adv + " " + cust, banned):
            continue
        out.append({"advisor": adv[-450:], "customer": cust[:500], "stages": stages, "line": cur["first"]})
    return out


class ExampleIndex:
    def __init__(self, exchanges: list[dict]):
        self.items = exchanges
        docs = [Counter(_tokens(e["advisor"])) for e in exchanges]
        df: Counter = Counter()
        for d in docs:
            df.update(d.keys())
        n = max(1, len(docs))
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.vecs = [self._vec(d) for d in docs]

    def _vec(self, counts: Counter) -> dict[str, float]:
        v = {t: (1 + math.log(c)) * self.idf.get(t, math.log(len(self.items) + 1)) for t, c in counts.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {t: x / norm for t, x in v.items()}

    def search(self, text: str, k: int = 5, min_score: float = 0.12) -> list[tuple[float, dict]]:
        if not self.items:
            return []
        q = self._vec(Counter(_tokens(text)))
        if not q:
            return []
        scored = []
        for v, item in zip(self.vecs, self.items):
            s = sum(w * v.get(t, 0.0) for t, w in q.items())
            if s >= min_score:
                scored.append((s, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]

    def by_stage(self, n: int, k: int = 3) -> list[dict]:
        hits = [e for e in self.items if n in e["stages"]]
        if len(hits) <= k:
            return hits
        step = len(hits) / k                  # spread across the stage, not just its start
        return [hits[int(i * step)] for i in range(k)]

    def guess_stage(self, hits: list[tuple[float, dict]]) -> int | None:
        votes: Counter = Counter()
        for score, item in hits:
            for n in item["stages"]:
                votes[n] += score
        return votes.most_common(1)[0][0] if votes else None
