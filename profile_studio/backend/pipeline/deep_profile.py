"""The in-depth customer profile: who the customer is, how they think, feel and talk.

Built from the WHOLE conversation (every line, both speakers), the stage-by-stage
analysis and the CAT case data. Every claim is meant to be backed by evidence
(verbatim quotes). The agent persona is then built on top of this profile.
"""

from __future__ import annotations

import json

from . import llm
from .conversation import render_conversation

MAX_CONVERSATION_CHARS = 420_000

DEEP_SYSTEM = """You are a senior behavioural analyst and customer psychologist working with a UK debt-advice
company. From real call recordings you write deep, accurate, evidence-based profiles of customers: who they
are, their personality, how they communicate, what they feel and why, how they understand and decide, and how
an advisor should work with them. You are specific and descriptive, never generic. Every judgement is backed
by what the customer actually said (verbatim quotes) or did on the call. You never invent facts; where
something is uncertain you say so. Answer with JSON only."""

SCHEMA = """{
  "headline": "one sentence: who this customer is and what situation they are in",
  "narrative": "4-7 rich paragraphs telling the customer's story as it emerged on the call: background, family, work, health, how the debt built up, what life is like now, what they want. Descriptive, specific, human.",
  "life_context": {
    "household": "who lives with them, dependants, partner/ex-partner, support network",
    "work_and_income": "job / unemployment / benefits history, how stable their income is",
    "health": "physical and mental health as disclosed, treatment, effect on daily life",
    "housing": "tenure, how long, how secure, attachment to the home",
    "key_life_events": [{"when": "approximate time", "event": "...", "impact": "on money / wellbeing"}]
  },
  "debt_story": {
    "how_it_started": "...",
    "timeline": [{"when": "...", "what": "..."}],
    "what_they_have_tried": "...",
    "current_pressure": "collection activity, arrears, letters/calls, enforcement",
    "biggest_worry": "in their own words where possible"
  },
  "personality": {
    "summary": "2-3 paragraphs describing their personality as it came across",
    "traits": [
      {"trait": "Openness", "level": "low | moderate | high", "description": "how it shows", "evidence": ["verbatim quote or observed behaviour"]},
      {"trait": "Conscientiousness", ...}, {"trait": "Extraversion", ...}, {"trait": "Agreeableness", ...},
      {"trait": "Emotional stability", ...}
    ],
    "other_traits": [{"trait": "e.g. self-critical, people-pleasing, proud, protective, humorous, guarded, impatient", "description": "...", "evidence": ["..."]}]
  },
  "communication_style": {
    "summary": "paragraph",
    "pace": "...",
    "verbosity": "how much they say, when they say more or less",
    "register_and_vocabulary": "formal/informal, vocabulary level, grammar",
    "regional_or_cultural_markers": "dialect words, terms of address ('love', 'mate'), cultural references",
    "fillers_and_habits": ["um", "you know", "..."],
    "how_they_answer_questions": "...",
    "how_they_ask_questions": "...",
    "listening_and_interruptions": "...",
    "humour": "...",
    "signature_phrases": ["verbatim phrases they use repeatedly or that are very typical of them"]
  },
  "emotional_profile": {
    "baseline_mood": "...",
    "emotional_range": "...",
    "triggers": [{"trigger": "topic or advisor behaviour", "reaction": "what happens", "evidence": "verbatim quote"}],
    "coping_strategies": ["humour, minimising, apologising, deflecting, ..."],
    "stress_signals": ["what it sounds like when they are struggling"],
    "emotional_arc": [{"point": "stage name or time in the call", "emotion": "...", "what_happened": "..."}]
  },
  "cognition_and_understanding": {
    "comprehension": "how well they follow explanations",
    "financial_literacy": "...",
    "numeracy_and_memory_for_figures": "...",
    "need_for_repetition": "...",
    "decision_making_style": "how they decide; do they defer to the advisor, need time, need family input",
    "evidence": ["..."]
  },
  "money_attitudes": {
    "attitude_to_debt": "...",
    "shame_or_guilt": "...",
    "sense_of_responsibility": "...",
    "spending_and_budgeting": "...",
    "motivation": "why they called / what drives them",
    "goals": ["..."],
    "fears": ["..."],
    "red_lines": ["things they will not accept, e.g. losing the home"]
  },
  "vulnerability": {
    "summary": "paragraph",
    "fca_drivers": [{"driver": "Health | Life events | Resilience | Capability", "detail": "...", "severity": "low | medium | high", "evidence": "..."}],
    "support_needs": ["adjustments an advisor should make"],
    "disclosure_behaviour": "how and when they disclosed sensitive information"
  },
  "trust_and_rapport": {
    "initial_stance": "...",
    "what_builds_trust": ["..."],
    "what_damages_trust": ["..."],
    "attitude_to_advisor": "...",
    "attitude_to_company_and_process": "..."
  },
  "engagement_over_call": {"summary": "...", "fatigue_or_drop_off": "...", "attention_and_distractions": "..."},
  "solution_attitudes": [{"solution": "e.g. IVA / DMP / DRO / Bankruptcy / Breathing Space / Lending / Self-management", "reaction": "...", "reasoning_in_their_words": "...", "quote": "verbatim"}],
  "relationships": [{"person": "by role only, e.g. ex-partner, mother, daughter", "role_in_story": "...", "how_they_talk_about_them": "..."}],
  "inconsistencies": [{"topic": "...", "detail": "what they said differently / were unsure about"}],
  "quote_library": [{"theme": "e.g. debt, family, health, anxiety, money, solutions, humour, the advisor", "quote": "verbatim customer line"}],
  "advisor_guidance": {
    "how_to_approach": "paragraph",
    "dos": ["..."],
    "donts": ["..."],
    "likely_challenges": ["..."],
    "difficulty": "easy | medium | hard",
    "difficulty_reason": "..."
  },
  "confidence_notes": "what is uncertain or based on little evidence"
}"""


def _prompt(ref: str, conv: dict, stage_digest: str, cat_digest: dict, anonymise: bool) -> str:
    body = render_conversation(conv["lines"])
    note = ""
    if len(body) > MAX_CONVERSATION_CHARS:
        half = MAX_CONVERSATION_CHARS // 2
        body = body[:half] + "\n[... middle of the conversation left out for length ...]\n" + body[-half:]
        note = "(The transcript was long; part of the middle is left out - use the stage evidence for it.)"
    privacy = ("Refer to the customer as 'the customer' (never by name) and to other people by their role "
               "(her ex-partner, his daughter). Do not include dates of birth, addresses, phone numbers, emails, "
               "account numbers or case numbers." if anonymise else
               "You may use the customer's first name. Do not include account numbers.")
    return f"""Write the in-depth profile of the CUSTOMER in case {ref}.

{privacy}

Rules:
- Be comprehensive and descriptive: this profile is used to understand the customer deeply and to make a
  simulator behave exactly like them. Prefer concrete detail over generalities.
- Back judgements with evidence: verbatim customer quotes (copy them exactly from the transcript) or
  described behaviour. quote_library: 15-30 quotes covering different themes.
- Lines labelled ADVISOR are the advisor; lines in a lead hand-over at the start are another agent, not
  the customer.
- Where the call and the case documents disagree, describe what the customer said and note the difference.
- Do not invent anything. If there is little evidence for something, say so briefly.

STAGE-BY-STAGE ANALYSIS OF THE CALL(S):
{stage_digest}

CASE DATA FROM THE CAT TOOL (verified from documents):
{json.dumps(cat_digest, ensure_ascii=False)[:40000] if cat_digest else "not available"}

Return JSON in exactly this shape (fill every field; lists can be long):
{SCHEMA}

FULL CONVERSATION {note}(line|time|speaker|text; C1/C2 = call number):
{body}
"""


def build_deep_profile(ref: str, conv: dict, stage_digest: str, cat_digest: dict, anonymise: bool,
                       model: str, usage) -> dict:
    return llm.chat_json(model, DEEP_SYSTEM, _prompt(ref, conv, stage_digest, cat_digest, anonymise),
                         usage, effort="medium")


def compact_for_persona(deep: dict) -> str:
    """The deep profile, trimmed of bulky evidence lists, for the persona prompt."""
    if not deep:
        return "not available"
    keep = dict(deep)
    keep.pop("narrative", None)
    return json.dumps(keep, ensure_ascii=False)[:45000]


# ------------------------------------------------------------------ report
def _li(items) -> str:
    out = []
    for x in items or []:
        if isinstance(x, dict):
            out.append("- " + " — ".join(str(v) for v in x.values() if v not in (None, "", [])))
        elif x not in (None, ""):
            out.append(f"- {x}")
    return "\n".join(out) or "- —"


def _kv(d: dict, labels: list[tuple[str, str]]) -> str:
    return "\n".join(f"**{label}:** {d.get(k)}" for k, label in labels if d.get(k)) or "—"


def report_markdown(ref: str, deep: dict, persona: dict | None, meta: dict | None) -> str:
    d = deep or {}
    p = (persona or {}).get("profile") or {}
    pers = d.get("personality") or {}
    com = d.get("communication_style") or {}
    emo = d.get("emotional_profile") or {}
    cog = d.get("cognition_and_understanding") or {}
    mon = d.get("money_attitudes") or {}
    vul = d.get("vulnerability") or {}
    tru = d.get("trust_and_rapport") or {}
    eng = d.get("engagement_over_call") or {}
    adv = d.get("advisor_guidance") or {}
    life = d.get("life_context") or {}
    debt = d.get("debt_story") or {}
    lines = [
        f"# Customer profile – case {ref}",
        f"_{d.get('headline', '')}_",
        "",
        f"Persona for the AI Call Agent: **{(persona or {}).get('label', '—')}** · {(persona or {}).get('segment', '')}",
        f"Built {(meta or {}).get('updated_at', '')} · difficulty **{adv.get('difficulty', '—')}**",
        "",
        "## Their story", d.get("narrative", "—"),
        "",
        "## Life context",
        _kv(life, [("household", "Household"), ("work_and_income", "Work & income"), ("health", "Health"),
                   ("housing", "Housing")]),
        "", "**Key life events**", _li(life.get("key_life_events")),
        "", "## Debt story",
        _kv(debt, [("how_it_started", "How it started"), ("what_they_have_tried", "What they've tried"),
                   ("current_pressure", "Current pressure"), ("biggest_worry", "Biggest worry")]),
        "", "**Timeline**", _li(debt.get("timeline")),
        "", "## Personality", pers.get("summary", "—"), "",
        "| Trait | Level | How it shows |", "|---|---|---|",
        *[f"| {t.get('trait')} | {t.get('level')} | {t.get('description')} |" for t in pers.get("traits") or []],
        "", "**Other traits**", _li([f"{t.get('trait')}: {t.get('description')}" for t in pers.get("other_traits") or []]),
        "", "## Communication style", com.get("summary", "—"), "",
        _kv(com, [("pace", "Pace"), ("verbosity", "Verbosity"), ("register_and_vocabulary", "Register & vocabulary"),
                  ("regional_or_cultural_markers", "Regional / cultural markers"),
                  ("how_they_answer_questions", "Answering questions"), ("how_they_ask_questions", "Asking questions"),
                  ("listening_and_interruptions", "Listening & interruptions"), ("humour", "Humour")]),
        "", "**Fillers & habits:** " + ", ".join(com.get("fillers_and_habits") or []),
        "", "**Signature phrases**", _li([f"“{q}”" for q in com.get("signature_phrases") or []]),
        "", "## Emotional profile",
        _kv(emo, [("baseline_mood", "Baseline mood"), ("emotional_range", "Emotional range")]),
        "", "**Triggers**", _li([f"{t.get('trigger')} → {t.get('reaction')} (“{t.get('evidence')}”)" for t in emo.get("triggers") or []]),
        "", "**Coping strategies**", _li(emo.get("coping_strategies")),
        "", "**Stress signals**", _li(emo.get("stress_signals")),
        "", "**Emotional arc through the call**", _li([f"{a.get('point')}: {a.get('emotion')} — {a.get('what_happened')}" for a in emo.get("emotional_arc") or []]),
        "", "## Understanding & decision-making",
        _kv(cog, [("comprehension", "Comprehension"), ("financial_literacy", "Financial literacy"),
                  ("numeracy_and_memory_for_figures", "Numbers & memory"), ("need_for_repetition", "Need for repetition"),
                  ("decision_making_style", "Decision-making")]),
        "", "## Attitudes to money & debt",
        _kv(mon, [("attitude_to_debt", "Attitude to debt"), ("shame_or_guilt", "Shame / guilt"),
                  ("sense_of_responsibility", "Responsibility"), ("spending_and_budgeting", "Spending & budgeting"),
                  ("motivation", "Motivation")]),
        "", "**Goals**", _li(mon.get("goals")), "", "**Fears**", _li(mon.get("fears")),
        "", "**Red lines**", _li(mon.get("red_lines")),
        "", "## Vulnerability", vul.get("summary", "—"), "",
        "| Driver | Severity | Detail |", "|---|---|---|",
        *[f"| {v.get('driver')} | {v.get('severity')} | {v.get('detail')} |" for v in vul.get("fca_drivers") or []],
        "", "**Support needs**", _li(vul.get("support_needs")),
        "", f"**How they disclosed:** {vul.get('disclosure_behaviour', '—')}",
        "", "## Trust & rapport",
        _kv(tru, [("initial_stance", "Initial stance"), ("attitude_to_advisor", "Attitude to the advisor"),
                  ("attitude_to_company_and_process", "Attitude to the company / process")]),
        "", "**What builds trust**", _li(tru.get("what_builds_trust")),
        "", "**What damages trust**", _li(tru.get("what_damages_trust")),
        "", "## Engagement over the call",
        _kv(eng, [("summary", "Summary"), ("fatigue_or_drop_off", "Fatigue / drop-off"),
                  ("attention_and_distractions", "Attention")]),
        "", "## Reactions to solutions", "| Solution | Reaction | In their words |", "|---|---|---|",
        *[f"| {s.get('solution')} | {s.get('reaction')} | {s.get('quote') or s.get('reasoning_in_their_words')} |" for s in d.get("solution_attitudes") or []],
        "", "## People in their life", _li([f"{r.get('person')}: {r.get('role_in_story')} — {r.get('how_they_talk_about_them')}" for r in d.get("relationships") or []]),
        "", "## Inconsistencies", _li([f"{i.get('topic')}: {i.get('detail')}" for i in d.get("inconsistencies") or []]),
        "", "## In their own words", _li([f"[{q.get('theme')}] “{q.get('quote')}”" for q in d.get("quote_library") or []]),
        "", "## Guidance for advisors", adv.get("how_to_approach", "—"),
        "", "**Do**", _li(adv.get("dos")), "", "**Don't**", _li(adv.get("donts")),
        "", "**Likely challenges**", _li(adv.get("likely_challenges")),
        "", f"**Difficulty:** {adv.get('difficulty', '—')} — {adv.get('difficulty_reason', '')}",
        "", "## Key facts (persona)",
        *[f"- **{k.replace('_', ' ').capitalize()}:** {p.get(k)}" for k in
          ("total_debt", "monthly_contractual_payments", "affordable_payment", "income", "expenses", "benefits",
           "employment", "housing", "household", "dependents", "assets", "vulnerability") if p.get(k)],
        "", "**Creditors**", _li(p.get("creditors")),
        "", f"_Confidence notes: {d.get('confidence_notes', '—')}_",
    ]
    return "\n".join(str(x) for x in lines) + "\n"
