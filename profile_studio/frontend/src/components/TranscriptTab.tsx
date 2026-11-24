import { Search } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, fmtDate, Json } from "../api";
import { Badge, Empty, ErrorBox, Spinner } from "./ui";

function highlight(text: string, q: string) {
  if (!q) return text;
  const parts = text.split(new RegExp(`(${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "ig"));
  return parts.map((p, i) => (p.toLowerCase() === q.toLowerCase() ? <mark key={i}>{p}</mark> : <Fragment key={i}>{p}</Fragment>));
}

export default function TranscriptTab({ refId, calls, focus }: { refId: string; calls: Json[]; focus: { callId: number; stage: number } | null }) {
  // 0 = the whole conversation (all calls merged in order, duplicates removed)
  const [callId, setCallId] = useState<number | null>(focus?.callId ?? 0);
  const [convAvailable, setConvAvailable] = useState<boolean | null>(null);
  const [data, setData] = useState<Json>(null);
  const [stages, setStages] = useState<Json[]>([]);
  const [stage, setStage] = useState<number>(focus?.stage ?? 0);
  const [q, setQ] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const firstRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.stages().then(setStages).catch(() => {});
  }, []);
  useEffect(() => {
    if (focus) {
      setCallId(focus.callId);
      setStage(focus.stage);
    }
  }, [focus]);
  useEffect(() => {
    if (callId == null) return;
    setLoading(true);
    const load = callId === 0
      ? api.conversation(refId).then((d) => {
          setConvAvailable(Boolean(d.available));
          if (!d.available) {
            setCallId(calls[calls.length - 1]?.call_id ?? null);
            return null;
          }
          return { ...d, roles: {}, roles_how: "" };
        })
      : api.transcript(refId, callId);
    load.then((d) => { if (d) { setData(d); setError(null); } }).catch((e) => setError(e.message)).finally(() => setLoading(false));
  }, [refId, callId]);

  const lines: Json[] = useMemo(() => {
    let ls: Json[] = data?.lines || [];
    if (stage) ls = ls.filter((l) => l.stages.includes(stage));
    if (q.trim()) ls = ls.filter((l) => l.text.toLowerCase().includes(q.trim().toLowerCase()));
    return ls;
  }, [data, stage, q]);

  useEffect(() => {
    firstRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [stage, callId, data]);

  const stageName = (n: number) => stages.find((s) => s.n === n)?.name?.replace("Solution: ", "") || `Stage ${n}`;
  if (!calls.length) return <div className="card"><Empty title="No calls fetched" /></div>;

  let lastStageKey = "";
  let lastCall: number | null = null;
  return (
    <div className="space-y-3">
      <div className="card p-3 flex flex-wrap items-end gap-3 sticky top-0 z-10">
        <div>
          <label className="label">Call</label>
          <select className="input" value={callId ?? ""} onChange={(e) => setCallId(Number(e.target.value))}>
            {convAvailable !== false && <option value={0}>Whole conversation{calls.length > 1 ? " (all calls, in order)" : ""}</option>}
            {calls.map((c) => <option key={c.call_id} value={c.call_id}>Call {c.call_id} · {fmtDate(c.call_date)}{c.case_id?.endsWith("-W") ? " (-W)" : ""}</option>)}
          </select>
        </div>
        <div>
          <label className="label">Stage</label>
          <select className="input" value={stage} onChange={(e) => setStage(Number(e.target.value))} disabled={!data?.segmented}>
            <option value={0}>All stages</option>
            {stages.map((s) => <option key={s.n} value={s.n}>{s.n}. {s.name}</option>)}
          </select>
        </div>
        <div className="flex-1 min-w-[200px]">
          <label className="label">Search</label>
          <div className="relative">
            <Search className="h-4 w-4 absolute left-2.5 top-2 text-slate-400" />
            <input className="input pl-8" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find words in the transcript" />
          </div>
        </div>
        <div className="text-xs text-slate-500 pb-2">
          {lines.length} lines
          {data && callId !== 0 && <> · speakers: {Object.entries(data.roles || {}).map(([k, v]) => `${k}=${v}`).join(", ") || "unknown"}</>}
        </div>
      </div>
      {callId === 0 && data?.calls && data.calls.length > 1 && (
        <div className="card p-3 text-sm space-y-1">
          {data.calls.map((c: Json) => (
            <div key={c.call_id} className="flex gap-2 items-center">
              {c.included ? <Badge tone="green">Call {c.call_no}</Badge> : <Badge>Skipped</Badge>}
              <span className="font-mono">{c.call_id}</span>
              <span className="text-slate-600">
                {c.included ? `lines ${c.first_line}–${c.last_line} · ${c.opening}` : `duplicate recording of call ${c.contained_in} (${Math.round((c.containment || 0) * 100)}% overlap) – not repeated`}
              </span>
            </div>
          ))}
        </div>
      )}
      {data?.note && <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">{data.note}</div>}
      {data && !data.segmented && (
        <div className="text-xs text-slate-500">Stage markers appear after the profile is built.{data.roles_how ? ` Speaker roles here come from the audit evidence (${data.roles_how}).` : ""}</div>
      )}
      <ErrorBox error={error} />
      {loading && <div className="p-8 grid place-items-center"><Spinner /></div>}
      <div className="card p-4 space-y-1.5">
        <div ref={firstRef} />
        {lines.map((l) => {
          const adv = l.role === "ADVISOR";
          const key = l.stages.join(",");
          const showMarker = data?.segmented && key && key !== lastStageKey && !stage;
          lastStageKey = key || lastStageKey;
          const callBreak = callId === 0 && data?.multi_call && l.call_no !== lastCall;
          lastCall = l.call_no ?? lastCall;
          return (
            <Fragment key={l.i}>
              {callBreak && (
                <div className="flex items-center gap-2 pt-4 pb-1">
                  <div className="h-px flex-1 bg-emerald-200" />
                  <Badge tone="green">Call {l.call_no} · {l.call_id}</Badge>
                  <div className="h-px flex-1 bg-emerald-200" />
                </div>
              )}
              {showMarker && (
                <div className="flex items-center gap-2 pt-3 pb-1">
                  <div className="h-px flex-1 bg-indigo-100" />
                  {l.stages.map((n: number) => <Badge key={n} tone="indigo">{n}. {stageName(n)}</Badge>)}
                  <div className="h-px flex-1 bg-indigo-100" />
                </div>
              )}
              <div className={`flex gap-3 ${adv ? "" : "flex-row-reverse"} ${l.non_customer ? "opacity-60" : ""}`}>
                <div className={`max-w-[78%] rounded-2xl px-3.5 py-2 text-sm ${adv ? "bg-slate-100 rounded-tl-sm" : "bg-emerald-50 ring-1 ring-emerald-100 rounded-tr-sm"}`}>
                  <div className="flex items-center gap-2 text-[11px] text-slate-500 mb-0.5">
                    <span className={`font-semibold ${adv ? "text-sky-700" : "text-emerald-700"}`}>{l.non_customer ? "OTHER (hand-over)" : l.role}</span>
                    <span className="font-mono">{l.t}</span>
                    <span className="font-mono text-slate-300">#{l.i}</span>
                  </div>
                  <div className="leading-relaxed">{highlight(l.text, q.trim())}</div>
                </div>
              </div>
            </Fragment>
          );
        })}
        {!loading && !lines.length && <Empty title="No lines match" />}
      </div>
    </div>
  );
}
