import { Check, Copy, Download, FileText, Pencil, Save, Send, Sparkles, X } from "lucide-react";
import DeepProfile from "./DeepProfile";
import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { Badge, Empty, ErrorBox, KV, List, Spinner } from "./ui";

const GROUPS: [string, string[]][] = [
  ["Who they are", ["backstory", "personality_traits", "key_people"]],
  ["How they talk", ["communication_style", "speech_patterns", "signature_phrases"]],
  ["Emotions", ["emotional_triggers", "coping_style", "stress_signals"]],
  ["Thinking & deciding", ["understanding_level", "decision_style", "solution_preferences"]],
  ["Motivation", ["attitude_to_debt", "goals", "fears", "red_lines", "trust_builders", "trust_breakers"]],
  ["Identity", ["name", "gender", "date_of_birth", "address_line", "postcode", "email"]],
  ["Personality & mood", ["mood", "personality", "biggest_concern", "daily_impact"]],
  ["Debt story", ["debt_cause", "trigger_point", "duration", "previous_solutions", "temporary_payment_plan_details", "tried_so_far", "collection_activity"]],
  ["Debts", ["total_debt", "monthly_contractual_payments", "affordable_payment", "joint_debts", "guarantor_debts", "priority_bills", "priority_bills_last_checked", "additional_debt_checks", "recent_account_spending", "preferential_payments"]],
  ["Loans", ["loan_position", "loan_decline_reason"]],
  ["Household", ["partner_status", "dependents", "household", "housing", "family_discussed", "family_financial_help", "partner_income_support", "third_party_help"]],
  ["Work & money", ["employment", "employer", "benefits", "income", "expenses", "assets", "future_changes"]],
  ["Vulnerability", ["vulnerability", "medical_support", "consent_to_recording", "consent_to_note_vulnerability"]],
];

const label = (k: string) => k.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

export default function ProfileTab({ refId, profile, onSaved, onBuild, building }: {
  refId: string; profile: Json; onSaved: () => void; onBuild: () => void; building: boolean;
}) {
  const persona = profile?.persona;
  const full = profile?.persona_full || {};
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [view, setView] = useState<"deep" | "persona" | "guide">("deep");

  useEffect(() => {
    if (persona) setDraft(JSON.stringify(persona, null, 2));
  }, [persona]);

  if (!persona) {
    return (
      <div className="card">
        <Empty icon={<Sparkles className="h-10 w-10" />} title="No customer profile yet">
          {profile?.meta?.status === "running" ? "Building now – follow progress above." : "Build it from the calls and CAT data."}
          <div className="mt-4">
            <button className="btn-primary" onClick={onBuild} disabled={building}>{building ? <Spinner /> : <Sparkles className="h-4 w-4" />} Build profile</button>
          </div>
        </Empty>
      </div>
    );
  }
  const p = persona.profile || {};

  const save = async () => {
    setError(null);
    let obj: Json;
    try {
      obj = JSON.parse(draft);
    } catch (e) {
      setError("Not valid JSON: " + (e as Error).message);
      return;
    }
    setBusy(true);
    try {
      await api.savePersona(refId, obj);
      setEditing(false);
      setMsg("Saved.");
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const exportAgent = async () => {
    setError(null);
    setMsg(null);
    setBusy(true);
    try {
      const r = await api.exportAgent(refId);
      setMsg(`${r.replaced ? "Updated" : "Added"} “${r.id}” in ${r.path} (${r.count} personas). ${r.note}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    await navigator.clipboard.writeText(JSON.stringify(persona, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="space-y-5">
      <div className="card p-5">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <h2 className="text-xl font-semibold">{persona.label}</h2>
              <span className="font-mono text-xs text-slate-500">{persona.id}</span>
              {full.difficulty && <Badge tone={full.difficulty === "hard" ? "red" : full.difficulty === "medium" ? "amber" : "green"}>{full.difficulty} to handle</Badge>}
              {profile.meta?.anonymised ? <Badge tone="violet">anonymised identity</Badge> : <Badge tone="amber">real identity</Badge>}
            </div>
            <div className="mt-1 text-sm text-slate-600">{persona.segment}</div>
            <div className="mt-2 grid sm:grid-cols-2 gap-3">
              <KV k="Risk" v={persona.risk} />
              <KV k="Hardest stage" v={persona.stage} />
            </div>
            <div className="mt-2 text-xs text-slate-500">{persona.source_pattern}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" onClick={exportAgent} disabled={busy}><Send className="h-4 w-4" /> Export to AI Call Agent</button>
            <a className="btn-secondary" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(persona, null, 2))}`} download={`${persona.id}.json`}>
              <Download className="h-4 w-4" /> JSON
            </a>
            {profile.report_available && (
              <a className="btn-secondary" href={`/api/cases/${refId}/report.md`} download>
                <FileText className="h-4 w-4" /> Profile report
              </a>
            )}
            <button className="btn-secondary" onClick={copy}>{copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />} Copy</button>
            <button className="btn-secondary" onClick={() => setEditing(!editing)}>{editing ? <X className="h-4 w-4" /> : <Pencil className="h-4 w-4" />} {editing ? "Close editor" : "Edit"}</button>
          </div>
        </div>
        {msg && <div className="mt-3 rounded-lg bg-emerald-50 border border-emerald-200 text-emerald-800 text-sm px-3 py-2">{msg}</div>}
        {error && <div className="mt-3"><ErrorBox error={error} /></div>}
      </div>

      {editing && (
        <div className="card p-4">
          <div className="flex items-center justify-between mb-2">
            <div className="font-semibold">Edit persona JSON</div>
            <button className="btn-primary" onClick={save} disabled={busy}>{busy ? <Spinner /> : <Save className="h-4 w-4" />} Save</button>
          </div>
          <textarea className="input font-mono text-xs h-[520px]" value={draft} onChange={(e) => setDraft(e.target.value)} spellCheck={false} />
        </div>
      )}

      <div className="flex gap-1 border-b border-slate-200">
        {([["deep", "In-depth profile"], ["persona", "Agent persona fields"], ["guide", "Behaviour guide"]] as const).map(([k, l]) => (
          <button key={k} className={`tab ${view === k ? "tab-active" : ""}`} onClick={() => setView(k)}>{l}</button>
        ))}
      </div>

      {view === "deep" && <DeepProfile deep={profile.deep} />}

      {view === "persona" && full.overall && (
        <div className="card p-5 grid md:grid-cols-2 gap-4">
          <KV k="Personality" v={full.overall.personality_summary} />
          <KV k="Communication style" v={full.overall.communication_style} />
          <KV k="Emotional arc" v={full.overall.emotional_arc} />
          <KV k="What builds / breaks trust" v={full.overall.trust_building} />
          <div className="md:col-span-2">
            <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400 mb-1">Challenges for the advisor</div>
            <List items={full.overall.key_challenges_for_advisor} />
          </div>
        </div>
      )}

      {view === "persona" && <div className="grid lg:grid-cols-2 gap-5">
        {GROUPS.filter(([, keys]) => keys.some((k) => p[k] !== undefined && p[k] !== "")).map(([title, keys]) => (
          <div key={title} className="card p-4">
            <div className="font-semibold mb-3">{title}</div>
            <div className="space-y-2.5">
              {keys.map((k) => (
                <KV key={k} k={label(k)} v={typeof p[k] === "boolean" ? (p[k] ? "Yes" : "No") : p[k]} />
              ))}
            </div>
          </div>
        ))}
        <div className="card p-4">
          <div className="font-semibold mb-3">Creditors ({(p.creditors || []).length})</div>
          <List items={p.creditors} />
        </div>
        <div className="card p-4">
          <div className="font-semibold mb-3">Additional debt checklist</div>
          {Object.keys(p.additional_debts || {}).length ? (
            <div className="space-y-1.5">
              {Object.entries(p.additional_debts).map(([k, v]) => (
                <div key={k} className="text-sm"><span className="text-slate-400">{label(k)}:</span> {String(v)}</div>
              ))}
            </div>
          ) : <div className="text-sm text-slate-400">None listed – the agent works these out from the creditors.</div>}
        </div>
      </div>}

      {view === "guide" && <div className="card p-5">
        <div className="font-semibold mb-2">Behaviour guide (sent to the AI Call Agent)</div>
        <pre className="whitespace-pre-wrap text-sm text-slate-700 font-sans leading-relaxed">{p.behavior_guide}</pre>
      </div>}
    </div>
  );
}
