import { FileText, FolderOpen, Plus, Search, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { fmtDay, Json } from "../api";
import { go, Route } from "../App";
import { Spinner } from "./ui";

function ProfileDot({ status }: { status?: string }) {
  const map: Record<string, [string, string]> = {
    ready: ["bg-emerald-500", "Profile ready"],
    running: ["bg-amber-400 animate-pulse", "Profile building"],
    queued: ["bg-amber-300", "Profile queued"],
    failed: ["bg-rose-500", "Profile failed"],
    interrupted: ["bg-rose-400", "Profile interrupted"],
  };
  const [cls, title] = map[status || ""] || ["bg-slate-300", "No profile yet"];
  return <span title={title} className={`h-2.5 w-2.5 rounded-full shrink-0 ${cls}`} />;
}

export default function Sidebar({ cases, route, jobs }: { cases: Json[]; route: Route; jobs: Json[] }) {
  const [q, setQ] = useState("");
  const busyRefs = useMemo(() => {
    const s = new Set<string>();
    jobs.filter((j) => j.status === "running" || j.status === "queued").forEach((j) => (j.refs || []).forEach((r: string) => s.add(r)));
    return s;
  }, [jobs]);
  const list = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return cases.filter((c) => !ql || c.ref.toLowerCase().includes(ql) || (c.name || "").toLowerCase().includes(ql));
  }, [cases, q]);
  const activeRef = route.page === "case" ? route.ref : null;

  return (
    <aside className="w-72 shrink-0 border-r border-slate-200 bg-white flex flex-col min-h-0">
      <div className="p-3 border-b border-slate-200 space-y-2">
        <button onClick={() => go({ page: "fetch" })} className={`w-full ${route.page === "fetch" ? "btn-primary" : "btn-secondary"}`}>
          <Plus className="h-4 w-4" /> Fetch cases
        </button>
        <div className="relative">
          <Search className="h-4 w-4 absolute left-2.5 top-2 text-slate-400" />
          <input className="input pl-8" placeholder="Search case or name" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </div>
      <div className="px-3 pt-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
        Fetched cases ({list.length})
      </div>
      <div className="flex-1 overflow-auto scroll-thin px-2 pb-3">
        {list.map((c) => (
          <button
            key={c.ref}
            onClick={() => go({ page: "case", ref: c.ref, tab: route.page === "case" ? route.tab : "stages" })}
            className={`w-full text-left rounded-lg px-2.5 py-2 mb-0.5 flex items-start gap-2.5 transition ${
              activeRef === c.ref ? "bg-indigo-50 ring-1 ring-indigo-200" : "hover:bg-slate-50"
            }`}
          >
            <div className="mt-1.5">
              <ProfileDot status={c.profile?.status} />
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-1.5">
                <span className="font-medium text-sm truncate">{c.name || "Unknown"}</span>
                {busyRefs.has(c.ref) && <Spinner className="h-3 w-3 text-indigo-500" />}
              </div>
              <div className="text-xs text-slate-500 flex items-center gap-1.5 mt-0.5">
                <span className="font-mono">{c.ref}</span>
                <span>·</span>
                <span>{c.calls} call{c.calls === 1 ? "" : "s"}</span>
                {c.has_cat && (
                  <span title="CAT data fetched" className="flex items-center gap-0.5 text-sky-600">
                    · <FileText className="h-3 w-3" /> CAT
                  </span>
                )}
                {c.has_docs && (
                  <span title={`${c.doc_files} documents downloaded`} className="flex items-center gap-0.5 text-sky-600">
                    · <FolderOpen className="h-3 w-3" /> {c.doc_files}
                  </span>
                )}
                {c.profile?.status === "ready" && (
                  <span title="Profile ready" className="text-emerald-600">
                    <Sparkles className="h-3 w-3" />
                  </span>
                )}
              </div>
              <div className="text-[11px] text-slate-400">{fmtDay(c.last_call)}</div>
            </div>
          </button>
        ))}
        {!list.length && <div className="text-sm text-slate-400 px-2 py-6 text-center">No cases yet. Use “Fetch cases”.</div>}
      </div>
    </aside>
  );
}
