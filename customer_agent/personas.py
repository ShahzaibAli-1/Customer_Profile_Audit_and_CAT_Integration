"""Customer personas built by Customer Profile Studio (profiles_output/), loaded for the agent.

Each persona brings together:
  * persona.json        - the AI Call Agent persona (facts + behaviour guide)
  * persona_full.json   - overall personality notes and stage-by-stage behaviour
  * deep_profile.json   - the in-depth profile (personality, communication, emotions ...)
  * conversation.json   - the real call(s): used for real advisor -> customer examples
  * CAT documents       - the documents this customer really has (cat_output/.../documents)

Personas reload automatically when their files change, so a profile built in the
Studio is available to the agent without a restart.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .documents import load_documents
from .examples import ExampleIndex, build_exchanges

ROOT = Path(__file__).resolve().parent.parent
PROFILES_DIR = ROOT / "profiles_output"
CAT_DIR = ROOT / "cat_output"
AUDIT_DIR = ROOT / "audit_output"


def _read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


@dataclass
class Persona:
    id: str
    ref: str
    label: str
    segment: str
    risk: str
    stage: str
    source_pattern: str
    profile: dict
    deep: dict | None
    stage_behaviour: list[dict]
    overall: dict
    difficulty: str | None
    anonymised: bool
    examples: ExampleIndex
    documents: list[dict]
    stage_names: dict[int, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.profile.get("name") or self.label

    @property
    def first_name(self) -> str:
        return self.name.split(" ")[0] if self.name else ""

    @property
    def gender(self) -> str:
        return (self.profile.get("gender") or "").lower()


NAME_FIELDS = {"full_name", "first_name", "last_name", "surname", "middle_name", "name"}


def _identity_strings(ref: str) -> tuple[set[str], set[str]]:
    """The REAL customer's identity, to keep it out of the examples of an anonymised persona.

    Returns (name_tokens, other_tokens): names are replaced by the persona's name (with spelling
    variants, since transcripts spell names however they sound); anything else (address, postcode,
    phone, email, DOB) makes the example be dropped.
    """
    names: set[str] = set()
    surnames: set[str] = set()
    others: set[str] = set()
    cat = None
    for d in sorted((CAT_DIR / "cases").glob(f"{ref}_*")) if (CAT_DIR / "cases").exists() else []:
        cat = _read(d / "case_export.json")
        if cat:
            break
    for f in (cat or {}).get("fields") or []:
        v = f.get("value")
        if not v or f.get("section") not in ("personal_details", "partner") and f.get("field_id") not in NAME_FIELDS:
            continue
        if f.get("field_id") in ("surname", "last_name"):
            surnames.add(str(v))
        else:
            (names if f.get("field_id") in NAME_FIELDS else others).add(str(v))
    for app in ((cat or {}).get("case_profile") or {}).get("applicants") or []:
        dpa = app.get("dpa") or {}
        if dpa.get("full_name"):
            names.add(str(dpa["full_name"]))
        for k in ("address", "postcode", "date_of_birth"):
            if dpa.get(k):
                others.add(str(dpa[k]))
        for k in ("email", "phone"):
            if (app.get("personal") or {}).get(k):
                others.add(str(app["personal"][k]))
    for cdir in sorted((AUDIT_DIR / "customers").glob(f"{ref}_*")) if (AUDIT_DIR / "customers").exists() else []:
        for call in cdir.glob("call_*"):
            rec = _read(call / "case_record.json", {}) or {}
            if rec.get("customer_name"):
                names.add(str(rec["customer_name"]))

    def split(values: set[str]) -> set[str]:
        tokens: set[str] = set()
        for s in values:
            for t in re.split(r"[\s,]+", s):
                t = t.strip().lower().strip(".")
                if len(t) >= 3 and not t.isdigit() and t not in ("miss", "mrs", "mr", "ms"):
                    tokens.add(t)
            compact = re.sub(r"[^a-z0-9@.]", "", s.lower())
            if len(compact) >= 5:
                tokens.add(compact)
            for pc in re.findall(r"[a-z]{1,2}\d[a-z\d]?\s*\d[a-z]{2}", s.lower()):
                tokens.add(re.sub(r"\s", "", pc))
        return tokens

    name_kinds: dict[str, str] = {}
    for s in names:
        words = [w.lower().strip(".") for w in re.split(r"[\s,]+", s) if w.strip()]
        words = [w for w in words if w.isalpha() and len(w) >= 3 and w not in ("miss", "mrs", "mr", "ms")]
        for i, w in enumerate(words):
            kind = "first" if i == 0 else "last"
            if name_kinds.get(w) != "first":          # a word seen as a first name anywhere stays a first name
                name_kinds[w] = kind
    for s in surnames:                                # the surname field is always the surname
        for w in re.split(r"[\s,-]+", s.lower()):
            if w.isalpha() and len(w) >= 3:
                name_kinds[w] = "last"
    return name_kinds, split(others)


class PersonaStore:
    def __init__(self, profiles_dir: Path = PROFILES_DIR):
        self.dir = profiles_dir
        self._cache: dict[str, tuple[float, Persona]] = {}
        self._lock = threading.Lock()

    def _stamp(self, pdir: Path) -> float:
        return max((f.stat().st_mtime for f in pdir.glob("*.json")), default=0.0)

    def _load(self, pdir: Path) -> Persona | None:
        meta = _read(pdir / "meta.json", {}) or {}
        persona = _read(pdir / "persona.json")
        if not persona or meta.get("status") != "ready":
            return None
        ref = pdir.name
        full = _read(pdir / "persona_full.json", {}) or {}
        deep = _read(pdir / "deep_profile.json")
        conv = _read(pdir / "conversation.json")
        stages = _read(pdir / "stages.json", []) or []
        stage_names = {s["n"]: s["name"] for s in stages if isinstance(s, dict) and "n" in s}
        anonymised = bool(meta.get("anonymised", True))
        names, banned = _identity_strings(ref) if anonymised else ({}, set())
        persona_name = (persona.get("profile") or {}).get("name") or persona.get("label") or ""
        exchanges = build_exchanges(conv, banned, names, persona_name) if conv else []
        return Persona(
            id=persona["id"], ref=ref, label=persona.get("label") or persona["id"],
            segment=persona.get("segment", ""), risk=persona.get("risk", ""), stage=persona.get("stage", ""),
            source_pattern=persona.get("source_pattern", ""), profile=persona.get("profile") or {},
            deep=deep, stage_behaviour=full.get("stage_behaviour") or [], overall=full.get("overall") or {},
            difficulty=full.get("difficulty") or ((deep or {}).get("advisor_guidance") or {}).get("difficulty"),
            anonymised=anonymised, examples=ExampleIndex(exchanges), documents=load_documents(ref),
            stage_names=stage_names,
        )

    def all(self) -> list[Persona]:
        out = []
        if not self.dir.exists():
            return out
        with self._lock:
            for pdir in sorted(p for p in self.dir.iterdir() if p.is_dir()):
                stamp = self._stamp(pdir)
                cached = self._cache.get(pdir.name)
                if cached and cached[0] == stamp:
                    out.append(cached[1])
                    continue
                try:
                    persona = self._load(pdir)
                except Exception as exc:  # noqa: BLE001 - one broken profile must not hide the rest
                    print(f"[customer-agent] could not load persona {pdir.name}: {exc}")
                    persona = None
                if persona:
                    self._cache[pdir.name] = (stamp, persona)
                    out.append(persona)
                else:
                    self._cache.pop(pdir.name, None)
        return out

    def get(self, key: str) -> Persona | None:
        """By persona id ('elaine-mercer-436216') or case number ('436216')."""
        key = (key or "").strip().lower()
        for p in self.all():
            if p.id.lower() == key or p.ref == key or p.id.lower().endswith("-" + key):
                return p
        return None


STORE = PersonaStore()
