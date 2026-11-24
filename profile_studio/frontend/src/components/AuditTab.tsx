import { ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { api, fmtDate, Json } from "../api";
import { Badge, Empty, ErrorBox, KV, Spinner, StatusBadge, statusTone } from "./ui";

function reasoningItems(r: unknown): { heading: string; comment: string; positive?: boolean }[] {
  if (!r) return [];
  let v: unknown = r;
  if (typeof r === "string") {
    try {
      v = JSON.parse(r);
    } catch {
      return [{ heading: "", comment: r }];
    }
  }
  return Array.isArray(v) ? (v as Json[]).map((x) => (typeof x === "object" ? x : { heading: "", comment: String(x) })) : [];
}

function Question({ q }: { q: Json }) {
  const [open, setOpen] = useState(false);
  const items = reasoningItems(q.reasoning);
  return (
    <div className="border-t border-slate-100">
      <button className="w-full text-left flex items-start gap-3 px-4 py-2.5 hover:bg-slate-50" onClick={() => setOpen(!open)}>
        <ChevronRight className={`h-4 w-4 mt-0.5 shrink-0 text-slate-400 transition ${open ? "rotate-90" : ""}`} />
        <span className="w-6 text-right font-mono text-xs text-slate-400 mt-0.5">{q.order}</span>
        <span className="flex-1 text-sm">{(q.question || "").split("\n")[0]}</span>
        <StatusBadge status={q.status} />
        {q.max_score ? <Badge>{q.score}/{q.max_score}</Badge> : null}
      </button>
      {open && (
        <div className="px-4 pb-4 pl-16 space-y-3">
          {q.question?.includes("\n") && <pre className="whitespace-pre-wrap text-xs text-slate-500 font-sans">{q.question}</pre>}
          {q.timestamps && <div className="text-xs text-slate-500">Timestamps: {q.timestamps}</div>}
          {items.map((it, i) => (
            <div key={i} className={`rounded-lg p-3 text-sm ${it.positive === false ? "bg-rose-50/60" : it.positive ? "bg-emerald-50/60" : "bg-slate-50"}`}>
              {it.heading && <div className="font-medium mb-1">{it.heading}</div>}
              <div className="whitespace-pre-wrap text-slate-700">{it.comment}</div>
            </div>
          ))}
          {!!q.children?.length && (
            <div className="rounded-lg border border-slate-200">
              {q.children.map((c: Json) => (
                <div key={c.question_id} className="flex items-start gap-2 px-3 py-2 border-t first:border-t-0 border-slate-100 text-sm">
                  <span className="flex-1">{c.question}</span>
                  <StatusBadge status={c.status} />
                  {c.max_score ? <Badge>{c.score}/{c.max_score}</Badge> : null}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AuditTab({ refId, calls }: { refId: string; calls: Json[] }) {
  const [callId, setCallId] = useState<number | null>(calls[calls.length - 1]?.call_id ?? null);
  const [data, setData] = useState<Json>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (callId == null) return;
    setData(null);
    api.audit(refId, callId).then(setData).catch((e) => setError(e.message));
  }, [refId, callId]);

  if (!calls.length) return <div className="card"><Empty title="No calls fetched" /></div>;
  const t = data?.totals || {};
  const stageQs = (data?.questions || []).filter((q: Json) => q.order <= 32);
  const crmQs = (data?.questions || []).filter((q: Json) => q.order > 32);

  return (
    <div className="space-y-4">
      <div className="card p-4 flex flex-wrap items-end gap-4">
        <div>
          <label className="label">Call</label>
          <select className="input" value={callId ?? ""} onChange={(e) => setCallId(Number(e.target.value))}>
            {calls.map((c) => <option key={c.call_id} value={c.call_id}>{c.call_id} · {fmtDate(c.call_date)}</option>)}
          </select>
        </div>
        {data && (
          <>
            <KV k="Score" v={<Badge tone={statusTone(t.outcome)}>{t.score}/{t.max} · {t.pct}% · {t.outcome}</Badge>} />
            <KV k="Complete / partial / omitted" v={`${t.complete} / ${t.partial} / ${t.omitted}`} />
            <KV k="Review" v={`${data.submitted?.review_status || "—"} (v${data.submitted?.version ?? "?"})`} />
            <KV k="Submitted" v={fmtDate(data.submitted?.submitted_at)} />
          </>
        )}
      </div>
      <ErrorBox error={error} />
      {!data && !error && <div className="p-8 grid place-items-center"><Spinner /></div>}
      {data && (
        <>
          <div className="card overflow-hidden">
            <div className="px-4 py-3 font-semibold">Script stages (questions 1–32)</div>
            {stageQs.map((q: Json) => <Question key={q.question_id} q={q} />)}
          </div>
          <div className="card overflow-hidden">
            <div className="px-4 py-3 font-semibold">CRM checks</div>
            {crmQs.map((q: Json) => <Question key={q.question_id} q={q} />)}
          </div>
          {!!data.errors?.length && <ErrorBox error={`API reported missing parts: ${JSON.stringify(data.errors)}`} />}
        </>
      )}
    </div>
  );
}
