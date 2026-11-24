"""The documents a customer really has (from their CAT document vault), grouped the way an advisor asks for them."""

from __future__ import annotations

import json
import mimetypes
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CAT_DIR = ROOT / "cat_output"

# What an advisor asks for -> how a customer would describe it.
CATEGORIES: dict[str, str] = {
    "photo_id": "photo ID (passport / driving licence)",
    "bank_statement": "bank statements",
    "wage_slip": "wage slips / payslips",
    "universal_credit": "Universal Credit statement / journal",
    "benefit_letter": "benefit award letters",
    "income_proof": "other proof of income",
    "creditor_letters": "letters and statements from creditors",
    "council_tax": "council tax bill",
    "utility_bill": "utility bills",
    "gamstop": "GAMSTOP confirmation",
    "other": "other documents",
}
# Made by the company, not uploaded by the customer - never "uploaded" by the simulated customer.
SYSTEM_TYPES = {"idu_report", "eiir_report", "criteria_check", "creditor_report", "credit_report", "proof_engine"}


def categorise(doc: dict, folder: str) -> str | None:
    t = (doc.get("document_type") or "").lower()
    name = (doc.get("file_name") or "").lower()
    if t in SYSTEM_TYPES or name.startswith(("idu_", "eiir_", "criteria_check")) or "creditreport" in name.replace(" ", ""):
        return None
    if t in ("photo_id", "proof_of_id", "passport", "driving_licence", "proof_of_name") or folder.lower() == "id":
        return "photo_id"
    if t == "bank_statement" or "bank" in name and "statement" in name or folder.lower().startswith("bank"):
        return "bank_statement"
    if t in ("credit_journal", "universal_credit") or re.search(r"\buc\b|universal", name):
        return "universal_credit"
    if t in ("payslip", "wage_slip") or re.search(r"pay ?slip|wage", name):
        return "wage_slip"
    if t in ("benefit_letter", "award_letter") or "award" in name:
        return "benefit_letter"
    if t == "income_proof" or folder.lower() == "income":
        return "income_proof"
    if t in ("creditor_document", "creditor_statement", "creditor_letter") or folder.lower() == "pod":
        return "creditor_letters"
    if "council" in name:
        return "council_tax"
    if t == "gamstop":
        return "gamstop"
    return "other"


def _describe(doc: dict) -> str:
    name = doc.get("file_name") or ""
    if name.lower().startswith("wamid.") or re.match(r"^[0-9]+_", name):
        return "a photo / screenshot sent from the phone"
    return name


def load_documents(ref: str) -> list[dict]:
    base = CAT_DIR / "cases"
    if not base.exists():
        return []
    for cdir in sorted(base.glob(f"{ref}_*")):
        ddir = cdir / "documents"
        meta = []
        try:
            meta = json.loads((ddir / "documents.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        files = {p.name: p for p in ddir.rglob("*") if p.is_file() and p.suffix.lower() not in (".json", ".csv")}
        out = []
        for d in meta:
            path = files.get(d.get("file_name") or "")
            if not path:
                continue
            folder = path.parent.name if path.parent != ddir else ""
            cat = categorise(d, folder)
            if not cat:
                continue
            out.append({
                "key": d.get("id") or path.name, "category": cat, "document_type": d.get("document_type"),
                "file_name": path.name, "describe": _describe(d), "uploaded_to_cat": d.get("upload_date"),
                "size": path.stat().st_size, "path": str(path),
                "mime": d.get("mime_type") or mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            })
        return out
    return []


def summary(docs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for d in docs:
        out.setdefault(d["category"], []).append(d)
    return out
