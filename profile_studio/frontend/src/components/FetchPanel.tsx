import { CheckCircle2, Download, Hash, ListChecks, RefreshCw, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, daysAgo, fmtDate, fmtDuration, isoDay, Json, parseRefs } from "../api";
import { go } from "../App";
import { Badge, Empty, ErrorBox, Spinner, StatusBadge } from "./ui";

type Opts = { include_cat: boolean; include_documents: boolean; build_profile: boolean; refetch: boolean; quality: string; anonymise: boolean };

function Options({ opts, setOpts, openai }: { opts: Opts; setOpts: (o: Opts) => void; openai: boolean }) {
  const cb = (k: keyof Opts, label: string, hint: string, disabled = false) => (
    <label className={`flex items-start gap-2 text-sm ${disabled ? "opacity-50" : ""}`}>
      <input type="checkbox" className="mt-0.5 accent-indigo-600" disabled={disabled} checked={Boolean(opts[k])}
        onChange={(e) => setOpts({ ...opts, [k]: e.target.checked })} />
      <span>
        <span className="font-medium">{label}</span>
        <span className="block text-xs text-slate-500">{hint}</span>
      </span>
    </label>
  );
  return (
    <div className="grid sm:grid-cols-2 gap-3">
      {cb("include_cat", "Include CAT case data", "Case data (fields, creditors, income, expenditure) from the CAT case export")}
      {cb("include_documents", "Download CAT documents", "Signs in to CAT and uses the Document vault's “Download all”")}
      {cb("build_profile", "Build customer profile", openai ? "Stage analysis + persona with OpenAI after fetching" : "Needs OPENAI_API_KEY in .env", !openai)}
      {cb("anonymise", "Anonymise persona identity", "Fictional name, DOB, address and email; real finances and behaviour")}
      {cb("refetch", "Re-fetch already fetched", "Download calls and CAT data again")}
      <div>
        <label className="label">Profile quality</label>
        <select className="input" value={opts.quality} onChange={(e) => setOpts({ ...opts, quality: e.target.value })}>
          <option value="auto">Auto – stronger model for complex / vulnerable cases</option>
          <option value="standard">Standard</option>
          <option value="high">High</option>
        </select>
      </div>
    </div>
  );
}

export default function FetchPanel({ health, onJobStarted }: { health: Json; onJobStarted: (j: Json) => void }) {
  const [mode, setMode] = useState<"ids" | "browse">("ids");
  const [opts, setOpts] = useState<Opts>({ include_cat: true, include_documents: true, build_profile: true, refetch: false, quality: "auto", anonymise: true });
  const [settings, setSettings] = useState<Json>(null);
  const [text, setText] = useState("");
  const [start, setStart] = useState(daysAgo(60));
  const [end, setEnd] = useState(isoDay(new Date()));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // browse state
  const [bStart, setBStart] = useState(daysAgo(6));
  const [bEnd, setBEnd] = useState(isoDay(new Date()));
  const [bq, setBq] = useState("");
  const [browse, setBrowse] = useState<Json>(null);
  const [bLoading, setBLoading] = useState(false);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [hideFetched, setHideFetched] = useState(false);

  useEffect(() => {
    api.settings().then((s) => {
      setSettings(s);
      setStart(daysAgo(Number(s.lookup_days) || 60));
      setOpts((o) => ({ ...o, anonymise: Boolean(s.anonymise), quality: s.quality || "auto" }));
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!health.openai_key && health.openai_key !== undefined) setOpts((o) => ({ ...o, build_profile: false }));
  }, [health.openai_key]);

  const refs = parseRefs(text);

  const submit = async (list: string[], s: string, e: string) => {
    setError(null);
    setBusy(true);
    try {
      const job = await api.fetchJob({ refs: list, start: s, end: e, ...opts });
      onJobStarted(job);
      if (list.length === 1) go({ page: "case", ref: list[0].replace(/-[A-Za-z]+$/, ""), tab: "overview" });
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const loadBrowse = async () => {
    setBLoading(true);
    setError(null);
    try {
      setBrowse(await api.browse(bStart, bEnd, bq));
      setSel(new Set());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBLoading(false);
    }
  };

  const rows: Json[] = useMemo(() => {
    const cs: Json[] = browse?.customers || [];
    return hideFetched ? cs.filter((c) => !c.all_fetched) : cs;
  }, [browse, hideFetched]);

  const toggle = (ref: string) => {
    const s = new Set(sel);
    s.has(ref) ? s.delete(ref) : s.add(ref);
    setSel(s);
  };

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="mb-5">
        <h1 className="text-2xl font-semibold">Fetch cases</h1>
        <p className="text-sm text-slate-500 mt-1">
          Pull transcripts and audits from the Audit tool and case data from the CAT tool, then build a stage-by-stage customer profile
          for the AI Call Agent.
        </p>
      </div>

      <div className="flex gap-1 border-b border-slate-200 mb-5">
        <button className={`tab ${mode === "ids" ? "tab-active" : ""}`} onClick={() => setMode("ids")}>
          <Hash className="h-4 w-4 inline mr-1" /> By case number
        </button>
        <button className={`tab ${mode === "browse" ? "tab-active" : ""}`} onClick={() => setMode("browse")}>
          <ListChecks className="h-4 w-4 inline mr-1" /> Browse Audit tool
        </button>
      </div>

      <ErrorBox error={error} />

      {mode === "ids" ? (
        <div className="card p-5 mt-3 space-y-5">
          <div>
            <label className="label">Case numbers</label>
            <textarea className="input font-mono h-24" placeholder="e.g. 435864 435898 436216  (spaces, commas or new lines)"
              value={text} onChange={(e) => setText(e.target.value)} />
            <div className="mt-1 text-xs text-slate-500">
              {refs.length ? <>{refs.length} case(s): {refs.join(", ")}</> : "The Audit tool can only search by date, so calls are looked up within the date range below."}
            </div>
          </div>
          <div className="grid sm:grid-cols-2 gap-3 max-w-md">
            <div>
              <label className="label">Search calls from</label>
              <input type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} />
            </div>
            <div>
              <label className="label">To</label>
              <input type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} />
            </div>
          </div>
          <Options opts={opts} setOpts={setOpts} openai={Boolean(health.openai_key)} />
          <div className="flex items-center gap-3">
            <button className="btn-primary" disabled={!refs.length || busy || !health.audit_key} onClick={() => submit(refs, start, end)}>
              {busy ? <Spinner /> : <Download className="h-4 w-4" />} Fetch {refs.length || ""} case{refs.length === 1 ? "" : "s"}
            </button>
            {!health.audit_key && health.audit_key !== undefined && <span className="text-sm text-rose-600">AUDIT_API_KEY missing in .env</span>}
          </div>
        </div>
      ) : (
        <div className="space-y-3 mt-3">
          <div className="card p-4 flex flex-wrap items-end gap-3">
            <div>
              <label className="label">From</label>
              <input type="date" className="input" value={bStart} onChange={(e) => setBStart(e.target.value)} />
            </div>
            <div>
              <label className="label">To</label>
              <input type="date" className="input" value={bEnd} onChange={(e) => setBEnd(e.target.value)} />
            </div>
            <div className="flex-1 min-w-[180px]">
              <label className="label">Filter</label>
              <div className="relative">
                <Search className="h-4 w-4 absolute left-2.5 top-2 text-slate-400" />
                <input className="input pl-8" placeholder="Case, customer or advisor" value={bq} onChange={(e) => setBq(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && loadBrowse()} />
              </div>
            </div>
            <button className="btn-primary" onClick={loadBrowse} disabled={bLoading}>
              {bLoading ? <Spinner /> : <RefreshCw className="h-4 w-4" />} Load calls
            </button>
          </div>

          {browse && (
            <div className="card overflow-hidden">
              <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 border-b border-slate-200 bg-slate-50">
                <span className="text-sm text-slate-600">{rows.length} customers · {browse.start} → {browse.end}</span>
                <label className="text-sm flex items-center gap-1.5 text-slate-600">
                  <input type="checkbox" className="accent-indigo-600" checked={hideFetched} onChange={(e) => setHideFetched(e.target.checked)} />
                  Hide already fetched
                </label>
                <div className="ml-auto flex items-center gap-2">
                  <button className="btn-ghost" onClick={() => setSel(new Set(rows.map((r) => r.ref)))}>Select all</button>
                  <button className="btn-ghost" onClick={() => setSel(new Set())}>Clear</button>
                </div>
              </div>
              <div className="max-h-[480px] overflow-auto scroll-thin">
                <table className="w-full text-sm">
                  <thead className="sticky top-0 bg-white shadow-sm text-left text-xs uppercase text-slate-500">
                    <tr>
                      <th className="p-2 w-8"></th>
                      <th className="p-2">Case</th>
                      <th className="p-2">Customer</th>
                      <th className="p-2">Advisor</th>
                      <th className="p-2">Calls</th>
                      <th className="p-2">Last call</th>
                      <th className="p-2">Solution</th>
                      <th className="p-2">Local</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => {
                      const last = r.calls[r.calls.length - 1] || {};
                      return (
                        <tr key={r.ref} className={`border-t border-slate-100 hover:bg-slate-50 cursor-pointer ${sel.has(r.ref) ? "bg-indigo-50/60" : ""}`}
                          onClick={() => toggle(r.ref)}>
                          <td className="p-2"><input type="checkbox" className="accent-indigo-600" readOnly checked={sel.has(r.ref)} /></td>
                          <td className="p-2 font-mono">{r.ref}</td>
                          <td className="p-2">{r.name}</td>
                          <td className="p-2 text-slate-600">{r.advisor}</td>
                          <td className="p-2">
                            {r.calls.length}
                            <span className="text-xs text-slate-400 ml-1">({r.calls.map((c: Json) => fmtDuration(c.call_duration)).join(", ")})</span>
                          </td>
                          <td className="p-2 text-slate-600">{fmtDate(r.last_call)}</td>
                          <td className="p-2">{last.chosen_solution || "—"}</td>
                          <td className="p-2">
                            <div className="flex gap-1 flex-wrap">
                              {r.all_fetched ? <Badge tone="green"><CheckCircle2 className="h-3 w-3 mr-0.5" />Calls</Badge>
                                : r.calls.some((c: Json) => c.fetched) ? <Badge tone="amber">Some calls</Badge> : null}
                              {r.has_cat && <Badge tone="blue">CAT</Badge>}
                              {r.profile?.status && r.profile.status !== "none" && <StatusBadge status={`Profile ${r.profile.status}`} />}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {!rows.length && <Empty title="No calls in this range" />}
              </div>
              <div className="p-4 border-t border-slate-200 space-y-4 bg-slate-50/50">
                <Options opts={opts} setOpts={setOpts} openai={Boolean(health.openai_key)} />
                <button className="btn-primary" disabled={!sel.size || busy} onClick={() => submit([...sel], browse.start, browse.end)}>
                  {busy ? <Spinner /> : <Download className="h-4 w-4" />} Fetch selected ({sel.size})
                </button>
              </div>
            </div>
          )}
          {!browse && !bLoading && (
            <div className="card">
              <Empty icon={<ListChecks className="h-10 w-10" />} title="Browse calls in the Audit tool">
                Pick a date range and load. Listings are cached locally, so loading the same days again doesn't use the daily allowance
                (today and yesterday are always refreshed).
              </Empty>
            </div>
          )}
        </div>
      )}
      {settings && <p className="text-xs text-slate-400 mt-4">Default search window: last {settings.lookup_days} days (change in Settings).</p>}
    </div>
  );
}
