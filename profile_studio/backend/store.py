"""Where everything lives on disk, and reading it back."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any

STUDIO_DIR = Path(__file__).resolve().parent.parent          # profile_studio/
ROOT = STUDIO_DIR.parent                                      # Test_Customer_Scripts/
AUDIT_DIR = ROOT / "audit_output"
CAT_DIR = ROOT / "cat_output"
PROFILES_DIR = ROOT / "profiles_output"
SETTINGS_PATH = STUDIO_DIR / "settings.json"
FRONTEND_DIST = STUDIO_DIR / "frontend" / "dist"

DEFAULT_SETTINGS: dict[str, Any] = {
    # Models. "auto" quality uses the high-quality set for complex / vulnerable cases.
    "model_segment": "gpt-6.1-sol",        # long-context: maps 32 stages onto a 1-3 hour transcript
    "model_stage": "gpt-6.1-terra",        # per-stage behaviour analysis (standard, faster)
    "model_stage_high": "gpt-6.1-sol",     # per-stage behaviour analysis (complex / vulnerable cases)
    "model_persona": "gpt-6.1-sol",        # final customer profile (standard)
    "model_persona_high": "gpt-6.1-sol",   # final customer profile (complex / vulnerable cases)
    "quality": "auto",                   # auto | standard | high
    "anonymise": True,                   # replace real identity details in the persona
    "lookup_days": 60,                   # default date window when searching by case number
    "agent_personas_path": r"C:\AI_Call_Agent\customer_agent\data\personas\transcript_personas.json",
    "parallel_stage_calls": 4,
}

_lock = threading.Lock()


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def get_settings() -> dict[str, Any]:
    return {**DEFAULT_SETTINGS, **(read_json(SETTINGS_PATH, {}) or {})}


def save_settings(update: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        current = get_settings()
        for k, v in update.items():
            if k in DEFAULT_SETTINGS:
                current[k] = v
        write_json(SETTINGS_PATH, current)
        return current


def clean_ref(ref: str) -> str:
    """'435898-W' -> '435898'; strips spaces."""
    return re.sub(r"-[A-Za-z]+$", "", str(ref or "").strip())


def ref_of_folder(folder: Path) -> str:
    return folder.name.split("_", 1)[0]


# ---------------------------------------------------------------- audit data
def audit_customer_dirs(ref: str) -> list[Path]:
    base = AUDIT_DIR / "customers"
    if not base.exists():
        return []
    return [p for p in sorted(base.glob(f"{ref}_*")) if p.is_dir()]


def call_dirs(ref: str) -> list[Path]:
    dirs = []
    for cdir in audit_customer_dirs(ref):
        dirs += [p for p in cdir.glob("call_*") if (p / "details.json").exists()]
    return dirs


def load_call(ref: str, call_id: int | str) -> dict | None:
    for d in call_dirs(ref):
        if d.name == f"call_{call_id}":
            return read_json(d / "details.json")
    return None


def calls_for_case(ref: str) -> list[dict]:
    """Light call summaries for a case, oldest first."""
    out = []
    for d in call_dirs(ref):
        rec = read_json(d / "case_record.json", {}) or {}
        rep = read_json(d / "audit_report.json", {}) or {}
        tr_path = d / "transcript.txt"
        out.append({
            "call_id": rec.get("call_id") or int(d.name.split("_")[1]),
            "case_id": rec.get("case_id"),
            "customer_name": (rec.get("customer_name") or "").strip(),
            "advisor": (rec.get("advisor") or {}).get("name"),
            "call_date": rec.get("call_date"),
            "call_duration": rec.get("call_duration"),
            "chosen_solution": rec.get("chosen_solution"),
            "average_score": rec.get("average_score"),
            "totals": rep.get("totals"),
            "review_status": (rep.get("submitted") or {}).get("review_status"),
            "transcript_lines": sum(1 for _ in tr_path.open(encoding="utf-8")) if tr_path.exists() else 0,
            "has_errors": (d / "errors.json").exists(),
        })
    out.sort(key=lambda c: (c.get("call_date") or "", c["call_id"]))
    return out


# ---------------------------------------------------------------- CAT data
def cat_case_dir(ref: str) -> Path | None:
    base = CAT_DIR / "cases"
    if not base.exists():
        return None
    for p in sorted(base.glob(f"{ref}_*")):
        if p.is_dir():
            return p
    return None


def documents_entry(ref: str) -> dict | None:
    return (read_json(CAT_DIR / "documents_manifest.json", {}) or {}).get(ref)


def documents_dir(ref: str) -> Path | None:
    d = cat_case_dir(ref)
    return d / "documents" if d and (d / "documents").exists() else None


def load_cat(ref: str) -> dict | None:
    d = cat_case_dir(ref)
    return read_json(d / "case_export.json") if d else None


# ---------------------------------------------------------------- profiles
def profile_dir(ref: str) -> Path:
    return PROFILES_DIR / ref


def profile_status(ref: str) -> dict:
    meta = read_json(profile_dir(ref) / "meta.json", None)
    if not meta:
        return {"status": "none"}
    return {k: meta.get(k) for k in ("status", "created_at", "updated_at", "models", "quality", "error", "anonymised")}


# ---------------------------------------------------------------- listing
def list_local_cases() -> list[dict]:
    refs: dict[str, dict] = {}

    def entry(ref: str) -> dict:
        return refs.setdefault(ref, {"ref": ref, "name": "", "calls": 0, "last_call": None,
                                     "has_audit": False, "has_cat": False, "documents": 0,
                                     "has_docs": False, "doc_files": 0})

    base = AUDIT_DIR / "customers"
    if base.exists():
        for cdir in base.iterdir():
            if not cdir.is_dir():
                continue
            calls = [p for p in cdir.glob("call_*") if (p / "details.json").exists()]
            if not calls:
                continue
            e = entry(ref_of_folder(cdir))
            e["has_audit"] = True
            e["calls"] += len(calls)
            for c in calls:
                rec = read_json(c / "case_record.json", {}) or {}
                if not e["name"]:
                    e["name"] = (rec.get("customer_name") or "").strip()
                d = rec.get("call_date")
                if d and (not e["last_call"] or d > e["last_call"]):
                    e["last_call"] = d

    cat_manifest = read_json(CAT_DIR / "manifest.json", {}) or {}
    for ref, m in cat_manifest.items():
        e = entry(ref)
        e["has_cat"] = True
        e["documents"] = m.get("documents", 0)
        if m.get("customer_name"):
            e["name"] = m["customer_name"].strip()   # CAT name is the cleanest spelling

    docs_manifest = read_json(CAT_DIR / "documents_manifest.json", {}) or {}
    for ref, m in docs_manifest.items():
        e = entry(ref)
        e["has_docs"] = True
        e["doc_files"] = m.get("files", 0)
        if not e["name"] and m.get("customer_name"):
            e["name"] = m["customer_name"].strip()

    if PROFILES_DIR.exists():
        for p in PROFILES_DIR.iterdir():
            if p.is_dir():
                entry(p.name)

    for ref, e in refs.items():
        e["profile"] = profile_status(ref)
    return sorted(refs.values(), key=lambda e: (e["last_call"] or ""), reverse=True)
