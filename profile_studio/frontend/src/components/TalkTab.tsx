import { FileImage, FileText, Paperclip, Phone, PhoneOff, RotateCcw, Send, Sparkles } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, Json } from "../api";
import { Badge, Empty, ErrorBox, Spinner } from "./ui";

type Turn = { role: "advisor" | "customer"; text: string; stage?: number | null; mood?: string | null; actions?: Json[]; latency_s?: number };

const QUICK: [string, string][] = [
  ["Open call", "Hello, this is Katie from Debt Advisory Services. Calls are recorded for training and monitoring purposes. Am I speaking with {first}?"],
  ["DOB", "Before we go any further, can you confirm your date of birth for me please?"],
  ["Address", "Thank you. And can you confirm the first line of your address and your postcode?"],
  ["Photo ID", "Thank you, that's all confirmed. There's one more quick security step: I need to check a photo ID, such as your passport or driving licence. I'm sending you a secure link by email now. Let me know once you've uploaded it."],
  ["Biggest concern", "So, in your own words, what's happening with your debts at the moment, and what's your biggest concern?"],
  ["Vulnerability", "Do you have any physical or mental health conditions that affect your day-to-day life?"],
  ["Docs to hand?", "Do you have a wage slip or a bank statement to hand?"],
  ["Send links + wait", "I've just sent an email to the email address we have on file, with one secure link for your wage slip and one for your bank statement. Please open it now and click each link to upload that document. I'll wait on the line while you do it, so just let me know once they are uploaded."],
  ["Uploaded yet?", "Have you managed to upload them yet?"],
  ["CCJs", "Do you have any County Court Judgements, or is anyone a guarantor for any of your debts?"],
  ["Income", "Can you talk me through all the money that comes into the household each month?"],
];

function Attachment({ a }: { a: Json }) {
  const tone = a.type === "upload" ? "border-emerald-200 bg-emerald-50" : a.type === "cannot_provide" ? "border-rose-200 bg-rose-50" : "border-amber-200 bg-amber-50";
  const label = a.type === "upload" ? "Uploaded" : a.type === "cannot_provide" ? "Can't provide" : "Will upload later";
  return (
    <div className={`mt-2 rounded-lg border px-3 py-2 text-xs ${tone}`}>
      <div className="font-semibold mb-1 flex items-center gap-1"><Paperclip className="h-3.5 w-3.5" />{label}: {a.documents.join(", ").replace(/_/g, " ")}{a.reason ? ` – ${a.reason}` : ""}</div>
      {(a.files || []).map((f: Json) => (
        <a key={f.key} href={f.url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-slate-700 hover:underline">
          {f.mime?.startsWith("image/") ? <FileImage className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
          {f.file_name}
        </a>
      ))}
      {!!a.no_file_on_record?.length && <div className="text-slate-500">No file on record for: {a.no_file_on_record.join(", ")}</div>}
    </div>
  );
}

export default function TalkTab({ refId, hasProfile }: { refId: string; hasProfile: boolean }) {
  const [persona, setPersona] = useState<Json>(null);
  const [docs, setDocs] = useState<Json[]>([]);
  const [health, setHealth] = useState<Json>(null);
  const [sessionId, setSessionId] = useState<string | null>(() => {
    try { return localStorage.getItem(`talk_${refId}`); } catch { return null; }
  });
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!hasProfile) return;
    api.customer(refId).then(setPersona).catch((e) => setError(e.message));
    api.customerDocs(refId).then((d) => setDocs(d.documents || [])).catch(() => {});
    api.agentHealth().then(setHealth).catch(() => {});
  }, [refId, hasProfile]);

  useEffect(() => {
    if (!sessionId) return;
    api.session(sessionId).then((s) => setTurns((s.history || []) as Turn[])).catch(() => {
      setSessionId(null);
    });
  }, [sessionId]);

  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [turns.length, busy]);

  const uploads = useMemo(() => turns.flatMap((t) => (t.actions || []).filter((a) => a.type === "upload")), [turns]);
  const first = (persona?.profile?.name || "").split(" ")[0];

  const remember = (sid: string | null) => {
    setSessionId(sid);
    try { sid ? localStorage.setItem(`talk_${refId}`, sid) : localStorage.removeItem(`talk_${refId}`); } catch { /* ignore */ }
  };

  const send = async (textArg?: string) => {
    const text = (textArg ?? input).replace("{first}", first).trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    setInput("");
    setTurns((t) => [...t, { role: "advisor", text }]);
    try {
      const r = sessionId && turns.length
        ? await api.say(persona.id, text, sessionId)
        : await api.startCall(persona.id, text);
      if (r.session_id !== sessionId) remember(r.session_id);
      setTurns((t) => [...t, { role: "customer", text: r.reply, stage: r.stage, mood: r.mood, actions: r.actions }]);
    } catch (e) {
      setError((e as Error).message);
      setTurns((t) => t.slice(0, -1));
      setInput(text);
    } finally {
      setBusy(false);
    }
  };

  const newCall = () => {
    remember(null);
    setTurns([]);
    setError(null);
  };

  if (!hasProfile) {
    return <div className="card"><Empty icon={<Sparkles className="h-10 w-10" />} title="Build the profile first">The customer agent is created from the case's profile.</Empty></div>;
  }
  if (!persona) return error ? <ErrorBox error={error} /> : <div className="p-8 grid place-items-center"><Spinner /></div>;

  return (
    <div className="flex gap-5 items-start">
      <div className="flex-1 min-w-0 card flex flex-col h-[calc(100vh-230px)] min-h-[520px]">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-slate-200">
          <div className="h-9 w-9 rounded-full bg-emerald-100 text-emerald-700 grid place-items-center font-semibold">{first?.[0] || "?"}</div>
          <div className="min-w-0">
            <div className="font-semibold">{persona.promptName} <span className="text-xs font-normal text-slate-500">· {persona.id}</span></div>
            <div className="text-xs text-slate-500 truncate">{persona.segment}</div>
          </div>
          {persona.difficulty && <Badge tone={persona.difficulty === "hard" ? "red" : persona.difficulty === "medium" ? "amber" : "green"}>{persona.difficulty}</Badge>}
          <button className="btn-secondary ml-auto" onClick={newCall} disabled={busy}>
            {turns.length ? <><RotateCcw className="h-4 w-4" /> New call</> : <><Phone className="h-4 w-4" /> Ready</>}
          </button>
        </div>
        <div className="flex-1 overflow-auto scroll-thin p-4 space-y-3 bg-slate-50/50">
          {!turns.length && (
            <Empty icon={<Phone className="h-10 w-10" />} title={`Call ${persona.promptName}`}>
              You are the advisor. Type what you'd say (or use a quick line below) – the customer answers in character, from their real call.
            </Empty>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`flex ${t.role === "advisor" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[75%] rounded-2xl px-4 py-2.5 text-sm shadow-sm ${t.role === "advisor" ? "bg-indigo-600 text-white rounded-br-sm" : "bg-white ring-1 ring-slate-200 rounded-bl-sm"}`}>
                <div className={`text-[11px] mb-0.5 ${t.role === "advisor" ? "text-indigo-200" : "text-slate-400"}`}>
                  {t.role === "advisor" ? "You (advisor)" : persona.promptName}
                  {t.role === "customer" && t.mood ? ` · ${t.mood}` : ""}
                  {t.role === "customer" && t.stage ? ` · stage ${t.stage}` : ""}
                </div>
                <div className="leading-relaxed whitespace-pre-wrap">{t.text}</div>
                {(t.actions || []).map((a, j) => <Attachment key={j} a={a} />)}
              </div>
            </div>
          ))}
          {busy && <div className="flex items-center gap-2 text-sm text-slate-500"><Spinner /> {persona.promptName} is answering…</div>}
          <div ref={end} />
        </div>
        {error && <div className="px-4 pt-2"><ErrorBox error={error} /></div>}
        <div className="border-t border-slate-200 p-3 space-y-2">
          <div className="flex flex-wrap gap-1.5">
            {QUICK.map(([label, text]) => (
              <button key={label} className="chip bg-slate-100 text-slate-700 hover:bg-indigo-100 hover:text-indigo-800" disabled={busy}
                onClick={() => setInput(text.replace("{first}", first))}>{label}</button>
            ))}
          </div>
          <div className="flex gap-2">
            <textarea className="input h-16 resize-none" placeholder="Say something as the advisor… (Enter to send, Shift+Enter for a new line)"
              value={input} onChange={(e) => setInput(e.target.value)} disabled={busy}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
            <button className="btn-primary self-stretch px-4" onClick={() => send()} disabled={busy || !input.trim()}>
              {busy ? <Spinner /> : <Send className="h-4 w-4" />}
            </button>
          </div>
        </div>
      </div>

      <div className="w-72 shrink-0 space-y-4 sticky top-0">
        <div className="card p-4 text-sm space-y-1.5">
          <div className="font-semibold mb-1">On the case file</div>
          <div><span className="text-slate-400">Name:</span> {persona.profile?.name}</div>
          <div><span className="text-slate-400">DOB:</span> {persona.profile?.date_of_birth}</div>
          <div><span className="text-slate-400">Address:</span> {persona.profile?.address_line}</div>
          <div><span className="text-slate-400">Postcode:</span> {persona.profile?.postcode}</div>
          <div><span className="text-slate-400">Email:</span> {persona.profile?.email}</div>
          {health && <div className="pt-2 text-xs text-slate-400">Model {health.model} · effort {health.effort}</div>}
        </div>
        <div className="card p-4">
          <div className="font-semibold text-sm mb-2">Documents this customer has ({docs.length})</div>
          <div className="space-y-1 max-h-60 overflow-auto scroll-thin">
            {docs.map((d) => (
              <a key={d.key} href={d.url} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-xs text-slate-700 hover:underline">
                <Badge tone="blue">{d.category.replace(/_/g, " ")}</Badge><span className="truncate">{d.file_name}</span>
              </a>
            ))}
            {!docs.length && <div className="text-xs text-slate-400">No documents downloaded for this case.</div>}
          </div>
        </div>
        <div className="card p-4">
          <div className="font-semibold text-sm mb-2">Uploaded in this call ({uploads.reduce((n, a) => n + (a.files?.length || 0), 0)})</div>
          {uploads.length ? uploads.map((a, i) => <Attachment key={i} a={a} />) : <div className="text-xs text-slate-400">Nothing yet.</div>}
        </div>
        {turns.length > 0 && (
          <button className="btn-ghost w-full text-xs" onClick={newCall}><PhoneOff className="h-3.5 w-3.5" /> End and start a new call</button>
        )}
      </div>
    </div>
  );
}
