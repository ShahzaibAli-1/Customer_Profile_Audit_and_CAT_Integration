import { ChevronRight, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api, fmtDate, Json } from "../api";
import { go } from "../App";
import { Empty, Progress, StatusBadge } from "./ui";

export function JobLog({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Json>(null);
  const box = useRef<HTMLPreElement>(null);
  useEffect(() => {
    let stop = false;
    const tick = () => api.job(jobId).then((j) => !stop && setJob(j)).catch(() => {});
    tick();
    const t = setInterval(tick, 1500);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [jobId]);
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [job?.logs?.length]);
  if (!job) return null;
  return (
    <pre ref={box} className="mt-2 max-h-72 overflow-auto scroll-thin rounded-lg bg-slate-900 text-slate-100 text-[11px] leading-relaxed p-3 whitespace-pre-wrap">
      {job.logs.join("\n") || "Waiting…"}
      {job.error && `\n\nERROR: ${job.error}`}
    </pre>
  );
}

export default function JobsDrawer({ open, onClose, jobs, focus, setFocus }: {
  open: boolean; onClose: () => void; jobs: Json[]; focus: string | null; setFocus: (id: string | null) => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-slate-900/30" onClick={onClose} />
      <div className="w-full max-w-xl bg-white shadow-2xl flex flex-col">
        <div className="h-14 flex items-center px-4 border-b border-slate-200">
          <h3 className="font-semibold">Jobs</h3>
          <button className="ml-auto btn-ghost" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        <div className="flex-1 overflow-auto scroll-thin p-3 space-y-2">
          {!jobs.length && <Empty title="No jobs yet">Fetches and profile builds appear here with live progress.</Empty>}
          {jobs.map((j) => {
            const active = j.status === "running" || j.status === "queued";
            const expanded = focus === j.id;
            return (
              <div key={j.id} className="card p-3">
                <div className="flex items-center gap-2">
                  <button className="flex items-center gap-1 font-medium text-sm" onClick={() => setFocus(expanded ? null : j.id)}>
                    <ChevronRight className={`h-4 w-4 transition ${expanded ? "rotate-90" : ""}`} />
                    {j.title}
                  </button>
                  <StatusBadge status={j.status} />
                  <span className="ml-auto text-xs text-slate-400">{fmtDate(j.created_at)}</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {(j.refs || []).map((r: string) => (
                    <button key={r} className="chip bg-slate-100 text-slate-600 hover:bg-indigo-100 font-mono" onClick={() => { go({ page: "case", ref: r, tab: "overview" }); onClose(); }}>
                      {r}
                    </button>
                  ))}
                </div>
                <div className="mt-2">
                  <Progress done={j.progress?.done || 0} total={j.progress?.total || (active ? 0 : 1)} />
                  <div className="mt-1 text-xs text-slate-500 flex justify-between">
                    <span className="truncate">{j.error ? j.error : j.progress?.message}</span>
                    <span>{j.progress?.total ? `${j.progress.done}/${j.progress.total}` : ""}</span>
                  </div>
                </div>
                {active && (
                  <button className="btn-ghost text-xs mt-1" onClick={() => api.cancelJob(j.id)}>Cancel</button>
                )}
                {expanded && <JobLog jobId={j.id} />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
