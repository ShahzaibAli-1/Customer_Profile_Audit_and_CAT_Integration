import { Bot, MessageSquareQuote, PlayCircle, Sparkles, User } from "lucide-react";
import { useMemo, useState } from "react";
import { Json } from "../api";
import { Badge, Empty, KV, List, Section, Spinner, StatusBadge, statusTone } from "./ui";

const GROUP_ORDER = ["Opening", "Fact Find", "Debts", "Personal", "Income & Expenditure", "Solutions", "Closing"];

function coopTone(c?: string) {
  return c === "high" ? "green" : c === "medium" ? "amber" : c === "low" ? "red" : "gray";
}

function auditOf(s: Json) {
  const inc = (s.audits || []).filter((a: Json) => a.included !== false);
  return (inc.length ? inc : s.audits || [])[0];
}

function StageAnalysis({ c, onShow }: { c: Json; onShow: () => void }) {
  const a = c.analysis;
  const cust = a?.customer || {};
  const adv = a?.advisor || {};
  const audits: Json[] = c.audits || [];
  const reasoning = audits.map((x: Json) => x.reasoning && `[Call ${x.call_id}${x.included === false ? " – duplicate recording" : ""}]\n${x.reasoning}`).filter(Boolean).join("\n\n");
  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 px-4 py-2.5 bg-slate-50 border-b border-slate-200">
        {c.time?.length ? c.time.map((t: string, i: number) => <Badge key={i} tone="indigo">{t}</Badge>) : <Badge>not found in the conversation</Badge>}
        {audits.map((x: Json) => (
          <span key={x.call_id} className={x.included === false ? "opacity-60" : ""} title={x.included === false ? "Audit of a duplicate recording" : `Audit of call ${x.call_id}`}>
            <StatusBadge status={`${audits.length > 1 ? `Call ${x.call_id} audit` : "Audit"}: ${x.status}`} />
            {x.max_score ? <span className="ml-1"><Badge>{x.score}/{x.max_score}</Badge></span> : null}
          </span>
        ))}
        {c.segment_note && <span className="text-xs text-slate-500">{c.segment_note}</span>}
        {c.covered && !c.legacy && (
          <button className="ml-auto btn-ghost text-xs" onClick={onShow}>
            <PlayCircle className="h-3.5 w-3.5" /> Show in transcript
          </button>
        )}
      </div>
      {c.legacy && <div className="px-4 py-2 text-xs text-amber-700 bg-amber-50">Built before calls were merged into one conversation – use “Rebuild everything” to update.</div>}
      {c.error && <div className="px-4 py-2 text-sm text-rose-600">Analysis failed: {c.error}</div>}
      {!a && !c.error && (
        <div className="px-4 py-3 text-sm text-slate-500">{c.covered ? "No analysis." : "This stage wasn't found anywhere in the conversation."}</div>
      )}
      {a && (
        <div className="p-4 space-y-4">
          <p className="text-sm text-slate-700">{a.summary}</p>
          <div className="grid lg:grid-cols-2 gap-4">
            <div className="rounded-lg border border-emerald-200 bg-emerald-50/40 p-3">
              <div className="flex items-center gap-1.5 font-semibold text-emerald-800 mb-2"><User className="h-4 w-4" /> Customer behaviour</div>
              <p className="text-sm mb-3">{cust.behaviour}</p>
              <div className="flex flex-wrap gap-1.5 mb-3">
                {cust.tone && <Badge tone="blue">tone: {cust.tone}</Badge>}
                {cust.cooperation && <Badge tone={coopTone(cust.cooperation)}>cooperation: {cust.cooperation}</Badge>}
                {cust.answer_length && <Badge>answers: {cust.answer_length}</Badge>}
                {(cust.emotions || []).map((e: string) => <Badge key={e} tone="violet">{e}</Badge>)}
              </div>
              <div className="space-y-2">
                <KV k="Disclosure style" v={cust.disclosure_style} />
                {!!cust.triggers?.length && <Section title="Triggers"><List items={cust.triggers} /></Section>}
                {!!cust.objections_or_resistance?.length && <Section title="Resistance / objections"><List items={cust.objections_or_resistance} /></Section>}
                {!!cust.confusion_or_questions?.length && <Section title="Questions / confusion"><List items={cust.confusion_or_questions} /></Section>}
                {!!cust.speech_patterns?.length && <Section title="Speech patterns"><List items={cust.speech_patterns} /></Section>}
              </div>
            </div>
            <div className="rounded-lg border border-sky-200 bg-sky-50/40 p-3">
              <div className="flex items-center gap-1.5 font-semibold text-sky-800 mb-2"><Bot className="h-4 w-4" /> Advisor</div>
              <p className="text-sm mb-3">{adv.approach}</p>
              {!!adv.questions_asked?.length && <Section title="Asked / said"><List items={adv.questions_asked} /></Section>}
              {!!adv.handled_well?.length && <Section title="Handled well"><List items={adv.handled_well} /></Section>}
              {!!adv.missed_or_weak?.length && <Section title="Missed / weak"><List items={adv.missed_or_weak} /></Section>}
            </div>
          </div>
          {!!cust.quotes?.length && (
            <Section title="Customer in their own words">
              <div className="space-y-1.5">
                {cust.quotes.map((q: string, i: number) => (
                  <blockquote key={i} className="flex gap-2 text-sm italic text-slate-700 border-l-4 border-emerald-300 pl-3">
                    <MessageSquareQuote className="h-4 w-4 shrink-0 text-emerald-400 mt-0.5" /> “{q}”
                  </blockquote>
                ))}
              </div>
            </Section>
          )}
          {!!a.facts_disclosed?.length && (
            <Section title="Facts disclosed">
              <div className="grid sm:grid-cols-2 gap-x-4 gap-y-1">
                {a.facts_disclosed.map((f: Json, i: number) => (
                  <div key={i} className="text-sm"><span className="text-slate-400">{f.topic}:</span> {f.fact}</div>
                ))}
              </div>
            </Section>
          )}
          {a.interaction_dynamic && <KV k="Interaction" v={a.interaction_dynamic} />}
          {a.simulation_guidance && (
            <div className="rounded-lg bg-indigo-50 border border-indigo-100 p-3">
              <div className="text-xs font-semibold uppercase tracking-wide text-indigo-700 mb-1">How the simulated customer should behave</div>
              <div className="text-sm text-indigo-950">{a.simulation_guidance}</div>
            </div>
          )}
          {reasoning && (
            <details className="text-sm">
              <summary className="cursor-pointer text-slate-500">Auditor's reasoning</summary>
              <pre className="mt-2 whitespace-pre-wrap text-xs text-slate-600 bg-slate-50 rounded-lg p-3">{reasoning}</pre>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default function StagesTab({ profile, calls, onBuild, building, onShowTranscript }: {
  profile: Json; calls: Json[]; onBuild: () => void; building: boolean; onShowTranscript: (callId: number, stage: number) => void;
}) {
  const conversation: Json[] = profile?.meta?.conversation || [];
  const stages: Json[] = profile?.stages || [];
  const [selected, setSelected] = useState<number>(1);
  const behaviour = useMemo(() => {
    const m: Record<number, Json> = {};
    (profile?.persona_full?.stage_behaviour || []).forEach((s: Json) => (m[s.n] = s));
    return m;
  }, [profile]);

  if (!stages.length) {
    return (
      <div className="card">
        <Empty icon={<Sparkles className="h-10 w-10" />} title="No stage analysis yet">
          Building the profile maps all 32 stages onto the transcript and describes how the advisor and the customer behaved in each.
          <div className="mt-4">
            <button className="btn-primary" onClick={onBuild} disabled={building || !calls.length}>
              {building ? <Spinner /> : <Sparkles className="h-4 w-4" />} Build profile
            </button>
          </div>
        </Empty>
      </div>
    );
  }

  const stage = stages.find((s) => s.n === selected) || stages[0];
  const beh = behaviour[stage.n];

  return (
    <div className="flex gap-5 items-start">
      <div className="w-72 shrink-0 card p-2 sticky top-0 max-h-[calc(100vh-220px)] overflow-auto scroll-thin">
        {GROUP_ORDER.map((g) => (
          <div key={g} className="mb-2">
            <div className="px-2 py-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">{g}</div>
            {stages.filter((s) => s.group === g).map((s) => {
              const covered = Boolean(s.covered);
              const audit = auditOf(s)?.status;
              const tone = statusTone(audit);
              const dot = !covered ? "bg-slate-300" : tone === "green" ? "bg-emerald-500" : tone === "amber" ? "bg-amber-400" : tone === "red" ? "bg-rose-500" : "bg-sky-400";
              return (
                <button key={s.n} onClick={() => setSelected(s.n)}
                  className={`w-full text-left flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${s.n === stage.n ? "bg-indigo-50 text-indigo-800" : "hover:bg-slate-50"} ${covered ? "" : "text-slate-400"}`}>
                  <span className="w-5 text-right text-xs font-mono text-slate-400">{s.n}</span>
                  <span className={`h-2 w-2 rounded-full shrink-0 ${dot}`} />
                  <span className="truncate">{s.name.replace("Solution: ", "")}</span>
                </button>
              );
            })}
          </div>
        ))}
        <div className="px-2 pt-2 border-t border-slate-100 text-[11px] text-slate-400 space-y-0.5">
          <div><span className="inline-block h-2 w-2 rounded-full bg-emerald-500 mr-1" />found · audit complete</div>
          <div><span className="inline-block h-2 w-2 rounded-full bg-amber-400 mr-1" />found · audit partial</div>
          <div><span className="inline-block h-2 w-2 rounded-full bg-rose-500 mr-1" />found · audit omitted / failed</div>
          <div><span className="inline-block h-2 w-2 rounded-full bg-slate-300 mr-1" />not found in transcript</div>
        </div>
      </div>

      <div className="flex-1 min-w-0 space-y-4">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-slate-400">{stage.group} · Stage {stage.n}</div>
          <h2 className="text-xl font-semibold">{stage.name}</h2>
        </div>
        {beh && (
          <div className="card p-4 border-indigo-200">
            <div className="flex items-center gap-1.5 text-sm font-semibold text-indigo-700 mb-1"><Sparkles className="h-4 w-4" /> Persona behaviour for this stage</div>
            <p className="text-sm">{beh.how_customer_behaves}</p>
            {!!beh.key_lines?.length && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {beh.key_lines.map((l: string, i: number) => <span key={i} className="chip bg-indigo-50 text-indigo-800 ring-1 ring-indigo-100 italic">“{l}”</span>)}
              </div>
            )}
          </div>
        )}
        {conversation.length > 1 && (
          <div className="text-xs text-slate-500">
            Analysed across the whole conversation:{" "}
            {conversation.filter((c) => c.included).map((c) => `call ${c.call_no} = ${c.call_id}`).join(", ")}
            {conversation.some((c) => !c.included) && <> · {conversation.filter((c) => !c.included).map((c) => `${c.call_id} skipped (duplicate of ${c.contained_in})`).join(", ")}</>}
          </div>
        )}
        <StageAnalysis c={stage} onShow={() => onShowTranscript(0, stage.n)} />
      </div>
    </div>
  );
}
