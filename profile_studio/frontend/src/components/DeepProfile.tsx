import { Brain, HeartPulse, MessageCircle, Quote, ShieldAlert, Sparkles, Target, Users } from "lucide-react";
import type { ReactNode } from "react";
import { Json } from "../api";
import { Badge, Empty, KV, List } from "./ui";

function Card({ title, icon, children, className = "" }: { title: string; icon?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={`card p-5 ${className}`}>
      <div className="flex items-center gap-2 font-semibold mb-3">{icon}{title}</div>
      {children}
    </div>
  );
}

function Para({ text }: { text?: string }) {
  if (!text) return <div className="text-sm text-slate-400">—</div>;
  return (
    <div className="space-y-2">
      {String(text).split(/\n\s*\n|\n/).filter(Boolean).map((p, i) => (
        <p key={i} className="text-sm text-slate-700 leading-relaxed">{p}</p>
      ))}
    </div>
  );
}

const LEVEL: Record<string, number> = { low: 1, moderate: 2, medium: 2, high: 3 };

function LevelBar({ level }: { level?: string }) {
  const n = LEVEL[(level || "").toLowerCase()] || 0;
  return (
    <div className="flex gap-1" title={level}>
      {[1, 2, 3].map((i) => <span key={i} className={`h-1.5 w-6 rounded-full ${i <= n ? "bg-indigo-500" : "bg-slate-200"}`} />)}
    </div>
  );
}

function Evidence({ items }: { items?: unknown[] }) {
  const xs = (items || []).filter(Boolean);
  if (!xs.length) return null;
  return (
    <div className="mt-1.5 space-y-1">
      {xs.map((e, i) => <div key={i} className="text-xs italic text-slate-500 border-l-2 border-slate-200 pl-2">“{String(e)}”</div>)}
    </div>
  );
}

function sevTone(s?: string) {
  return s === "high" ? "red" : s === "medium" ? "amber" : "green";
}

export default function DeepProfile({ deep }: { deep: Json }) {
  if (!deep) {
    return (
      <div className="card">
        <Empty icon={<Sparkles className="h-10 w-10" />} title="No in-depth profile yet">
          Use “Rebuild profile only” (about 4 minutes) to add it to this case – the stage analysis is reused.
        </Empty>
      </div>
    );
  }
  const life = deep.life_context || {};
  const debt = deep.debt_story || {};
  const pers = deep.personality || {};
  const com = deep.communication_style || {};
  const emo = deep.emotional_profile || {};
  const cog = deep.cognition_and_understanding || {};
  const mon = deep.money_attitudes || {};
  const vul = deep.vulnerability || {};
  const tru = deep.trust_and_rapport || {};
  const eng = deep.engagement_over_call || {};
  const adv = deep.advisor_guidance || {};

  return (
    <div className="space-y-5">
      <div className="card p-5 bg-gradient-to-br from-indigo-50 to-white border-indigo-100">
        <div className="text-xs font-semibold uppercase tracking-wide text-indigo-600 mb-1">Who this customer is</div>
        <div className="text-lg font-medium text-slate-900">{deep.headline}</div>
        {adv.difficulty && (
          <div className="mt-2 flex items-center gap-2 text-sm">
            <Badge tone={adv.difficulty === "hard" ? "red" : adv.difficulty === "medium" ? "amber" : "green"}>{adv.difficulty} to advise</Badge>
            <span className="text-slate-600">{adv.difficulty_reason}</span>
          </div>
        )}
      </div>

      <Card title="Their story" icon={<Users className="h-4 w-4 text-indigo-500" />}><Para text={deep.narrative} /></Card>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Life context">
          <div className="space-y-2.5">
            <KV k="Household" v={life.household} />
            <KV k="Work & income" v={life.work_and_income} />
            <KV k="Health" v={life.health} />
            <KV k="Housing" v={life.housing} />
          </div>
          {!!life.key_life_events?.length && (
            <div className="mt-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">Key life events</div>
              <ol className="relative border-l border-slate-200 ml-1 space-y-2">
                {life.key_life_events.map((e: Json, i: number) => (
                  <li key={i} className="ml-3 text-sm">
                    <span className="absolute -left-1 mt-1.5 h-2 w-2 rounded-full bg-indigo-400" />
                    <span className="font-medium">{e.when}</span> – {e.event}
                    {e.impact && <span className="text-slate-500"> ({e.impact})</span>}
                  </li>
                ))}
              </ol>
            </div>
          )}
        </Card>
        <Card title="Debt story">
          <div className="space-y-2.5">
            <KV k="How it started" v={debt.how_it_started} />
            <KV k="What they've tried" v={debt.what_they_have_tried} />
            <KV k="Current pressure" v={debt.current_pressure} />
            <KV k="Biggest worry" v={debt.biggest_worry} />
          </div>
          {!!debt.timeline?.length && (
            <div className="mt-4">
              <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">Timeline</div>
              <List items={debt.timeline.map((t: Json) => `${t.when}: ${t.what}`)} />
            </div>
          )}
        </Card>
      </div>

      <Card title="Personality" icon={<Brain className="h-4 w-4 text-violet-500" />}>
        <Para text={pers.summary} />
        <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3 mt-4">
          {(pers.traits || []).map((t: Json) => (
            <div key={t.trait} className="rounded-lg border border-slate-200 p-3">
              <div className="flex items-center justify-between mb-1">
                <span className="font-medium text-sm">{t.trait}</span>
                <span className="flex items-center gap-2 text-xs text-slate-500">{t.level}<LevelBar level={t.level} /></span>
              </div>
              <div className="text-sm text-slate-700">{t.description}</div>
              <Evidence items={t.evidence} />
            </div>
          ))}
        </div>
        {!!pers.other_traits?.length && (
          <div className="grid md:grid-cols-2 gap-3 mt-3">
            {pers.other_traits.map((t: Json, i: number) => (
              <div key={i} className="rounded-lg bg-violet-50/60 border border-violet-100 p-3">
                <div className="font-medium text-sm text-violet-900">{t.trait}</div>
                <div className="text-sm text-slate-700">{t.description}</div>
                <Evidence items={t.evidence} />
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Communication style" icon={<MessageCircle className="h-4 w-4 text-sky-500" />}>
        <Para text={com.summary} />
        <div className="grid md:grid-cols-2 gap-x-6 gap-y-2.5 mt-4">
          <KV k="Pace" v={com.pace} />
          <KV k="How much they say" v={com.verbosity} />
          <KV k="Register & vocabulary" v={com.register_and_vocabulary} />
          <KV k="Regional / cultural markers" v={com.regional_or_cultural_markers} />
          <KV k="How they answer questions" v={com.how_they_answer_questions} />
          <KV k="How they ask questions" v={com.how_they_ask_questions} />
          <KV k="Listening & interruptions" v={com.listening_and_interruptions} />
          <KV k="Humour" v={com.humour} />
        </div>
        {!!com.fillers_and_habits?.length && (
          <div className="mt-4 flex flex-wrap gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mr-1 mt-0.5">Fillers & habits</span>
            {com.fillers_and_habits.map((f: string, i: number) => <Badge key={i} tone="blue">{f}</Badge>)}
          </div>
        )}
        {!!com.signature_phrases?.length && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mr-1 mt-0.5">Signature phrases</span>
            {com.signature_phrases.map((f: string, i: number) => <span key={i} className="chip bg-sky-50 text-sky-900 ring-1 ring-sky-100 italic">“{f}”</span>)}
          </div>
        )}
      </Card>

      <Card title="Emotional profile" icon={<HeartPulse className="h-4 w-4 text-rose-500" />}>
        <div className="grid md:grid-cols-2 gap-4">
          <KV k="Baseline mood" v={emo.baseline_mood} />
          <KV k="Emotional range" v={emo.emotional_range} />
        </div>
        {!!emo.triggers?.length && (
          <div className="mt-4">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1.5">Triggers</div>
            <div className="overflow-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-slate-500"><tr><th className="py-1 pr-3">Trigger</th><th className="py-1 pr-3">Reaction</th><th className="py-1">Evidence</th></tr></thead>
                <tbody>
                  {emo.triggers.map((t: Json, i: number) => (
                    <tr key={i} className="border-t border-slate-100 align-top">
                      <td className="py-1.5 pr-3 font-medium">{t.trigger}</td>
                      <td className="py-1.5 pr-3">{t.reaction}</td>
                      <td className="py-1.5 italic text-slate-500">{t.evidence && `“${t.evidence}”`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
        <div className="grid md:grid-cols-2 gap-4 mt-4">
          <div><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Coping strategies</div><List items={emo.coping_strategies} /></div>
          <div><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Stress signals</div><List items={emo.stress_signals} /></div>
        </div>
        {!!emo.emotional_arc?.length && (
          <div className="mt-4">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-2">Emotional arc through the call</div>
            <div className="flex gap-2 overflow-x-auto pb-1 scroll-thin">
              {emo.emotional_arc.map((a: Json, i: number) => (
                <div key={i} className="min-w-[180px] max-w-[220px] rounded-lg border border-rose-100 bg-rose-50/40 p-2.5">
                  <div className="text-[11px] text-slate-500">{a.point}</div>
                  <div className="font-medium text-sm text-rose-800">{a.emotion}</div>
                  <div className="text-xs text-slate-600 mt-0.5">{a.what_happened}</div>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Understanding & decision-making" icon={<Brain className="h-4 w-4 text-amber-500" />}>
          <div className="space-y-2.5">
            <KV k="Comprehension" v={cog.comprehension} />
            <KV k="Financial literacy" v={cog.financial_literacy} />
            <KV k="Numbers & memory for figures" v={cog.numeracy_and_memory_for_figures} />
            <KV k="Need for repetition" v={cog.need_for_repetition} />
            <KV k="Decision-making style" v={cog.decision_making_style} />
          </div>
          <Evidence items={cog.evidence} />
        </Card>
        <Card title="Money, debt & what they want" icon={<Target className="h-4 w-4 text-emerald-500" />}>
          <div className="space-y-2.5">
            <KV k="Attitude to debt" v={mon.attitude_to_debt} />
            <KV k="Shame / guilt" v={mon.shame_or_guilt} />
            <KV k="Responsibility" v={mon.sense_of_responsibility} />
            <KV k="Spending & budgeting" v={mon.spending_and_budgeting} />
            <KV k="Motivation" v={mon.motivation} />
          </div>
          <div className="grid sm:grid-cols-3 gap-3 mt-3">
            <div><div className="text-[11px] font-semibold uppercase text-slate-400 mb-1">Goals</div><List items={mon.goals} /></div>
            <div><div className="text-[11px] font-semibold uppercase text-slate-400 mb-1">Fears</div><List items={mon.fears} /></div>
            <div><div className="text-[11px] font-semibold uppercase text-slate-400 mb-1">Red lines</div><List items={mon.red_lines} /></div>
          </div>
        </Card>
      </div>

      <Card title="Vulnerability" icon={<ShieldAlert className="h-4 w-4 text-rose-500" />}>
        <Para text={vul.summary} />
        <div className="grid md:grid-cols-2 gap-3 mt-4">
          {(vul.fca_drivers || []).map((v: Json, i: number) => (
            <div key={i} className="rounded-lg border border-slate-200 p-3">
              <div className="flex items-center gap-2 mb-1"><span className="font-medium text-sm">{v.driver}</span><Badge tone={sevTone(v.severity)}>{v.severity}</Badge></div>
              <div className="text-sm text-slate-700">{v.detail}</div>
              {v.evidence && <Evidence items={[v.evidence]} />}
            </div>
          ))}
        </div>
        <div className="grid md:grid-cols-2 gap-4 mt-4">
          <div><div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Support needs</div><List items={vul.support_needs} /></div>
          <KV k="How they disclosed" v={vul.disclosure_behaviour} />
        </div>
      </Card>

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="Trust & rapport">
          <div className="space-y-2.5">
            <KV k="Initial stance" v={tru.initial_stance} />
            <KV k="Attitude to the advisor" v={tru.attitude_to_advisor} />
            <KV k="Attitude to the company / process" v={tru.attitude_to_company_and_process} />
          </div>
          <div className="grid sm:grid-cols-2 gap-3 mt-3">
            <div><div className="text-[11px] font-semibold uppercase text-emerald-600 mb-1">Builds trust</div><List items={tru.what_builds_trust} /></div>
            <div><div className="text-[11px] font-semibold uppercase text-rose-600 mb-1">Damages trust</div><List items={tru.what_damages_trust} /></div>
          </div>
        </Card>
        <Card title="Engagement over the call">
          <div className="space-y-2.5">
            <KV k="Summary" v={eng.summary} />
            <KV k="Fatigue / drop-off" v={eng.fatigue_or_drop_off} />
            <KV k="Attention & distractions" v={eng.attention_and_distractions} />
          </div>
        </Card>
      </div>

      {!!deep.solution_attitudes?.length && (
        <Card title="Reactions to the debt solutions">
          <div className="overflow-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase text-slate-500"><tr><th className="py-1.5 pr-3">Solution</th><th className="py-1.5 pr-3">Reaction</th><th className="py-1.5 pr-3">Their reasoning</th><th className="py-1.5">In their words</th></tr></thead>
              <tbody>
                {deep.solution_attitudes.map((s: Json, i: number) => (
                  <tr key={i} className="border-t border-slate-100 align-top">
                    <td className="py-2 pr-3 font-medium">{s.solution}</td>
                    <td className="py-2 pr-3">{s.reaction}</td>
                    <td className="py-2 pr-3 text-slate-600">{s.reasoning_in_their_words}</td>
                    <td className="py-2 italic text-slate-500">{s.quote && `“${s.quote}”`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <div className="grid lg:grid-cols-2 gap-5">
        <Card title="People in their life" icon={<Users className="h-4 w-4 text-slate-500" />}>
          <div className="space-y-2">
            {(deep.relationships || []).map((r: Json, i: number) => (
              <div key={i} className="text-sm"><span className="font-medium">{r.person}</span> – {r.role_in_story}{r.how_they_talk_about_them && <span className="text-slate-500"> · {r.how_they_talk_about_them}</span>}</div>
            ))}
            {!deep.relationships?.length && <div className="text-sm text-slate-400">—</div>}
          </div>
        </Card>
        <Card title="Inconsistencies & uncertainties">
          <List items={(deep.inconsistencies || []).map((x: Json) => `${x.topic}: ${x.detail}`)} empty="None noted" />
          {deep.confidence_notes && <div className="mt-3 text-xs text-slate-500">{deep.confidence_notes}</div>}
        </Card>
      </div>

      {!!deep.quote_library?.length && (
        <Card title="In their own words" icon={<Quote className="h-4 w-4 text-emerald-500" />}>
          <div className="grid md:grid-cols-2 gap-2">
            {deep.quote_library.map((q: Json, i: number) => (
              <div key={i} className="rounded-lg bg-emerald-50/50 border border-emerald-100 px-3 py-2">
                <div className="text-[11px] uppercase tracking-wide text-emerald-700">{q.theme}</div>
                <div className="text-sm italic text-slate-800">“{q.quote}”</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card title="Guidance for advisors" icon={<Target className="h-4 w-4 text-indigo-500" />}>
        <Para text={adv.how_to_approach} />
        <div className="grid md:grid-cols-3 gap-4 mt-4">
          <div><div className="text-[11px] font-semibold uppercase text-emerald-600 mb-1">Do</div><List items={adv.dos} /></div>
          <div><div className="text-[11px] font-semibold uppercase text-rose-600 mb-1">Don't</div><List items={adv.donts} /></div>
          <div><div className="text-[11px] font-semibold uppercase text-amber-600 mb-1">Likely challenges</div><List items={adv.likely_challenges} /></div>
        </div>
      </Card>
    </div>
  );
}
