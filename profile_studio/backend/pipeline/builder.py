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
from .conversation import build_conversation, render_conversation
from .deep_profile import build_deep_profile, compact_for_persona, report_markdown
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
# Descriptive fields beyond the agent's CustomerProfile dataclass. The agent's loader keeps unknown keys
# as extra_facts and puts them in the prompt, so they shape the simulated customer too.
EXTRA_PROFILE_FIELDS = [
    "backstory", "personality_traits", "communication_style", "speech_patterns", "signature_phrases",
    "emotional_triggers", "coping_style", "stress_signals", "understanding_level", "decision_style",
    "attitude_to_debt", "goals", "fears", "red_lines", "trust_builders", "trust_breakers",
    "solution_preferences", "key_people",
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


# ======================================================================= step 0
ROLE_SYSTEM = "You identify speakers in UK debt-advice call transcripts. Answer with JSON only."


def resolve_roles(lines, model, usage) -> tuple[dict, str]:
    """Which speaker letter is the advisor, when the audit evidence could not tell."""
    sample = render(lines[:160])
    data = llm.chat_json(model, ROLE_SYSTEM,
                         "This is the start of a call between a debt ADVISOR and a CUSTOMER (calls may open with "
                         "a short lead hand-over by another agent). Which speaker letter is the ADVISOR?\n"
                         'Return {"advisor_speaker": "A" or "B", "reason": "one sentence"}\n\n' + sample,
                         usage, effort="low")
    adv = str(data.get("advisor_speaker") or "").strip().upper()[:1]
    speakers = {ln["speaker"] for ln in lines}
    if adv in speakers:
        return {s: (ADVISOR if s == adv else CUSTOMER) for s in speakers}, "model: " + str(data.get("reason", ""))[:160]
    return {}, "unknown"


# ======================================================================= step 1
SEGMENT_SYSTEM = """You are a senior QA analyst for a UK debt-advice company (IVA / DMP advice calls).
You read long call transcripts and mark precisely where each stage of the advisor's script happens.
Be accurate with line numbers. Answer with JSON only."""


def _segment_prompt(conv, hints, n_lines) -> str:
    hint_rows = []
    for n in range(1, 33):
        hs = hints.get(n) or []
        if hs:
            hint_rows.append(f"{n}. " + "; ".join(
                f"call {h['call_no']}: status={h['status']}" + (f", audit timestamps ~ lines {h['span'][0]}-{h['span'][1]}"
                                                               if h.get("span") else "")
                for h in hs))
    calls_txt = "\n".join(
        f"- CALL {c['call_no']} (call id {c['call_id']}): lines {c['first_line']}-{c['last_line']}, starts with {c['opening']}"
        for c in conv["calls"] if c["included"])
    return f"""Map the 32 advisor-script stages onto this debt-advice conversation.

STAGES (in script order):
{stage_list_text()}

THE CONVERSATION{" (several calls, in time order - treat them as ONE continuous conversation; a stage may be split across calls or repeated after a reconnection)" if conv["multi_call"] else ""}:
{calls_txt}

Speakers are already labelled ADVISOR / CUSTOMER. Note: calls often start with a short lead hand-over
from another agent ("I've got a lead for you...", reading out the customer's details) before the customer
is connected - those lines are on the CUSTOMER channel but are NOT the customer. Advisors also play
pre-recorded solution explanations; those count as the advisor.

AUDIT HINTS per stage (from the automated audit of each call; time ranges converted to line numbers -
useful but often too wide or partly wrong; trust the transcript content over the hints):
{chr(10).join(hint_rows) or "none"}

Return JSON:
{{
  "non_customer_spans": [{{"start": <line>, "end": <line>, "what": "e.g. lead hand-over by opener / hold / recording"}}],
  "stages": [
    {{"n": 1, "covered": true, "spans": [[<first line>, <last line>]], "note": "short note, e.g. 'brief', 'repeated after reconnection in call 2'"}},
    ... one object for EVERY stage 1-32 ...
  ]
}}
Rules: line numbers are the numbers before the first "|" (0 to {n_lines - 1}). A stage may have several spans
if it is revisited. Spans should be tight (just the lines where that stage is discussed). If a stage
did not happen anywhere in the conversation, set "covered": false and "spans": [].

TRANSCRIPT (line|time|speaker|text; C1/C2 = call number):
"""


def _audit_hints(conv, prepared) -> dict[int, list[dict]]:
    """Each included call's audit timestamps for stage n, converted to merged line numbers."""
    hints: dict[int, list[dict]] = {}
    for c in conv["calls"]:
        if not c["included"]:
            continue
        p = prepared[c["call_id"]]
        offset = c["first_line"]
        for n, a in p["audit"].items():
            idx = lines_in_seconds(p["lines"], a.get("ranges") or [])
            hints.setdefault(n, []).append({
                "call_no": c["call_no"], "status": a.get("status"),
                "span": [min(idx) + offset, max(idx) + offset] if idx else None,
            })
    return hints


def segment_conversation(conv, prepared, model, usage, log) -> dict:
    lines = conv["lines"]
    n = len(lines)
    hints = _audit_hints(conv, prepared)
    body = render_conversation(lines)
    if len(body) > MAX_SEGMENT_CHARS:
        log(f"    conversation very long ({len(body):,} chars) - trimming for stage mapping")
        body = body[:MAX_SEGMENT_CHARS]
    data = llm.chat_json(model, SEGMENT_SYSTEM, _segment_prompt(conv, hints, n) + body, usage, effort="low")

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
        stages[k] = {"covered": bool(st.get("covered")) and bool(spans), "spans": sorted(spans),
                     "note": st.get("note") or "", "source": "llm"}
    # Stages the model left out: fall back to tight audit hints.
    for k in range(1, 33):
        if k in stages:
            continue
        spans = [h["span"] for h in hints.get(k, []) if h.get("span") and h["span"][1] - h["span"][0] <= 150
                 and (h.get("status") or "").lower() not in ("omitted", "n/a")]
        stages[k] = ({"covered": True, "spans": spans, "note": "from audit timestamps", "source": "audit"}
                     if spans else {"covered": False, "spans": [], "note": "", "source": "none"})
    non_customer = []
    for sp in data.get("non_customer_spans") or []:
        try:
            non_customer.append({"start": int(sp["start"]), "end": int(sp["end"]), "what": str(sp.get("what", ""))})
        except (KeyError, TypeError, ValueError):
            pass
    return {"stages": stages, "non_customer_spans": non_customer}


# ======================================================================= step 2
STAGE_SYSTEM = """You are an expert in UK debt-advice call quality and customer behaviour.
You study one stage of a real advisor-customer conversation and describe, precisely and with evidence,
how the advisor handled it and how the CUSTOMER behaved, so a training simulator can reproduce
this exact customer. Quote the customer verbatim. Never invent facts. Answer with JSON only."""


def _audit_text(audits: list[dict]) -> str:
    if not audits:
        return "No audit result for this stage."
    out = []
    for a in audits:
        subs = "\n".join(f"  - [{s['status']}] {s['question']}" for s in a.get("sub_questions") or [])
        out.append(f"[Audit of call {a['call_id']} - {a.get('audit_state')}]\n"
                   f"Status: {a.get('status')} (score {a.get('score')}/{a.get('max_score')})\n"
                   f"Question: {(a.get('question') or '')[:600]}\n"
                   f"Sub-questions:\n{subs or '  none'}\n"
                   f"Auditor reasoning (may be about the whole call):\n{(a.get('reasoning') or '')[:1500]}")
    return "\n\n".join(out)


def _stage_prompt(stage, audits, excerpt, conv_info, notes) -> str:
    return f"""STAGE {stage['n']}: {stage['name']}
What this stage is: {stage['desc']}

CONVERSATION: {conv_info}
{notes}

AUDIT FOR THIS STAGE:
{_audit_text(audits)}

TRANSCRIPT EXCERPT FOR THIS STAGE (line|time|speaker|text; C1/C2 = call number):
{excerpt}

Return JSON:
{{
  "happened": true/false,
  "summary": "2-3 sentences: what happened in this stage (say if it was split across calls or repeated)",
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


def analyse_stage(stage, audits, lines, spans, conv_info, model, usage) -> dict:
    idx: list[int] = []
    for a, b in spans:
        idx += list(range(max(0, a - 2), min(len(lines), b + 3)))
    idx = sorted(set(idx))
    notes = ""
    if len(idx) > MAX_STAGE_LINES:
        half = MAX_STAGE_LINES // 2
        idx = idx[:half] + idx[-half:]
        notes = "(Excerpt is long; the middle part was left out.)"
    excerpt = render_conversation(lines, idx)
    return llm.chat_json(model, STAGE_SYSTEM, _stage_prompt(stage, audits, excerpt, conv_info, notes),
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


def _persona_prompt(ref, calls, stage_digest, cat_digest, crm, anonymise, deep_txt="not available") -> str:
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

IN-DEPTH CUSTOMER PROFILE (personality, communication, emotions, understanding, attitudes, vulnerability -
written by a behavioural analyst from the full conversation; the persona must embody all of this):
{deep_txt}

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
    "backstory": "3-5 sentences: who you are and how you got here, first person",
    "personality_traits": "2-4 sentences describing your personality (from the in-depth profile)",
    "communication_style": "2-4 sentences: pace, how much you say, vocabulary, register, regional markers",
    "speech_patterns": "fillers and habits, e.g. 'um', 'you know', repeating 'I, I', calling the advisor 'love'",
    "signature_phrases": "5-10 verbatim phrases you typically use, separated by ' / '",
    "emotional_triggers": "what upsets / embarrasses / reassures you and how you react",
    "coping_style": "how you cope when stressed on the call (apologising, joking, going quiet...)",
    "stress_signals": "what you sound like when you are struggling",
    "understanding_level": "how well you follow explanations, numbers and jargon; when you need repetition",
    "decision_style": "how you make decisions (defer to the advisor, need time, ask family...)",
    "attitude_to_debt": "shame, responsibility, motivation",
    "goals": "what you want out of this call and the solution",
    "fears": "what you are afraid of",
    "red_lines": "what you will not accept",
    "trust_builders": "what makes you open up and trust the advisor",
    "trust_breakers": "what makes you close down or lose trust",
    "solution_preferences": "your reactions to each debt solution, in your words",
    "key_people": "the people in your life who matter to this story, by role",
    "behavior_guide": "A comprehensive guide in clearly headed sections (see FORMAT RULES). Must reflect the real customer."
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
- Be detailed, descriptive and specific - this persona must let the simulator BE this customer. Prefer the
  customer's real words and concrete detail over generic descriptions.
- Every profile field is a plain-English STRING (2-5 sentences where there is enough detail), written the way the customer would say it
  ("about 9,600 pounds in total, the verified figure is 8,927 pounds"). NEVER an object, list or JSON -
  except "creditors" (list of strings), "additional_debts" (object of strings) and the two consent booleans.
- date_of_birth like "19 August 1992". gender is "female" or "male".
- All money as e.g. "1,250 pounds". Use the customer's own words where possible. Facts must be consistent
  across fields. Where the real call and the documents disagree, say what the customer SAID on the call
  (that is how the simulated customer answers) and mention the verified figure in brackets.
- behavior_guide: comprehensive, about 5,000-8,000 characters, in these headed sections (plain text headings
  in capitals): WHO YOU ARE / YOUR PERSONALITY / HOW YOU SPEAK / HOW YOU FEEL AND WHAT TRIGGERS YOU /
  HOW YOU UNDERSTAND AND DECIDE / MONEY, DEBT AND WHAT YOU WANT / TRUST AND THE ADVISOR / PACE OF DISCLOSURE.
  Second person or third person, concrete, with real example lines. Do NOT repeat the stage-by-stage detail
  there - that goes in stage_behaviour.
- stage_behaviour: each "how_customer_behaves" is 2-4 concrete sentences; up to 4 key_lines (verbatim where possible)."""


def stage_digest(stages_out: list[dict]) -> str:
    parts = []
    for st in stages_out:
        an = st.get("analysis")
        if not an:
            parts.append(f"## {st['n']}. {st['name']}: not covered in the conversation")
            continue
        c = an.get("customer") or {}
        audits = ", ".join(f"call {a['call_id']}: {a.get('status')}" for a in st.get("audits") or []) or "none"
        parts.append(
            f"## {st['n']}. {st['name']} ({', '.join(st.get('time') or [])}; audit: {audits})\n"
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
    for f in EXTRA_PROFILE_FIELDS:
        t = _as_text(p.get(f))
        if t:
            out[f] = t
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
def choose_quality(settings: dict, conv: dict) -> tuple[str, str]:
    q = settings.get("quality", "auto")
    if q in ("standard", "high"):
        return q, "chosen in settings"
    lines = conv["lines"]
    cust_text = " ".join(ln["text"] for ln in lines if ln["role"] != ADVISOR)
    vuln = len(set(m.group(0).lower() for m in VULN_WORDS.finditer(cust_text)))
    n_calls = sum(1 for c in conv["calls"] if c["included"])
    reasons = []
    if vuln >= 2:
        reasons.append(f"vulnerability signals ({vuln} kinds mentioned)")
    if len(lines) > 2500:
        reasons.append(f"long conversation ({len(lines)} lines)")
    if n_calls > 2:
        reasons.append(f"{n_calls} separate calls")
    if reasons:
        return "high", "auto: " + ", ".join(reasons)
    return "standard", "auto: straightforward case"


def _span_time(lines, a, b) -> str:
    la, lb = lines[a], lines[b]
    if la["call_no"] == lb["call_no"]:
        prefix = la["t"].split(" ")[0] + " " if " " in la["t"] else ""
        return f"{prefix}{clock(la['start'])}-{clock(lb['end'])}"
    return f"{la['t']} - {lb['t']}"


def _audit_state(p) -> str:
    return p["info"].get("review_status") or "draft / not submitted (scores unreliable)"


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

        m_seg = settings["model_segment"]
        # ---- step 0: speaker roles where the audit evidence could not tell
        for cid, p in prepared.items():
            if len(set(p["roles"].values())) < 2:
                log(f"  call {cid}: speaker roles not in the audit evidence - asking {m_seg}")
                p["roles"], p["roles_how"] = resolve_roles(p["lines"], m_seg, usage)
            log(f"  call {cid}: {len(p['lines'])} lines, speakers {p['roles']} ({str(p['roles_how'])[:70]})")

        # ---- merge calls into one ordered conversation
        prev_conv = store.read_json(pdir / "conversation.json", {}) or {}
        conv = build_conversation(prepared, log)
        included = [c for c in conv["calls"] if c["included"]]
        log("Conversation: " + " -> ".join(
            f"call {c['call_id']} ({c['lines']} lines, {c['opening']})" for c in included)
            + f" = {len(conv['lines'])} lines")
        store.write_json(pdir / "conversation.json", conv)
        for old in (pdir / "calls").glob("*.json") if (pdir / "calls").exists() else []:
            old.unlink()                       # per-call files from the old one-call-at-a-time format

        qual, qual_reason = choose_quality(settings, conv)
        m_stage = settings["model_stage_high"] if qual == "high" else settings["model_stage"]
        m_persona = settings["model_persona_high"] if qual == "high" else settings["model_persona"]
        log(f"Quality: {qual} ({qual_reason}). Models: segment={m_seg}, stages={m_stage}, persona={m_persona}")
        meta.update({"quality": qual, "quality_reason": qual_reason,
                     "models": {"segment": m_seg, "stage": m_stage, "persona": m_persona},
                     "conversation": [{k: c.get(k) for k in ("call_id", "call_no", "included", "case_id", "lines",
                                                             "opening", "reason", "contained_in", "containment",
                                                             "span_in_kept")} for c in conv["calls"]]})
        store.write_json(pdir / "meta.json", meta)

        old_stages = store.read_json(pdir / "stages.json", None)
        reuse = persona_only and isinstance(old_stages, list) and old_stages and "calls" not in old_stages[0]
        if persona_only and not reuse:
            log("The saved stage analysis is from the older per-call format - running the full build instead")
        errors: dict[int, str] = {}
        if reuse:
            stages_out = old_stages
            total_steps, done = 1, 0
            # Keep the stage map on the rebuilt conversation (it was saved without it just above).
            old_conv_stages = {str(st["n"]): {"covered": st.get("covered"), "spans": st.get("spans") or [],
                                              "note": st.get("segment_note") or ""} for st in stages_out}
            conv["stages"] = old_conv_stages
            conv["non_customer_spans"] = prev_conv.get("non_customer_spans") or []
            store.write_json(pdir / "conversation.json", conv)
            errors = {st["n"]: st["error"] for st in stages_out if st.get("error")}
            log("Re-using the saved stage analysis - rebuilding the customer profile only")
        else:
            lines = conv["lines"]
            total_steps, done = 2, 0
            progress(done, total_steps, "Mapping the 32 stages")

            # ---- step 1: segmentation over the whole conversation
            log(f"[1/4] Mapping 32 stages onto the conversation ({len(lines)} lines) ...")
            seg = segment_conversation(conv, prepared, m_seg, usage, log)
            covered = sum(1 for s in seg["stages"].values() if s["covered"])
            log(f"    {covered}/32 stages found")
            conv["stages"] = {str(k): v for k, v in sorted(seg["stages"].items())}
            conv["non_customer_spans"] = seg["non_customer_spans"]
            store.write_json(pdir / "conversation.json", conv)
            done += 1
            progress(done, total_steps, "Stages mapped")

            # ---- step 2: one analysis per stage, across all calls
            conv_info = ("; ".join(
                f"call {c['call_no']} = call id {c['call_id']} on {str(prepared[c['call_id']]['info'].get('call_date'))[:10]}"
                f" ({c['opening']}, advisor {prepared[c['call_id']]['info'].get('advisor')})" for c in included)
                + f"; chosen solution {included[-1] and prepared[included[-1]['call_id']]['info'].get('chosen_solution')}")
            audits_by_stage: dict[int, list[dict]] = {}
            for c in conv["calls"]:
                p = prepared[c["call_id"]]
                for n, a in p["audit"].items():
                    audits_by_stage.setdefault(n, []).append({
                        "call_id": c["call_id"], "included": c["included"], "audit_state": _audit_state(p),
                        "status": a.get("status"), "score": a.get("score"), "max_score": a.get("max_score"),
                        "question": a.get("question"), "reasoning": a.get("reasoning"),
                        "sub_questions": a.get("sub_questions")})
            tasks = [(st, seg["stages"][st["n"]]["spans"]) for st in STAGES
                     if seg["stages"].get(st["n"], {}).get("covered")]
            total_steps += len(tasks)
            log(f"[2/4] Analysing {len(tasks)} stages with {m_stage} ...")
            progress(done, total_steps, "Analysing stages")
            results: dict[int, dict] = {}
            workers = max(1, int(settings.get("parallel_stage_calls", 4)))

            def stage_audits(n):
                aud = audits_by_stage.get(n, [])
                inc = [a for a in aud if a["included"]]
                return inc or aud

            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(analyse_stage, st, stage_audits(st["n"]), lines, spans, conv_info,
                                  m_stage, usage): st["n"] for st, spans in tasks}
                for f in as_completed(futs):
                    n = futs[f]
                    try:
                        results[n] = f.result()
                    except Exception as exc:  # noqa: BLE001
                        errors[n] = str(exc)[:300]
                        log(f"    stage {n} failed: {errors[n]}")
                    done += 1
                    progress(done, total_steps, f"Stage {n} - {STAGE_BY_N[n]['name']}")

            stages_out = []
            for st in STAGES:
                s = seg["stages"].get(st["n"]) or {}
                spans = s.get("spans") or []
                stages_out.append({
                    "n": st["n"], "key": st["key"], "name": st["name"], "group": st["group"],
                    "covered": bool(s.get("covered")), "spans": spans,
                    "time": [_span_time(lines, a, b) for a, b in spans],
                    "call_nos": sorted({lines[i]["call_no"] for a, b in spans for i in (a, b)}),
                    "segment_note": s.get("note"),
                    "audits": [{k: v for k, v in a.items()} for a in audits_by_stage.get(st["n"], [])],
                    "analysis": results.get(st["n"]),
                    "error": errors.get(st["n"]),
                })
            store.write_json(pdir / "stages.json", stages_out)

        # ---- step 3: in-depth customer profile from the whole conversation
        total_steps += 1
        cat_digest = condense_cat(cat, anonymise)
        digest = stage_digest(stages_out)
        log(f"[3/4] Writing the in-depth customer profile (personality, communication, emotions, "
            f"understanding, attitudes, vulnerability) with {m_persona} ...")
        progress(done, total_steps, "In-depth customer profile")
        deep = build_deep_profile(ref, conv, digest, cat_digest, anonymise, m_persona, usage)
        store.write_json(pdir / "deep_profile.json", deep)
        done += 1
        progress(done, total_steps, "In-depth profile written")

        # ---- step 4: persona for the AI Call Agent
        log(f"[4/4] Building the AI Call Agent persona with {m_persona} ...")
        calls_brief = []
        for c in conv["calls"]:
            p = prepared[c["call_id"]]
            calls_brief.append({
                "call_id": c["call_id"], "order_in_conversation": c.get("call_no"),
                "included": c["included"], "note": c.get("opening") or c.get("reason"),
                "date": str(p["info"].get("call_date"))[:10], "advisor": p["info"].get("advisor"),
                "chosen_solution": p["info"].get("chosen_solution"), "audit": p["info"].get("totals"),
                "audit_state": _audit_state(p), "transcript_lines": len(p["lines"])})
        crm = [c for cid, p in prepared.items() for c in p["crm"]
               if any(x["call_id"] == cid and x["included"] for x in conv["calls"])]
        prompt = _persona_prompt(ref, calls_brief, digest, cat_digest, crm, anonymise, compact_for_persona(deep))
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
        meta_now = {**meta, "updated_at": _now()}
        (pdir / "customer_profile.md").write_text(report_markdown(ref, deep, persona_row, meta_now), encoding="utf-8")
        done += 1
        progress(done, total_steps, "Profile ready")

        meta.update({"status": "ready", "updated_at": _now(), "created_at": meta.get("created_at") or _now(),
                     "usage": usage.by_model, "calls": [c["call_id"] for c in included], "stage_errors": len(errors),
                     "persona_id": persona_row["id"]})
        store.write_json(pdir / "meta.json", meta)
        log(f"Done: profile '{persona_row['label']}' ({persona_row['id']}). Tokens: {usage.by_model}")
        return meta
    except Exception as exc:
        meta.update({"status": "failed", "error": str(exc)[:1000], "updated_at": _now(), "usage": usage.by_model})
        store.write_json(pdir / "meta.json", meta)
        raise
