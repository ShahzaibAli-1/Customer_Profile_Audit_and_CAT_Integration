"""Build a customer profile from a case's calls (Audit tool) and case data (CAT tool).

Steps, per case:
  1. Segment   - for every call, mark where each of the 32 stages happens in the
                 transcript (long-context model, audit timestamps used as hints).
  2. Analyse   - for every stage that happened, describe how the advisor ran it and
                 how the customer behaved (tone, cooperation, disclosure, quotes, facts).
  3. Synthesise- merge everything with the CAT case data into a customer persona in
                 the AI Call Agent's transcript_personas.json format, including a
                 stage-by-stage behaviour guide.

Everything is saved to profiles_output/<case>/.
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

import store
from . import llm
from .stages import STAGE_BY_N, STAGES, stage_list_text
from .transcript import (ADVISOR, CUSTOMER, audit_by_stage, build_lines, clock, crm_checks,
                         guess_roles, lines_in_seconds, render)

Log = Callable[[str], None]
Progress = Callable[[int, int, str], None]

PROFILE_FIELDS = [
    "name", "date_of_birth", "address_line", "postcode", "email", "gender", "mood", "personality",
    "total_debt", "monthly_contractual_payments", "affordable_payment", "biggest_concern",
    "trigger_point", "debt_cause", "duration", "previous_solutions", "temporary_payment_plan_details",
    "tried_so_far", "daily_impact", "family_discussed", "family_financial_help", "partner_income_support",
    "third_party_help", "vulnerability", "medical_support", "partner_status", "dependents", "housing",
    "household", "employment", "employer", "benefits", "future_changes", "assets", "income", "expenses",
    "creditors", "joint_debts", "guarantor_debts", "priority_bills", "priority_bills_last_checked",
    "loan_position", "loan_decline_reason", "collection_activity", "additional_debt_checks",
    "recent_account_spending", "preferential_payments", "additional_debts",
    "consent_to_recording", "consent_to_note_vulnerability", "behavior_guide",
]
ADDITIONAL_DEBT_KEYS = ["overdraft", "hmrc", "council_tax", "dwp", "vat", "parking_fines", "court_fines",
                        "family_friends", "previous_rent_arrears", "current_rent_arrears", "student_loans",
                        "hire_purchase", "car_lease"]
SENSITIVE_KEYS = {"account_number", "sort_code", "iban", "bic", "ni_number", "document_id", "source_doc_id",
                  "pdf_url", "screenshot_url", "account_identifier", "read_with_document_id", "search_id", "id",
                  "line_id", "finding_key"}
IDENTITY_KEYS = {"email", "phone", "dob", "date_of_birth", "address", "postcode", "full_name", "first_name",
                 "last_name", "middle_name", "account_holder", "town", "previous_addresses", "searched"}
VULN_WORDS = re.compile(r"\b(anxiety|anxious|depress\w*|mental health|disab\w*|cancer|bereave\w*|passed away|"
                        r"suicid\w*|self[- ]harm|autis\w*|adhd|bipolar|ptsd|carer|chemo\w*|dyslexi\w*|"
                        r"multiple sclerosis|stroke|epilep\w*|gambl\w*|alcohol\w*|addict\w*)\b", re.I)

MAX_SEGMENT_CHARS = 900_000     # beyond this a transcript is split for segmentation
MAX_STAGE_LINES = 700


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-") or "customer"


# ======================================================================= step 1
SEGMENT_SYSTEM = """You are a senior QA analyst for a UK debt-advice company (IVA / DMP advice calls).
You read long call transcripts and mark precisely where each stage of the advisor's script happens.
Be accurate with line numbers. Answer with JSON only."""


def _segment_prompt(lines, role_hint, hints, n_lines) -> str:
    hint_rows = []
    for n in range(1, 33):
        h = hints.get(n)
        if not h:
            continue
        span = ""
        if h.get("line_span"):
            span = f" audit timestamps ≈ lines {h['line_span'][0]}-{h['line_span'][1]}"
        hint_rows.append(f"{n}. status={h.get('status')}{span}")
    return f"""Map the 32 advisor-script stages onto this debt-advice call transcript.

STAGES (in script order):
{stage_list_text()}

SPEAKERS: the transcript has two speaker letters (A, B). One is the ADVISOR, the other is the CUSTOMER.
{role_hint}
Note: calls often start with a short lead hand-over from another agent ("I've got a lead for you...",
reading out the customer's details) before the customer is connected. That person is NOT the customer.
Advisors also play pre-recorded solution explanations; those count as the advisor.

AUDIT HINTS per stage (from an automated audit of this call; time ranges converted to line numbers -
useful but often too wide or partly wrong; trust the transcript content over the hints):
{chr(10).join(hint_rows) or "none"}

Return JSON:
{{
  "advisor_speaker": "A" or "B",
  "customer_speaker": "A" or "B",
  "speaker_reasoning": "one sentence",
  "non_customer_spans": [{{"start": <line>, "end": <line>, "what": "e.g. lead hand-over by opener / hold / recording"}}],
  "stages": [
    {{"n": 1, "covered": true, "spans": [[<first line>, <last line>]], "note": "short note, e.g. 'brief', 'revisited later'"}},
    ... one object for EVERY stage 1-32 ...
  ]
}}
Rules: line numbers are the numbers before the first "|" (0 to {n_lines - 1}). A stage may have several spans
if it is revisited. Spans should be tight (just the lines where that stage is discussed). If a stage
did not happen in this call, set "covered": false and "spans": [].

TRANSCRIPT (line|time|speaker|text):
"""


def segment_call(lines, roles_guess, role_how, audit, model, usage, log) -> dict:
    n = len(lines)
    hints = {}
    for k, a in audit.items():
        idx = lines_in_seconds(lines, a.get("ranges") or [])
        hints[k] = {"status": a.get("status"), "line_span": [min(idx), max(idx)] if idx else None}
    if roles_guess:
        adv = [s for s, r in roles_guess.items() if r == ADVISOR]
        role_hint = (f"Hint from audit evidence ({role_how}): speaker {adv[0]} is probably the ADVISOR. "
                     f"Verify from the content.") if adv else ""
    else:
        role_hint = "Work out who is who from the content."
    body = render(lines)
    if len(body) > MAX_SEGMENT_CHARS:
        log(f"    transcript very long ({len(body):,} chars) - trimming for segmentation")
        body = body[:MAX_SEGMENT_CHARS]
    prompt = _segment_prompt(lines, role_hint, hints, n) + body
    data = llm.chat_json(model, SEGMENT_SYSTEM, prompt, usage, effort="low")

    stages = {}
    for st in data.get("stages") or []:
        try:
            k = int(st.get("n"))
        except (TypeError, ValueError):
            continue
        if k not in STAGE_BY_N:
            continue
        spans = []
        for sp in st.get("spans") or []:
            try:
                a, b = int(sp[0]), int(sp[1])
            except (TypeError, ValueError, IndexError):
                continue
            a, b = max(0, min(a, b)), min(n - 1, max(a, b))
            if a <= b:
                spans.append([a, b])
        stages[k] = {"covered": bool(st.get("covered")) and bool(spans), "spans": spans,
                     "note": st.get("note") or "", "source": "llm"}
    # Stages the model left out: fall back to tight audit hints.
    for k in range(1, 33):
        if k in stages:
            continue
        h = hints.get(k) or {}
        sp = h.get("line_span")
        if sp and sp[1] - sp[0] <= 150 and (audit.get(k, {}).get("status") or "").lower() not in ("omitted", "n/a"):
            stages[k] = {"covered": True, "spans": [sp], "note": "from audit timestamps", "source": "audit"}
        else:
            stages[k] = {"covered": False, "spans": [], "note": "", "source": "none"}

    adv = str(data.get("advisor_speaker") or "").strip().upper()[:1]
    speakers = {ln["speaker"] for ln in lines}
    if roles_guess and len(set(roles_guess.values())) == 2:
        roles, how = roles_guess, role_how
        if adv and roles_guess.get(adv) != ADVISOR:
            log(f"    note: model thought speaker {adv} is the advisor; keeping the audit evidence ({role_how})")
    elif adv in speakers:
        roles = {s: (ADVISOR if s == adv else CUSTOMER) for s in speakers}
        how = "model: " + str(data.get("speaker_reasoning") or "")[:200]
    else:
        roles = {s: f"SPEAKER_{s}" for s in speakers}
        how = "unknown"
    non_customer = []
    for sp in data.get("non_customer_spans") or []:
        try:
            non_customer.append({"start": int(sp["start"]), "end": int(sp["end"]), "what": str(sp.get("what", ""))})
        except (KeyError, TypeError, ValueError):
            pass
    return {"roles": roles, "roles_how": how, "stages": stages, "non_customer_spans": non_customer}


# ======================================================================= step 2
STAGE_SYSTEM = """You are an expert in UK debt-advice call quality and customer behaviour.
You study one stage of a real advisor-customer call and describe, precisely and with evidence,
how the advisor handled it and how the CUSTOMER behaved, so a training simulator can reproduce
this exact customer. Quote the customer verbatim. Never invent facts. Answer with JSON only."""


def _stage_prompt(stage, audit_entry, excerpt, call_info, notes) -> str:
    audit_txt = "No audit result for this stage."
    if audit_entry:
        subs = "\n".join(f"  - [{s['status']}] {s['question']}" for s in audit_entry.get("sub_questions") or [])
        audit_txt = (f"Audit status: {audit_entry.get('status')} "
                     f"(score {audit_entry.get('score')}/{audit_entry.get('max_score')})\n"
                     f"Audit question: {audit_entry.get('question', '')[:600]}\n"
                     f"Sub-questions:\n{subs or '  none'}\n"
                     f"Auditor reasoning (may be about the whole call):\n{audit_entry.get('reasoning', '')}")
    return f"""STAGE {stage['n']}: {stage['name']}
What this stage is: {stage['desc']}

CALL: {call_info}
{notes}

AUDIT FOR THIS STAGE:
{audit_txt}

TRANSCRIPT EXCERPT FOR THIS STAGE (line|time|speaker|text):
{excerpt}

Return JSON:
{{
  "happened": true/false,
  "summary": "2-3 sentences: what happened in this stage",
  "advisor": {{
    "approach": "how the advisor ran this stage",
    "questions_asked": ["key questions/statements, close to verbatim"],
    "handled_well": ["..."],
    "missed_or_weak": ["..."]
  }},
  "customer": {{
    "behaviour": "2-4 sentences on how the customer behaved",
    "tone": "e.g. calm, anxious, defensive, flat, chatty",
    "emotions": ["..."],
    "cooperation": "high | medium | low",
    "disclosure_style": "e.g. volunteers detail / short answers until probed / vague / evasive",
    "answer_length": "short | medium | long | mixed",
    "speech_patterns": ["fillers, phrases, habits actually heard"],
    "objections_or_resistance": ["..."],
    "triggers": ["what made them emotional, confused, defensive or more open"],
    "confusion_or_questions": ["questions the customer asked / things they did not understand"],
    "quotes": ["up to 5 short verbatim customer lines that capture how they speak"]
  }},
  "facts_disclosed": [{{"topic": "e.g. income / creditor / health", "fact": "..."}}],
  "interaction_dynamic": "how advisor and customer affected each other in this stage",
  "simulation_guidance": "Second person ('You...'): exactly how a simulated version of this customer should behave and answer in this stage",
  "confidence": "high | medium | low"
}}"""


def analyse_stage(stage, audit_entry, lines, roles, spans, call_info, model, usage) -> dict:
    idx: list[int] = []
    for a, b in spans:
        idx += list(range(max(0, a - 2), min(len(lines), b + 3)))
    idx = sorted(set(idx))
    notes = ""
    if len(idx) > MAX_STAGE_LINES:
        half = MAX_STAGE_LINES // 2
        idx = idx[:half] + idx[-half:]
        notes = "(Excerpt is long; the middle part was left out.)"
    excerpt = render(lines, roles, idx)
    return llm.chat_json(model, STAGE_SYSTEM, _stage_prompt(stage, audit_entry, excerpt, call_info, notes),
                         usage, effort="low")


# ======================================================================= step 3
PERSONA_SYSTEM = """You build realistic customer personas for a UK debt-advice training simulator
(the simulator plays the customer; a trainee advisor runs the call). You base every detail on the
evidence given: real call behaviour per stage, and the case data from the company's systems.
The persona must make the simulated customer behave like the real one did in each stage.
Answer with JSON only."""


def _strip(obj, drop_identity: bool, depth: int = 0):
    if depth > 7:
        return None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if kl in SENSITIVE_KEYS or (drop_identity and kl in IDENTITY_KEYS) or kl.startswith("_availability"):
                continue
            v2 = _strip(v, drop_identity, depth + 1)
            if v2 not in (None, "", [], {}):
                out[k] = v2
        return out
    if isinstance(obj, list):
        items = [_strip(v, drop_identity, depth + 1) for v in obj[:60]]
        return [i for i in items if i not in (None, "", [], {})]
    if isinstance(obj, str):
        return obj[:400]
    return obj


def condense_cat(cat: dict | None, anonymise: bool) -> dict:
    if not cat:
        return {}
    out: dict = {"status": (cat.get("_meta") or {}).get("status")}
    fields = {}
    for f in cat.get("fields") or []:
        if not isinstance(f, dict) or f.get("value") in (None, "", [], {}):
            continue
        if anonymise and f.get("field_id") in {"full_name", "first_name", "middle_name", "last_name", "dob",
                                               "date_of_birth", "address", "postcode", "email", "phone",
                                               "mobile", "ni_number"}:
            continue
        fields[f"{f.get('section')}.{f.get('label')}"] = f.get("value")
    out["fields"] = fields
    tables = []
    for t in cat.get("tables") or []:
        if isinstance(t, dict):
            tables.append({"label": t.get("label"), "total_crm": t.get("total_crm"),
                           "total_verified": t.get("total_verified"),
                           "rows": _strip(t.get("rows") or [], anonymise)[:60]})
    out["tables"] = tables
    cp = cat.get("case_profile") or {}
    out["case_profile"] = _strip({k: cp.get(k) for k in ("household", "debts", "assets", "iva_schedule",
                                                         "household_budget", "partner_awareness",
                                                         "income_working_out")}, anonymise)
    apps = []
    for a in cp.get("applicants") or []:
        apps.append(_strip({"person": a.get("person"), "employment": a.get("employment"),
                            "income": {"total_monthly": (a.get("income") or {}).get("total_monthly")},
                            "personal": {k: (a.get("personal") or {}).get(k) for k in ("title", "gender")},
                            "idu_verdict": (a.get("idu") or {}).get("verdict")}, anonymise))
    out["applicants"] = apps
    sec = cat.get("sections") or {}
    keep = ["benefits", "universal_credit", "vehicles", "dependants", "gambling", "ccjs", "council_tax_debts",
            "employment_deductions", "financial_difficulty", "partner_third_party", "banking_transactions",
            "identity_checks", "property_assets", "other_arrears", "self_employed", "director", "drafter_notes"]
    out["sections"] = _strip({k: sec.get(k) for k in keep if k in sec}, anonymise)
    out["call_sheet_handover"] = _strip(cat.get("call_sheet_handover"), anonymise)
    out["documents"] = [{"type": d.get("document_type"), "status": d.get("processing_status")}
                        for d in cat.get("documents") or [] if isinstance(d, dict)]
    return out


def _persona_prompt(ref, calls, stage_digest, cat_digest, crm, anonymise) -> str:
    identity_rule = (
        "ANONYMISE: invent a realistic fictional UK name (same gender and similar age/background), date of "
        "birth (same age within 2 years), address, postcode (same region) and email at example.com. Never use "
        "the real name, DOB, address, phone or email. Keep every financial, household and behavioural fact."
        if anonymise else
        "Use the real identity details from the evidence (name, DOB, address, postcode, email)."
    )
    schema_profile = ",\n    ".join(f'"{f}": ...' for f in PROFILE_FIELDS if f not in ("additional_debts", "behavior_guide"))
    return f"""Build the training persona for case {ref}.

{identity_rule}

CALLS: {json.dumps(calls, ensure_ascii=False)}

STAGE-BY-STAGE EVIDENCE FROM THE REAL CALLS (advisor handling + customer behaviour + facts):
{stage_digest}

CASE DATA FROM THE CAT TOOL (verified figures from documents; prefer these numbers):
{json.dumps(cat_digest, ensure_ascii=False)[:60000] if cat_digest else "not available"}

CRM CHECKS FROM THE AUDIT:
{json.dumps(crm, ensure_ascii=False)[:8000] if crm else "none"}

Return JSON:
{{
  "label": "persona display name (the persona's name)",
  "segment": "one-line situation, e.g. 'Single parent on UC, ex-partner debts'",
  "risk": "main risk / vulnerability in one line",
  "stage": "the stage where this customer is hardest to handle (one of the 32 stage names)",
  "source_pattern": "one sentence: what real call this is based on (no real names if anonymising)",
  "difficulty": "easy | medium | hard",
  "overall": {{
    "personality_summary": "...",
    "communication_style": "...",
    "emotional_arc": "how their mood changed from start to end of the call",
    "trust_building": "what made them open up / close down",
    "key_challenges_for_advisor": ["..."]
  }},
  "profile": {{
    {schema_profile},
    "creditors": ["one line per creditor: name, balance, monthly payment, arrears/status"],
    "additional_debts": {{ one key per category that applies, from: {", ".join(ADDITIONAL_DEBT_KEYS)} -> "Yes/No, detail" }},
    "consent_to_recording": true/false,
    "consent_to_note_vulnerability": true/false,
    "behavior_guide": "Several paragraphs, written like the examples below: background, how they speak, pace of disclosure, emotional triggers, resistance, typical phrases. Must reflect the real customer."
  }},
  "stage_behaviour": [
    {{"n": 1, "name": "DPA", "how_customer_behaves": "2-4 sentences, second person, concrete (what you say, how, how long)", "key_lines": ["typical things to say"]}},
    ... one entry for EVERY stage 1-32; if a stage did not happen in the real call, infer from the customer's overall behaviour and say so ...
  ]
}}

Style reference for behavior_guide: "Abigail speaks quietly and directly. She does not volunteer information
unprompted - wait for the advisor to ask. When asked her biggest concern, she says something like 'peace,
just peace'... Pace of disclosure: slow at first (short answers), then more detailed once she trusts the
advisor... Typical speech: hesitant, grammatically simple, honest. Uses 'sort of', 'I mean'."

FORMAT RULES (the simulator reads these fields as plain text):
- Every profile field is a plain-English STRING of 1-3 sentences, written the way the customer would say it
  ("about 9,600 pounds in total, the verified figure is 8,927 pounds"). NEVER an object, list or JSON -
  except "creditors" (list of strings), "additional_debts" (object of strings) and the two consent booleans.
- date_of_birth like "19 August 1992". gender is "female" or "male".
- All money as e.g. "1,250 pounds". Use the customer's own words where possible. Facts must be consistent
  across fields. Where the real call and the documents disagree, say what the customer SAID on the call
  (that is how the simulated customer answers) and mention the verified figure in brackets.
- behavior_guide: 4-7 focused paragraphs, at most about 4,000 characters. Do NOT repeat the stage-by-stage
  detail there - that goes in stage_behaviour.
- stage_behaviour: each "how_customer_behaves" is at most 3 sentences; up to 3 key_lines."""


def stage_digest(stages_out: list[dict]) -> str:
    parts = []
    for st in stages_out:
        analyses = [a for a in st.get("calls", []) if a.get("analysis")]
        if not analyses:
            parts.append(f"## {st['n']}. {st['name']}: not covered in the calls")
            continue
        for a in analyses:
            an = a["analysis"]
            c = an.get("customer") or {}
            parts.append(
                f"## {st['n']}. {st['name']} (call {a['call_id']}, audit: {a.get('audit_status')})\n"
                f"Summary: {an.get('summary')}\n"
                f"Advisor: {(an.get('advisor') or {}).get('approach')}\n"
                f"Customer: {c.get('behaviour')} | tone={c.get('tone')} | cooperation={c.get('cooperation')} | "
                f"disclosure={c.get('disclosure_style')} | length={c.get('answer_length')}\n"
                f"Emotions: {c.get('emotions')} Triggers: {c.get('triggers')} Resistance: {c.get('objections_or_resistance')}\n"
                f"Speech: {c.get('speech_patterns')} Quotes: {c.get('quotes')}\n"
                f"Facts: {an.get('facts_disclosed')}\n"
                f"Guidance: {an.get('simulation_guidance')}"
            )
    return "\n\n".join(parts)


def _as_text(v) -> str:
    """Flatten whatever the model returned into readable text (never JSON)."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (int, float)):
        return f"{v:,}"
    if isinstance(v, list):
        return "; ".join(t for t in (_as_text(x) for x in v) if t)
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            t = _as_text(x)
            if t:
                parts.append(f"{str(k).replace('_', ' ')}: {t}")
        return "; ".join(parts)
    return str(v)


def _clean_profile(p: dict) -> dict:
    out = {}
    for f in PROFILE_FIELDS:
        v = p.get(f)
        if f == "creditors":
            v = [_as_text(c) for c in (v if isinstance(v, list) else [v] if v else []) if _as_text(c)]
        elif f == "additional_debts":
            v = {k: _as_text(x) for k, x in (v or {}).items() if k in ADDITIONAL_DEBT_KEYS and _as_text(x)}
        elif f in ("consent_to_recording", "consent_to_note_vulnerability"):
            v = bool(v) if v is not None else True
        elif f == "gender":
            v = _as_text(v).lower()
        else:
            v = _as_text(v)
        out[f] = v
    return out


def compose_behavior_guide(guide: str, overall: dict, stage_beh: list[dict]) -> str:
    parts = [guide.strip()] if guide else []
    if overall:
        o = []
        for k, label in (("communication_style", "Communication style"), ("emotional_arc", "Emotional arc"),
                         ("trust_building", "Trust")):
            if overall.get(k):
                o.append(f"{label}: {overall[k]}")
        if o:
            parts.append("\n".join(o))
    if stage_beh:
        lines = ["STAGE-BY-STAGE BEHAVIOUR (follow for each part of the call):"]
        for s in sorted(stage_beh, key=lambda s: s.get("n") or 0):
            name = STAGE_BY_N.get(s.get("n"), {}).get("name", s.get("name", ""))
            row = f"{s.get('n')}. {name}: {s.get('how_customer_behaves', '').strip()}"
            if s.get("key_lines"):
                row += " Typical lines: " + " / ".join(f'"{x}"' for x in s["key_lines"][:3])
            lines.append(row)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ======================================================================= main
def choose_quality(settings: dict, calls_lines: dict, cat: dict | None) -> tuple[str, str]:
    q = settings.get("quality", "auto")
    if q in ("standard", "high"):
        return q, "chosen in settings"
    total = sum(len(v["lines"]) for v in calls_lines.values())
    cust_text = " ".join(ln["text"] for v in calls_lines.values() for ln in v["lines"]
                         if v["roles"].get(ln["speaker"]) != ADVISOR)
    vuln = len(set(m.group(0).lower() for m in VULN_WORDS.finditer(cust_text)))
    reasons = []
    if vuln >= 2:
        reasons.append(f"vulnerability signals ({vuln} kinds mentioned)")
    if total > 2500:
        reasons.append(f"long call ({total} lines)")
    if len(calls_lines) > 2:
        reasons.append(f"{len(calls_lines)} calls")
    if reasons:
        return "high", "auto: " + ", ".join(reasons)
    return "standard", "auto: straightforward case"


def build_profile(ref: str, settings: dict, log: Log, progress: Progress, anonymise: bool | None = None,
                  quality: str | None = None, persona_only: bool = False) -> dict:
    ref = store.clean_ref(ref)
    if quality:
        settings = {**settings, "quality": quality}
    anonymise = settings.get("anonymise", True) if anonymise is None else anonymise
    pdir = store.profile_dir(ref)
    pdir.mkdir(parents=True, exist_ok=True)
    meta = store.read_json(pdir / "meta.json", {}) or {}
    meta.update({"ref": ref, "status": "running", "started_at": _now(), "error": None, "anonymised": anonymise})
    store.write_json(pdir / "meta.json", meta)
    usage = llm.Usage()

    try:
        calls = store.calls_for_case(ref)
        if not calls:
            raise RuntimeError("No Audit tool calls fetched for this case yet - fetch the case first.")
        cat = store.load_cat(ref)
        log(f"Case {ref}: {len(calls)} call(s); CAT data {'found' if cat else 'not found'}")

        # ---- prepare calls
        prepared: dict[int, dict] = {}
        for c in calls:
            details = store.load_call(ref, c["call_id"]) or {}
            lines = build_lines(details.get("transcript") or [])
            if not lines:
                log(f"  call {c['call_id']}: no transcript - skipped")
                continue
            report = details.get("report") or {}
            roles, how = guess_roles(details.get("transcript") or [], report)
            prepared[c["call_id"]] = {"info": c, "lines": lines, "roles": roles, "roles_how": how,
                                      "audit": audit_by_stage(report), "crm": crm_checks(report),
                                      "record": details.get("call") or {}}
        if not prepared:
            raise RuntimeError("None of the calls has a transcript.")

        qual, qual_reason = choose_quality(settings, prepared, cat)
        m_seg = settings["model_segment"]
        m_stage = settings["model_stage_high"] if qual == "high" else settings["model_stage"]
        m_persona = settings["model_persona_high"] if qual == "high" else settings["model_persona"]
        log(f"Quality: {qual} ({qual_reason}). Models: segment={m_seg}, stages={m_stage}, persona={m_persona}")
        meta.update({"quality": qual, "quality_reason": qual_reason,
                     "models": {"segment": m_seg, "stage": m_stage, "persona": m_persona}})
        store.write_json(pdir / "meta.json", meta)

        reuse = persona_only and (pdir / "stages.json").exists()
        if reuse:
            stages_out = store.read_json(pdir / "stages.json", [])
            total_steps, done = 1, 0
            errors = {(c["call_id"], st["n"]): c["error"] for st in stages_out for c in st.get("calls", [])
                      if c.get("error")}
            log("Re-using the saved stage analysis - rebuilding the customer profile only")
            meta["models"]["segment"] = (meta.get("models") or {}).get("segment", m_seg)
        else:
            total_steps = len(prepared) + 1   # segmentation + persona; stage count added later
            done = 0
            progress(done, total_steps, "Segmenting calls into stages")

            # ---- step 1: segmentation
            for cid, p in prepared.items():
                log(f"[1/3] Call {cid}: mapping 32 stages onto {len(p['lines'])} transcript lines ...")
                seg = segment_call(p["lines"], p["roles"], p["roles_how"], p["audit"], m_seg, usage, log)
                p["roles"], p["roles_how"], p["seg"] = seg["roles"], seg["roles_how"], seg
                covered = sum(1 for s in seg["stages"].values() if s["covered"])
                log(f"    speakers: {seg['roles']} ({seg['roles_how'][:80]}); {covered}/32 stages found")
                store.write_json(pdir / "calls" / f"{cid}.json", {
                    "call_id": cid, "info": p["info"], "roles": seg["roles"], "roles_how": seg["roles_how"],
                    "non_customer_spans": seg["non_customer_spans"],
                    "stages": {str(k): v for k, v in sorted(seg["stages"].items())},
                    "lines": p["lines"],
                })
                done += 1
                progress(done, total_steps, f"Segmented call {cid}")

            # ---- step 2: stage analysis
            tasks = []
            for cid, p in prepared.items():
                info = p["info"]
                call_info = (f"call {cid} on {str(info.get('call_date'))[:10]}, advisor {info.get('advisor')}, "
                             f"chosen solution {info.get('chosen_solution')}, audit score "
                             f"{(info.get('totals') or {}).get('score')}/{(info.get('totals') or {}).get('max')}")
                for st in STAGES:
                    s = p["seg"]["stages"].get(st["n"]) or {}
                    if s.get("covered") and s.get("spans"):
                        tasks.append((cid, st, s["spans"], call_info))
            total_steps += len(tasks)
            log(f"[2/3] Analysing {len(tasks)} stage excerpts with {m_stage} ...")
            progress(done, total_steps, "Analysing stages")
            results: dict[tuple[int, int], dict] = {}
            errors: dict[tuple[int, int], str] = {}
            workers = max(1, int(settings.get("parallel_stage_calls", 4)))
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(analyse_stage, st, prepared[cid]["audit"].get(st["n"]), prepared[cid]["lines"],
                                  prepared[cid]["roles"], spans, call_info, m_stage, usage): (cid, st["n"])
                        for cid, st, spans, call_info in tasks}
                for f in as_completed(futs):
                    key = futs[f]
                    try:
                        results[key] = f.result()
                    except Exception as exc:  # noqa: BLE001
                        errors[key] = str(exc)[:300]
                        log(f"    stage {key[1]} (call {key[0]}) failed: {errors[key]}")
                    done += 1
                    progress(done, total_steps, f"Stage {key[1]} - {STAGE_BY_N[key[1]]['name']} (call {key[0]})")

            stages_out = []
            for st in STAGES:
                entry = {"n": st["n"], "key": st["key"], "name": st["name"], "group": st["group"], "calls": []}
                for cid, p in prepared.items():
                    s = p["seg"]["stages"].get(st["n"]) or {}
                    a = p["audit"].get(st["n"]) or {}
                    if not s.get("covered") and not a:
                        continue
                    entry["calls"].append({
                        "call_id": cid,
                        "covered": bool(s.get("covered")),
                        "spans": s.get("spans") or [],
                        "time": [clock(p["lines"][sp[0]]["start"]) + "-" + clock(p["lines"][sp[1]]["end"])
                                 for sp in s.get("spans") or []],
                        "segment_note": s.get("note"),
                        "audit_status": a.get("status"),
                        "audit_score": [a.get("score"), a.get("max_score")] if a else None,
                        "audit_question": a.get("question"),
                        "audit_reasoning": a.get("reasoning"),
                        "audit_sub_questions": a.get("sub_questions"),
                        "analysis": results.get((cid, st["n"])),
                        "error": errors.get((cid, st["n"])),
                    })
                stages_out.append(entry)
            store.write_json(pdir / "stages.json", stages_out)

        # ---- step 3: persona
        log(f"[3/3] Building the customer profile with {m_persona} ...")
        calls_brief = [{"call_id": cid, "date": str(p["info"].get("call_date"))[:10],
                        "advisor": p["info"].get("advisor"), "chosen_solution": p["info"].get("chosen_solution"),
                        "audit": p["info"].get("totals"),
                        "audit_state": p["info"].get("review_status") or "draft / not submitted (scores unreliable)",
                        "transcript_lines": len(p["lines"])} for cid, p in prepared.items()]
        crm = [c for p in prepared.values() for c in p["crm"]]
        prompt = _persona_prompt(ref, calls_brief, stage_digest(stages_out), condense_cat(cat, anonymise), crm, anonymise)
        persona_raw = llm.chat_json(m_persona, PERSONA_SYSTEM, prompt, usage, effort="medium")
        profile = _clean_profile(persona_raw.get("profile") or {})
        stage_beh = [s for s in persona_raw.get("stage_behaviour") or [] if isinstance(s, dict)]
        overall = persona_raw.get("overall") or {}
        profile["behavior_guide"] = compose_behavior_guide(profile.get("behavior_guide", ""), overall, stage_beh)
        name = profile.get("name") or persona_raw.get("label") or f"Customer {ref}"
        persona_row = {
            "id": f"{_slug(name)}-{ref}",
            "label": persona_raw.get("label") or name,
            "segment": persona_raw.get("segment") or "",
            "risk": persona_raw.get("risk") or "",
            "stage": persona_raw.get("stage") or "",
            "source_pattern": f"Real SFM case {ref}: " + (persona_raw.get("source_pattern") or ""),
            "profile": profile,
        }
        store.write_json(pdir / "persona.json", persona_row)
        store.write_json(pdir / "persona_full.json", {
            "persona": persona_row, "difficulty": persona_raw.get("difficulty"), "overall": overall,
            "stage_behaviour": stage_beh,
        })
        done += 1
        progress(done, total_steps, "Profile ready")

        meta.update({"status": "ready", "updated_at": _now(), "created_at": meta.get("created_at") or _now(),
                     "usage": usage.by_model, "calls": list(prepared), "stage_errors": len(errors),
                     "persona_id": persona_row["id"]})
        store.write_json(pdir / "meta.json", meta)
        log(f"Done: profile '{persona_row['label']}' ({persona_row['id']}). Tokens: {usage.by_model}")
        return meta
    except Exception as exc:
        meta.update({"status": "failed", "error": str(exc)[:1000], "updated_at": _now(), "usage": usage.by_model})
        store.write_json(pdir / "meta.json", meta)
        raise
