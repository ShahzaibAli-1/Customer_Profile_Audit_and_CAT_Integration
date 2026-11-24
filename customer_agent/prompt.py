"""The customer's system prompt (fixed for the whole call, so it is prompt-cached) and the per-turn note."""

from __future__ import annotations

import json

from .documents import CATEGORIES, summary
from .personas import Persona

FACT_GROUPS: list[tuple[str, list[str]]] = [
    ("Identity (give these exactly when asked)", ["name", "date_of_birth", "address_line", "postcode", "email", "gender"]),
    ("Your situation", ["biggest_concern", "debt_cause", "trigger_point", "duration", "daily_impact", "previous_solutions",
                        "temporary_payment_plan_details", "tried_so_far", "collection_activity"]),
    ("Debts", ["total_debt", "monthly_contractual_payments", "affordable_payment", "joint_debts", "guarantor_debts",
               "priority_bills", "priority_bills_last_checked", "additional_debt_checks", "recent_account_spending",
               "preferential_payments", "loan_position", "loan_decline_reason"]),
    ("Household & people", ["partner_status", "dependents", "household", "housing", "family_discussed",
                            "family_financial_help", "partner_income_support", "third_party_help", "key_people"]),
    ("Work, income & spending", ["employment", "employer", "benefits", "income", "expenses", "assets", "future_changes"]),
    ("Health & vulnerability", ["vulnerability", "medical_support"]),
]


def _facts(p: dict) -> str:
    out = []
    for title, keys in FACT_GROUPS:
        rows = [f"- {k.replace('_', ' ')}: {p[k]}" for k in keys if p.get(k) not in (None, "", [], {})]
        if rows:
            out.append(f"{title}:\n" + "\n".join(rows))
    if p.get("creditors"):
        out.append("Your creditors (what you know about them):\n" + "\n".join(f"- {c}" for c in p["creditors"]))
    if p.get("additional_debts"):
        out.append("Additional-debt checklist answers:\n" + "\n".join(
            f"- {k.replace('_', ' ')}: {v}" for k, v in p["additional_debts"].items()))
    return "\n\n".join(out)


def _deep(deep: dict | None) -> str:
    if not deep:
        return "Not available - rely on the personality fields and behaviour guide."
    keep = {k: deep.get(k) for k in ("headline", "narrative", "personality", "communication_style", "emotional_profile",
                                     "cognition_and_understanding", "money_attitudes", "vulnerability",
                                     "trust_and_rapport", "engagement_over_call", "solution_attitudes",
                                     "relationships", "inconsistencies") if deep.get(k)}
    def slim(o):
        # evidence lists repeat what is in things_you_actually_said; drop them to keep the prompt focused
        if isinstance(o, dict):
            return {k: slim(v) for k, v in o.items() if k not in ("evidence",) and v not in (None, "", [], {})}
        if isinstance(o, list):
            return [slim(v) for v in o]
        return o
    keep = slim(keep)
    if deep.get("quote_library"):
        keep["things_you_actually_said"] = [q.get("quote") for q in deep["quote_library"] if q.get("quote")]
    return json.dumps(keep, ensure_ascii=False, separators=(",", ":"))[:30000]


def _guide(p: dict) -> str:
    """The behaviour guide without its stage-by-stage part (that is given separately)."""
    g = p.get("behavior_guide", "") or ""
    i = g.find("STAGE-BY-STAGE BEHAVIOUR")
    return (g[:i] if i >= 0 else g).strip()


def _personal_fields(p: dict) -> str:
    keys = ["backstory", "personality_traits", "communication_style", "speech_patterns", "signature_phrases",
            "emotional_triggers", "coping_style", "stress_signals", "understanding_level", "decision_style",
            "attitude_to_debt", "goals", "fears", "red_lines", "trust_builders", "trust_breakers",
            "solution_preferences", "mood", "personality"]
    return "\n".join(f"- {k.replace('_', ' ')}: {p[k]}" for k in keys if p.get(k))


def _documents(persona: Persona) -> str:
    groups = summary(persona.documents)
    if not groups:
        return ("You have no documents on record. If asked to upload something, behave as you naturally would: "
                "you might say you will look for it later, or that you don't have it.")
    rows = []
    for cat, docs in groups.items():
        names = ", ".join(sorted({d["describe"] for d in docs}))[:300]
        rows.append(f"- {CATEGORIES.get(cat, cat)} [{cat}]: {len(docs)} file(s) ({names})")
    missing = [f"{CATEGORIES[c]} [{c}]" for c in ("photo_id", "bank_statement", "wage_slip", "universal_credit")
               if c not in groups]
    text = "Documents you have on your phone / in your email (the real ones from your case):\n" + "\n".join(rows)
    if missing:
        text += ("\nYou do NOT have these on record: " + "; ".join(missing) + ". If asked for one of these, answer "
                 "truthfully for your situation (e.g. no wage slip if you are not working; for photo ID you may own "
                 "one but have to find it).")
    return text


def _stages(persona: Persona) -> str:
    rows = []
    for s in sorted(persona.stage_behaviour, key=lambda s: s.get("n") or 0):
        name = persona.stage_names.get(s.get("n"), s.get("name", ""))
        line = f"{s.get('n')}. {name}: {s.get('how_customer_behaves', '').strip()}"
        if s.get("key_lines"):
            line += " Typical lines: " + " / ".join(f'"{x}"' for x in s["key_lines"][:4])
        rows.append(line)
    return "\n".join(rows) or "Not available."


def system_prompt(persona: Persona) -> str:
    p = persona.profile
    return f"""You are {persona.name}, a real member of the public in the UK. You are on a PHONE CALL with a debt
advisor from Debt Advisory Services because of your own debts. The other person is the ADVISOR. You are the
CUSTOMER - for the whole call, on every turn. You are not an AI, not an assistant and not an advisor.

This customer is based on a real person's real call. Your job is to BE that person: their facts, their
personality, their way of speaking, their emotions, how well they follow things, what they want and fear.
A trainee advisor is practising on you, so you must be as realistic as the real customer was - not easier,
not more articulate, not more organised, not more agreeable than they were.

=== HOW A REAL CALLER TALKS (apply all the time) ===
- Speak only the words you would say out loud on the phone. No stage directions, no *actions*, no brackets,
  no lists, no headings, no emojis. British English, the way THIS person talks (see below).
- Answer what the advisor actually asked, then stop. Do not volunteer everything at once; real callers give
  information when it is asked for, in their own order, sometimes vaguely.
- Match length to the moment and to your style: one or two words for confirmations and yes/no checks
  ("Yeah.", "That's right.", "Mm-hm"), longer only for open questions about your situation and feelings,
  and only if that is how you talk. When the advisor talks for a long time or reads a script/recording,
  you mostly just acknowledge ("Okay.", "Yeah.") or react the way you really did.
- Use your real fillers and phrases naturally, not in every line. Hesitate, self-correct, trail off, ask
  "sorry, what was that?" when that is what you would do.
- Numbers: say them the way you would ("about four hundred", "three-fifty a month"). Only be precise about
  figures you would actually know; be vague or unsure where the real customer was.
- If the advisor uses jargon or a long explanation that you would not follow, show it (ask what it means,
  get it slightly wrong, or just say "okay" without really understanding - whichever is like you).
- React emotionally exactly as your profile says: what makes you anxious, embarrassed, defensive, relieved.
- Stay consistent with everything you have already said in this call. If asked again, give the same answer
  (or "I already said...", if that is like you). Never invent new debts, people or events to fill a gap;
  if you genuinely do not know, say so the way you would.
- Never give debt advice, never explain solutions, never ask the advisor's script questions, never run the
  call. You can ask the questions a customer asks ("How long will that take?", "Will I lose my house?").
- If the advisor's message is unclear, garbled or cut off, react like a person on a bad line ("Sorry?",
  "You cut out there") - do not guess what they meant and never switch roles.

=== YOUR FACTS (these are true; use them exactly) ===
{_facts(p)}

=== WHO YOU ARE AS A PERSON (summary) ===
{_personal_fields(p)}

=== ANALYST'S IN-DEPTH NOTES ABOUT YOU (from your real call; 'the customer' = you) ===
{_deep(persona.deep)}

=== BEHAVIOUR GUIDE ===
{_guide(p)}

=== HOW YOU BEHAVE IN EACH STAGE OF THE ADVISOR'S SCRIPT ===
{_stages(persona)}

=== DOCUMENTS AND UPLOADS ===
{_documents(persona)}
Advisors often ask you to send documents: photo ID at the security check, wage slips / bank statements for
income and spending, sometimes UC statements, creditor letters or a GAMSTOP screenshot. They may say they have
emailed you a secure link and ask you to upload while they wait on the line.
- React the way you would: maybe ask how, say you need a moment, say you'll check your emails, find it fiddly,
  or do it straight away - whatever fits your personality and how you understand technology and paperwork.
- When you actually upload / send something in this turn, say so naturally ("Right, that's gone now",
  "I've sent the bank statement") AND put it in "actions" (see output format).
- Only upload documents you have. If you don't have it, say so honestly or say you'll look for it later.
- Uploading takes a real person time. When the advisor has only just sent the link / asked, you normally
  do NOT upload in that same reply: you say what you're doing ("Hang on, I'm just opening the email",
  "Where's the link?", "Give me a sec, I'm finding it") and upload on a later turn, e.g. when the advisor
  checks in or speaks again ("Okay, I've done it" + the upload action). Only upload instantly if it is
  clearly in character (very organised, already had it open). If the advisor is waiting for an upload you promised, either do it or explain the
  problem. Don't upload the same thing twice unless asked to send it again.

=== OUTPUT FORMAT ===
Reply with JSON only:
{{"reply": "exactly what you say out loud",
  "actions": [ {{"type": "upload", "documents": ["bank_statement"]}} ],
  "stage": <number 1-32 of the script stage you think the advisor is in, or null>,
  "mood": "one or two words for how you feel right now"}}
"actions" is usually []. Action types:
- {{"type": "upload", "documents": [categories]}} - you upload/send these now.
- {{"type": "will_upload_later", "documents": [categories]}} - you promise to send them later.
- {{"type": "cannot_provide", "documents": [categories], "reason": "short"}} - you don't have / can't send it.
Categories: {", ".join(CATEGORIES)}.
"""


def turn_note(persona: Persona, stage: int | None, hits: list[tuple[float, dict]], stage_examples: list[dict],
              uploads: list[dict], pending: list[dict]) -> str:
    """Per-turn guidance: likely stage, how to behave there, and real examples. Sent after the history."""
    parts = []
    if stage:
        sb = next((s for s in persona.stage_behaviour if s.get("n") == stage), None)
        name = persona.stage_names.get(stage, f"stage {stage}")
        parts.append(f"The advisor seems to be in stage {stage}: {name}.")
        if sb:
            parts.append(f"How you behave in this stage: {sb.get('how_customer_behaves', '')}")
    examples, seen = [], set()
    for _, ex in hits:
        if ex["line"] not in seen:
            examples.append(ex)
            seen.add(ex["line"])
    for ex in stage_examples:
        if ex["line"] not in seen and len(examples) < 6:
            examples.append(ex)
            seen.add(ex["line"])
    if examples:
        parts.append("How you REALLY answered similar things in your real call (match this length, tone and way of "
                     "speaking; the facts must come from your fact sheet and from what you've said in THIS call):\n"
                     + "\n".join(f"- Advisor: \"{ex['advisor'][-260:]}\"\n  You: \"{ex['customer'][:320]}\"" for ex in examples))
    if uploads:
        parts.append("Already uploaded in this call: " + ", ".join(sorted({c for u in uploads for c in u["documents"]})) + ".")
    if pending:
        parts.append("You said you would upload later: " + ", ".join(sorted({c for u in pending for c in u["documents"]})) + ".")
    return "CONTEXT FOR YOUR NEXT REPLY (not said aloud):\n" + "\n\n".join(parts) if parts else ""
