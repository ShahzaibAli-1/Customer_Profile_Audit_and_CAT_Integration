import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return <Loader2 className={`${className} animate-spin`} />;
}

const TONES: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-1 ring-emerald-200",
  amber: "bg-amber-50 text-amber-700 ring-1 ring-amber-200",
  red: "bg-rose-50 text-rose-700 ring-1 ring-rose-200",
  blue: "bg-sky-50 text-sky-700 ring-1 ring-sky-200",
  indigo: "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200",
  violet: "bg-violet-50 text-violet-700 ring-1 ring-violet-200",
  gray: "bg-slate-100 text-slate-600 ring-1 ring-slate-200",
};

export function Badge({ tone = "gray", children, title }: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`chip ${TONES[tone] ?? TONES.gray}`}>
      {children}
    </span>
  );
}

export function statusTone(status?: string | null): string {
  const s = (status || "").toLowerCase();
  if (!s) return "gray";
  if (["complete", "verified", "pass", "done", "ready", "high", "fetched", "processed"].some((k) => s === k || s.startsWith(k))) return "green";
  if (["partial", "partially", "medium", "variance", "running", "queued", "pending"].some((k) => s.startsWith(k))) return "amber";
  if (["omitted", "fail", "failed", "mismatch", "missing", "low", "interrupted", "not found"].some((k) => s.startsWith(k))) return "red";
  return "gray";
}

export function StatusBadge({ status }: { status?: string | null }) {
  if (!status) return <Badge>—</Badge>;
  return <Badge tone={statusTone(status)}>{status}</Badge>;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-16 px-6 text-slate-500">
      {icon && <div className="mb-3 text-slate-300">{icon}</div>}
      <div className="font-semibold text-slate-700">{title}</div>
      {children && <div className="mt-1 text-sm max-w-md">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error }: { error?: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>;
}

export function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-4">
      <div className="flex items-center justify-between mb-1.5">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{title}</h4>
        {right}
      </div>
      {children}
    </div>
  );
}

export function List({ items, empty = "—" }: { items?: unknown[] | null; empty?: string }) {
  const xs = (items || []).filter((x) => x !== null && x !== undefined && String(x).trim() !== "");
  if (!xs.length) return <div className="text-sm text-slate-400">{empty}</div>;
  return (
    <ul className="space-y-1">
      {xs.map((x, i) => (
        <li key={i} className="text-sm text-slate-700 flex gap-2">
          <span className="text-slate-300 mt-0.5">•</span>
          <span>{typeof x === "string" ? x : JSON.stringify(x)}</span>
        </li>
      ))}
    </ul>
  );
}

export function Progress({ done, total }: { done: number; total: number }) {
  const pct = total ? Math.min(100, Math.round((done / total) * 100)) : 0;
  return (
    <div className="h-1.5 w-full rounded-full bg-slate-200 overflow-hidden">
      <div className="h-full bg-indigo-500 transition-all" style={{ width: `${pct}%` }} />
    </div>
  );
}

export function KV({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{k}</div>
      <div className="text-sm text-slate-800 whitespace-pre-wrap break-words">{v === "" || v === null || v === undefined ? "—" : v}</div>
    </div>
  );
}
