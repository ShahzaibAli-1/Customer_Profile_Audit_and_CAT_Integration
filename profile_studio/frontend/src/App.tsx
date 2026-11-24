import { Activity, Settings as SettingsIcon, UserRoundSearch } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, Json } from "./api";
import CaseView from "./components/CaseView";
import FetchPanel from "./components/FetchPanel";
import JobsDrawer from "./components/JobsDrawer";
import SettingsModal from "./components/SettingsModal";
import Sidebar from "./components/Sidebar";

export type Route = { page: "fetch" } | { page: "case"; ref: string; tab: string };

function parseHash(): Route {
  const parts = window.location.hash.replace(/^#\/?/, "").split("/");
  if (parts[0] === "case" && parts[1]) return { page: "case", ref: parts[1], tab: parts[2] || "stages" };
  return { page: "fetch" };
}

export function go(route: Route) {
  window.location.hash = route.page === "case" ? `/case/${route.ref}/${route.tab}` : "/fetch";
}

function HealthDot({ ok, label, title }: { ok?: boolean; label: string; title: string }) {
  return (
    <span title={title} className="flex items-center gap-1.5 text-xs text-slate-300">
      <span className={`h-2 w-2 rounded-full ${ok === undefined ? "bg-slate-500" : ok ? "bg-emerald-400" : "bg-rose-400"}`} />
      {label}
    </span>
  );
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash());
  const [cases, setCases] = useState<Json[]>([]);
  const [health, setHealth] = useState<Json>({});
  const [jobs, setJobs] = useState<Json[]>([]);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [focusJob, setFocusJob] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const prevActive = useRef<Set<string>>(new Set());

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const loadCases = useCallback(() => api.cases().then(setCases).catch(() => {}), []);
  const loadHealth = useCallback(() => api.health().then(setHealth).catch(() => setHealth({ down: true })), []);

  useEffect(() => {
    loadCases();
    loadHealth();
  }, [loadCases, loadHealth]);

  // Poll jobs; when one finishes, refresh everything that might have changed.
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const js: Json[] = await api.jobs();
        if (stop) return;
        setJobs(js);
        const active = new Set(js.filter((j) => j.status === "running" || j.status === "queued").map((j) => j.id as string));
        const finished = [...prevActive.current].some((id) => !active.has(id));
        if (finished) {
          loadCases();
          setRefreshKey((k) => k + 1);
        }
        prevActive.current = active;
      } catch {
        /* server down - health dot shows it */
      }
    };
    tick();
    const t = setInterval(tick, 2000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [loadCases]);

  const onJobStarted = (job: Json | Json[]) => {
    const first = Array.isArray(job) ? job[0] : job;
    setFocusJob(first?.id ?? null);
    setJobsOpen(true);
    api.jobs().then(setJobs).catch(() => {});
    loadCases();
  };

  const running = jobs.filter((j) => j.status === "running" || j.status === "queued").length;

  return (
    <div className="h-full flex flex-col">
      <header className="h-14 shrink-0 bg-slate-900 text-white flex items-center px-4 gap-4">
        <button onClick={() => go({ page: "fetch" })} className="flex items-center gap-2 font-semibold">
          <span className="h-8 w-8 rounded-lg bg-indigo-500 grid place-items-center">
            <UserRoundSearch className="h-5 w-5" />
          </span>
          Customer Profile Studio
        </button>
        <div className="hidden md:flex items-center gap-4 ml-4">
          <HealthDot ok={health.down ? false : health.audit_key} label="Audit tool" title="AUDIT_API_KEY in .env" />
          <HealthDot ok={health.down ? false : health.cat_reachable} label="CAT data" title={health.cat_url || "CAT case export API"} />
          <HealthDot ok={health.down ? false : health.cat_docs_configured} label="CAT documents" title="CAT_URL, CAT_USERNAME, CAT_PASSWORD in .env" />
          <HealthDot ok={health.down ? false : health.openai_key} label="OpenAI" title="OPENAI_API_KEY in .env" />
          {health.down && <span className="text-xs text-rose-300">API server not reachable</span>}
        </div>
        <div className="ml-auto flex items-center gap-2">
          <button onClick={() => setJobsOpen(true)} className="btn text-slate-200 hover:bg-slate-800">
            <Activity className="h-4 w-4" /> Jobs
            {running > 0 && <span className="ml-1 rounded-full bg-indigo-500 px-1.5 text-xs">{running}</span>}
          </button>
          <button onClick={() => setSettingsOpen(true)} className="btn text-slate-200 hover:bg-slate-800">
            <SettingsIcon className="h-4 w-4" /> Settings
          </button>
        </div>
      </header>

      <div className="flex-1 flex min-h-0">
        <Sidebar cases={cases} route={route} jobs={jobs} />
        <main className="flex-1 min-w-0 overflow-auto scroll-thin">
          {route.page === "fetch" ? (
            <FetchPanel health={health} onJobStarted={onJobStarted} />
          ) : (
            <CaseView key={route.ref} refId={route.ref} tab={route.tab} refreshKey={refreshKey} jobs={jobs} onJobStarted={onJobStarted} health={health} />
          )}
        </main>
      </div>

      <JobsDrawer open={jobsOpen} onClose={() => setJobsOpen(false)} jobs={jobs} focus={focusJob} setFocus={setFocusJob} />
      {settingsOpen && (
        <SettingsModal
          health={health}
          onClose={() => {
            setSettingsOpen(false);
            loadHealth();
          }}
        />
      )}
    </div>
  );
}
