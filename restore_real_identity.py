"""Put the real customer's identity back into personas built with anonymise on.

Profiles built while `anonymise` was on (the old default) got an invented name, date of birth,
address, postcode and email (the model kept choosing "... Mercer"). Everything else in them
(finances, household, behaviour) is the real case. This swaps the invented identity for the real
one from the case's CAT export (cat_output/cases/<ref>_*/case_export.json), in persona.json,
persona_full.json, deep_profile.json and customer_profile.md, and renames the persona id
(e.g. elaine-mercer-400123 -> jane-doe-400123). No model is run.

    python restore_real_identity.py            # every anonymised profile
    python restore_real_identity.py 400123     # just these cases
    python restore_real_identity.py --dry-run  # show what would change

The originals are kept in profiles_output/<ref>/_anonymised_backup/.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILES = ROOT / "profiles_output"
CASES = ROOT / "cat_output" / "cases"
FILES_JSON = ("persona.json", "persona_full.json", "deep_profile.json")
FILES_TEXT = ("customer_profile.md",)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


def read(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def real_identity(ref: str) -> dict | None:
    """The customer's identity as CAT holds it."""
    export = None
    for d in sorted(CASES.glob(f"{ref}_*")) if CASES.exists() else []:
        export = read(d / "case_export.json")
        if export:
            break
    if not export:
        return None
    f = {x.get("field_id"): x.get("value") for x in export.get("fields") or []}
    apps = (export.get("case_profile") or {}).get("applicants") or []
    dpa = (apps[0].get("dpa") if apps else None) or {}
    full = (dpa.get("full_name") or f.get("full_name") or "").strip()
    if not full:
        return None
    words = full.split()
    dob = str(dpa.get("date_of_birth") or f.get("date_of_birth") or "")
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", dob)
    return {
        "name": full,
        "first": (f.get("first_name") or words[0]).strip(),
        "last": (f.get("surname") or words[-1]).strip(),
        "dob": f"{int(m[3])} {MONTHS[int(m[2]) - 1]} {m[1]}" if m else dob,
        "address": (dpa.get("address") or f.get("current_address") or "").strip(),
        "postcode": (dpa.get("postcode") or "").strip(),
        "email": (f.get("email") or "").strip(),
    }


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def replacements(fake: dict, real: dict) -> list[tuple[re.Pattern, str]]:
    """Longest first, so the full name goes before its parts."""
    pairs = []

    def add(old, new, words=True):
        old, new = (old or "").strip(), (new or "").strip()
        if old and new and old != new:
            pat = re.escape(old)
            pairs.append((re.compile(rf"(?<![\w@.]){pat}(?![\w@])" if words else pat), new))

    add(fake.get("email"), real["email"], words=False)
    add(fake.get("address_line"), real["address"], words=False)
    add(fake.get("date_of_birth"), real["dob"])
    add(fake.get("postcode"), real["postcode"])
    fname = (fake.get("name") or "").strip()
    add(fname, real["name"])
    parts = fname.split()
    if len(parts) >= 2:
        add(parts[0], real["first"])
        add(parts[-1], real["last"])
        add(parts[-1].upper(), real["last"].upper())
    add(slug(fname), slug(real["name"]), words=False)
    return pairs


def swap(value, pairs):
    if isinstance(value, str):
        for pat, new in pairs:
            value = pat.sub(new, value)
        return value
    if isinstance(value, list):
        return [swap(v, pairs) for v in value]
    if isinstance(value, dict):
        return {k: swap(v, pairs) for k, v in value.items()}
    return value


def restore(pdir: Path, dry: bool) -> str:
    ref = pdir.name
    meta = read(pdir / "meta.json", {}) or {}
    persona = read(pdir / "persona.json")
    if not persona:
        return f"{ref}: skipped (no persona.json)"
    if not meta.get("anonymised", True):
        return f"{ref}: already the real identity ({persona.get('label')})"
    real = real_identity(ref)
    if not real:
        return f"{ref}: SKIPPED, no identity for this case in cat_output (run fetch_case_export.py {ref} first)"
    fake = dict(persona.get("profile") or {})
    fake.setdefault("name", persona.get("label"))
    pairs = replacements(fake, real)
    if dry:
        return f"{ref}: {fake.get('name')} -> {real['name']} ({len(pairs)} replacements)"

    backup = pdir / "_anonymised_backup"
    backup.mkdir(exist_ok=True)
    for name in (*FILES_JSON, *FILES_TEXT, "meta.json"):
        src = pdir / name
        if src.exists() and not (backup / name).exists():
            shutil.copy2(src, backup / name)

    for name in FILES_JSON:
        data = read(pdir / name)
        if data is None:
            continue
        data = swap(data, pairs)
        if name == "persona.json":
            data["id"] = f"{slug(real['name'])}-{ref}"
            data["label"] = real["name"]
            prof = data.setdefault("profile", {})
            prof.update({"name": real["name"], "date_of_birth": real["dob"], "address_line": real["address"],
                         "postcode": real["postcode"], "email": real["email"]})
        (pdir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in FILES_TEXT:
        path = pdir / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            for pat, new in pairs:
                text = pat.sub(new, text)
            path.write_text(text, encoding="utf-8")
    meta.update({"anonymised": False, "identity_restored_at": datetime.now(timezone.utc).isoformat(),
                 "identity_restored_from": "cat_output case_export.json"})
    (pdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    left = sum(1 for n in (*FILES_JSON, *FILES_TEXT) if (pdir / n).exists()
               and fake.get("name") and fake["name"] in (pdir / n).read_text(encoding="utf-8"))
    return (f"{ref}: {fake.get('name')} -> {real['name']}, id {persona['id']} -> {slug(real['name'])}-{ref}"
            + (f"  (WARNING: the invented name is still in {left} file(s))" if left else ""))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    dirs = [PROFILES / a for a in args] if args else sorted(p for p in PROFILES.iterdir() if p.is_dir())
    for pdir in dirs:
        print(restore(pdir, dry) if pdir.is_dir() else f"{pdir.name}: no such profile")


if __name__ == "__main__":
    main()
