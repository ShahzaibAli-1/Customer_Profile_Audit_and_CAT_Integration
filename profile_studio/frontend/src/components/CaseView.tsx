import { Bot, Download, FileText, FolderOpen, LayoutGrid, ListChecks, MessagesSquare, RefreshCw, Sparkles, UserRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, fmtDate, fmtDay, fmtDuration, Json } from "../api";
import { go } from "../App";
import AuditTab from "./AuditTab";
import CatTab from "./CatTab";
import DocumentsTab from "./DocumentsTab";
import { JobLog } from "./JobsDrawer";
import ProfileTab from "./ProfileTab";
import StagesTab from "./StagesTab";
import TranscriptTab from "./TranscriptTab";
import { Badge, Empty, ErrorBox, KV, Progress, Spinner, StatusBadge, statusTone } from "./ui";

const TABS: [string, string, JSX.Element][] = [
  ["overview", "Overview", <LayoutGrid key="o" className="h-4 w-4" />],
  ["stages", "Stages (1–32)", <ListChecks key="s" className="h-4 w-4" />],
  ["profile", "Customer profile", <UserRound key="p" className="h-4 w-4" />],
  ["transcript", "Transcript", <MessagesSquare key="t" className="h-4 w-4" />],
  ["audit", "Audit", <Bot key="a" className="h-4 w-4" />],
  ["documents", "Documents", <FolderOpen key="d" className="h-4 w-4" />],
  ["cat", "CAT data", <FileText key="c" className="h-4 w-4" />],
];

export default function CaseView({ refId, tab, refreshKey, jobs, onJobStarted, health }: {
  refId: string; tab: string; refreshKey: number; jobs: Json[]; onJobStarted: (j: Json) => void; health: Json;
}) {
  const [detail, setDetail] = useState<Json>(null);
  const [profile, setProfile] = useState<Json>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [transcriptFocus, setTranscriptFocus] = useState<{ callId: number; stage: number } | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, p] = await Promise.all([api.caseDetail(refId).catch((e) => ({ __error: e.message })), api.profile(refId)]);
      if (d.__error) {
        setDetail(null);
        setError(d.__error);
      } else {
        setDetail(d);
        setError(null);
      }
      setProfile(p);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [refId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const activeJobs = useMemo(
    () => jobs.filter((j) => (j.status === "running" || j.status === "queued") && (j.refs || []).includes(refId)),
    [jobs, refId],
  );
  // While something runs for this case, refresh now and then so new data shows up.
  useEffect(() => {
    if (!activeJobs.length) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [activeJobs.length, load]);

  const build = async (quality?: string, personaOnly = false) => {
    setBusy(true);
    try {
      onJobStarted(await api.profileJob({ refs: [refId], quality, persona_only: personaOnly }));
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const refetch = async () => {
    setBusy(true);
    try {
      onJobStarted(await api.fetchJob({ refs: [refId], include_cat: true, include_documents: true, build_profile: false, refetch: false }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const fetchDocs = async () => {
    setBusy(true);
    try {
      onJobStarted(await api.fetchJob({ refs: [refId], include_audit: false, include_cat: false, include_documents: true, build_profile: false }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const setTab = (t: string) => go({ page: "case", ref: refId, tab: t });
  const pstatus = profile?.meta?.status || "none";
  const name = detail?.name || profile?.persona?.label || "Case " + refId;

  if (!detail && !profile?.persona && error) {
    return (
      <div className="p-6">
        {activeJobs.length > 0 ? (
          <div className="card p-5">
            <div className="font-semibold mb-2">Fetching case {refId}…</div>
            {activeJobs.map((j) => <div key={j.id}><Progress done={j.progress?.done || 0} total={j.progress?.total || 0} /><JobLog jobId={j.id} /></div>)}
          </div>
        ) : (
          <Empty title={`Case ${refId} isn't fetched yet`}>{error}</Empty>
        )}
      </div>
    );
  }
  if (!detail && !profile) return <div className="p-10 grid place-items-center"><Spinner className="h-6 w-6" /></div>;

  return (
    <div className="flex flex-col min-h-full">
      <div className="bg-white border-b border-slate-200 px-6 pt-5">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold truncate">{name}</h1>
              <span className="font-mono text-sm text-slate-500 bg-slate-100 rounded px-1.5 py-0.5">{refId}</span>
            </div>
            <div className="flex flex-wrap items-center gap-2 mt-1.5 text-sm text-slate-500">
              <span>{detail?.calls?.length || 0} call(s)</span>
              {detail?.calls?.[0] && <span>· advisor {detail.calls[detail.calls.length - 1].advisor}</span>}
              {detail?.calls?.[0] && <span>· {fmtDay(detail.calls[detail.calls.length - 1].call_date)}</span>}
              {detail?.cat ? <Badge tone="blue">CAT data</Badge> : <Badge>No CAT data</Badge>}
              {detail?.documents ? <Badge tone="blue">{detail.documents.files} documents downloaded</Badge> : <Badge>No documents</Badge>}
              <StatusBadge status={`Profile: ${pstatus}`} />
              {profile?.meta?.quality && <Badge tone="violet" title={profile.meta.quality_reason}>{profile.meta.quality} quality</Badge>}
            </div>
          </div>
          <div className="ml-auto flex flex-wrap gap-2">
            <button className="btn-secondary" onClick={refetch} disabled={busy || activeJobs.length > 0}>
              <RefreshCw className="h-4 w-4" /> Fetch calls & documents
            </button>
            {pstatus === "ready" && profile?.stages && (
              <button className="btn-secondary" onClick={() => build(undefined, true)} disabled={busy || activeJobs.length > 0}
                title="Keep the stage analysis and regenerate only the customer profile (about 2 minutes)">
                <Sparkles className="h-4 w-4" /> Rebuild profile only
              </button>
            )}
            <button className="btn-primary" onClick={() => build()} disabled={busy || activeJobs.length > 0 || !detail?.calls?.length}
              title="Map stages, analyse every stage and build the profile (about 5-10 minutes)">
              {activeJobs.some((j) => j.kind === "profile") ? <Spinner /> : <Sparkles className="h-4 w-4" />}
              {pstatus === "ready" ? "Rebuild everything" : "Build profile"}
            </button>
          </div>
        </div>
        {activeJobs.map((j) => (
          <div key={j.id} className="mt-3 rounded-lg bg-indigo-50 border border-indigo-100 px-3 py-2">
            <div className="flex justify-between text-sm text-indigo-800 mb-1">
              <span className="font-medium">{j.title}: {j.progress?.message}</span>
              <span>{j.progress?.total ? `${j.progress.done}/${j.progress.total}` : ""}</span>
            </div>
            <Progress done={j.progress?.done || 0} total={j.progress?.total || 0} />
          </div>
        ))}
        {error && detail && (
          <div className="mt-3 flex items-start gap-2">
            <div className="flex-1"><ErrorBox error={error} /></div>
            <button className="btn-ghost text-xs" onClick={() => setError(null)}>Dismiss</button>
          </div>
        )}
        {pstatus === "failed" || pstatus === "interrupted" ? (
          <div className="mt-3"><ErrorBox error={`Profile build ${pstatus}: ${profile?.meta?.error}`} /></div>
        ) : null}
        <div className="flex gap-1 mt-4 overflow-x-auto">
          {TABS.map(([k, label, icon]) => (
            <button key={k} onClick={() => setTab(k)} className={`tab flex items-center gap-1.5 ${tab === k ? "tab-active" : ""}`}>
              {icon} {label}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 p-6">
        {tab === "overview" && <Overview detail={detail} profile={profile} goTab={setTab} />}
        {tab === "stages" && (
          <StagesTab profile={profile} calls={detail?.calls || []} onBuild={() => build()} building={activeJobs.length > 0}
            onShowTranscript={(callId, stage) => { setTranscriptFocus({ callId, stage }); setTab("transcript"); }} />
        )}
        {tab === "profile" && <ProfileTab refId={refId} profile={profile} onSaved={load} onBuild={() => build()} building={activeJobs.length > 0} />}
        {tab === "transcript" && <TranscriptTab refId={refId} calls={detail?.calls || []} focus={transcriptFocus} />}
        {tab === "audit" && <AuditTab refId={refId} calls={detail?.calls || []} />}
        {tab === "documents" && (
          <DocumentsTab refId={refId} refreshKey={refreshKey} onFetch={fetchDocs} busy={busy || activeJobs.length > 0}
            configured={Boolean(health?.cat_docs_configured)} />
        )}
        {tab === "cat" && <CatTab refId={refId} hasCat={Boolean(detail?.cat)} />}
      </div>
    </div>
  );
}

function Overview({ detail, profile, goTab }: { detail: Json; profile: Json; goTab: (t: string) => void }) {
  const stages: Json[] = profile?.stages || [];
  const calls: Json[] = detail?.calls || [];
  const usage = profile?.meta?.usage || {};
  return (
    <div className="grid xl:grid-cols-3 gap-5">
      <div className="xl:col-span-2 space-y-5">
        <div className="card">
          <div className="px-4 py-3 border-b border-slate-200 font-semibold">Calls (Audit tool)</div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs uppercase text-slate-500 bg-slate-50">
              <tr><th className="p-2 pl-4">Call</th><th className="p-2">Date</th><th className="p-2">Advisor</th><th className="p-2">Length</th>
                <th className="p-2">Solution</th><th className="p-2">Audit</th><th className="p-2">Review</th></tr>
            </thead>
            <tbody>
              {calls.map((c) => (
                <tr key={c.call_id} className="border-t border-slate-100">
                  <td className="p-2 pl-4 font-mono">{c.call_id}{c.case_id && c.case_id.includes("-") ? <span className="text-slate-400"> ({c.case_id})</span> : null}</td>
                  <td className="p-2">{fmtDate(c.call_date)}</td>
                  <td className="p-2">{c.advisor}</td>
                  <td className="p-2">{fmtDuration(c.call_duration)} <span className="text-xs text-slate-400">· {c.transcript_lines} lines</span></td>
                  <td className="p-2">{c.chosen_solution || "—"}</td>
                  <td className="p-2">
                    {c.totals ? <Badge tone={statusTone(c.totals.outcome)}>{c.totals.score}/{c.totals.max} · {c.totals.pct}% {c.totals.outcome}</Badge> : "—"}
                  </td>
                  <td className="p-2"><StatusBadge status={c.review_status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!calls.length && <Empty title="No calls fetched" />}
        </div>

        <div className="card p-4">
          <div className="flex items-center justify-between mb-3">
            <div className="font-semibold">Stage coverage</div>
            <button className="btn-ghost text-xs" onClick={() => goTab("stages")}>Open stages →</button>
          </div>
          {stages.length ? (
            <div className="grid grid-cols-4 sm:grid-cols-8 gap-1.5">
              {stages.map((s) => {
                const covered = s.calls.some((c: Json) => c.covered);
                const audit = s.calls.find((c: Json) => c.audit_status)?.audit_status;
                const tone = statusTone(audit);
                const color = !covered ? "bg-slate-100 text-slate-400" : tone === "green" ? "bg-emerald-100 text-emerald-800" : tone === "amber" ? "bg-amber-100 text-amber-800" : tone === "red" ? "bg-rose-100 text-rose-800" : "bg-sky-100 text-sky-800";
                return (
                  <div key={s.n} title={`${s.n}. ${s.name}\n${covered ? "Found in transcript" : "Not found in transcript"}\nAudit: ${audit || "—"}`}
                    className={`rounded-md px-1.5 py-1 text-[11px] leading-tight ${color}`}>
                    <div className="font-semibold">{s.n}</div>
                    <div className="truncate">{s.name.replace("Solution: ", "")}</div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-sm text-slate-500">Build the profile to map the 32 stages onto the transcript.</div>
          )}
        </div>
      </div>

      <div className="space-y-5">
        <div className="card p-4 space-y-3">
          <div className="font-semibold">Customer profile</div>
          {profile?.persona ? (
            <>
              <KV k="Persona" v={profile.persona.label} />
              <KV k="Situation" v={profile.persona.segment} />
              <KV k="Risk" v={profile.persona.risk} />
              <KV k="Hardest stage" v={profile.persona.stage} />
              <KV k="Built" v={`${fmtDate(profile.meta?.updated_at)} · ${profile.meta?.anonymised ? "anonymised" : "real identity"}`} />
              <KV k="Models" v={Object.entries(profile.meta?.models || {}).map(([k, v]) => `${k}: ${v}`).join("\n")} />
              <KV k="Tokens" v={Object.entries(usage).map(([m, u]: [string, Json]) => `${m}: ${u.calls} calls, ${(u.input_tokens / 1000).toFixed(0)}k in / ${(u.output_tokens / 1000).toFixed(0)}k out`).join("\n") || "—"} />
              <button className="btn-secondary w-full" onClick={() => goTab("profile")}>Open profile</button>
            </>
          ) : (
            <div className="text-sm text-slate-500">Not built yet.</div>
          )}
        </div>
        <div className="card p-4 space-y-3">
          <div className="font-semibold">CAT tool</div>
          {detail?.cat ? (
            <>
              <KV k="Status" v={`${detail.cat.status}${detail.cat.frozen ? " (frozen)" : ""}`} />
              <KV k="Documents" v={`${detail.cat.documents.length} (${detail.cat.documents.filter((d: Json) => d.processing_status === "failed").length} failed processing)`} />
              {detail.cat.tables.map((t: Json) => (
                <KV key={t.label} k={t.label} v={`${t.rows} rows · CRM ${t.total_crm ?? "—"} · verified ${t.total_verified ?? "—"}`} />
              ))}
              <button className="btn-secondary w-full" onClick={() => goTab("cat")}>Open CAT data</button>
            </>
          ) : (
            <div className="text-sm text-slate-500">No CAT data. Use “Fetch calls & documents”.</div>
          )}
        </div>
        <div className="card p-4 space-y-3">
          <div className="font-semibold">Documents (CAT document vault)</div>
          {detail?.documents ? (
            <>
              <KV k="Files" v={`${detail.documents.files} downloaded${detail.documents.failed_processing ? ` · ${detail.documents.failed_processing} failed processing in CAT` : ""}`} />
              <KV k="CAT case" v={`${detail.documents.cat_status} · ${detail.documents.phase} · id ${detail.documents.application_id}`} />
              <KV k="Downloaded" v={fmtDate(detail.documents.fetched_at)} />
              <button className="btn-secondary w-full" onClick={() => goTab("documents")}>Open documents</button>
            </>
          ) : (
            <div className="text-sm text-slate-500">Not downloaded yet.</div>
          )}
        </div>
        {profile?.persona && (
          <a className="btn-secondary w-full" href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(profile.persona, null, 2))}`}
            download={`${profile.persona.id}.json`}>
            <Download className="h-4 w-4" /> Download persona JSON
          </a>
        )}
      </div>
    </div>
  );
}
