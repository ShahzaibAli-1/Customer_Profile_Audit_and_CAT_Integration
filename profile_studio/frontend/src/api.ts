export type Json = any; // eslint-disable-line @typescript-eslint/no-explicit-any

async function req<T = Json>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data: Json = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail) ? detail.map((d: Json) => d.msg).join("; ") : `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data as T;
}

export const api = {
  health: () => req("GET", "/api/health"),
  stages: () => req("GET", "/api/stages"),
  settings: () => req("GET", "/api/settings"),
  saveSettings: (s: Json) => req("PUT", "/api/settings", s),
  models: () => req("GET", "/api/models"),
  cases: () => req("GET", "/api/cases"),
  caseDetail: (ref: string) => req("GET", `/api/cases/${ref}`),
  cat: (ref: string) => req("GET", `/api/cases/${ref}/cat`),
  documents: (ref: string) => req("GET", `/api/cases/${ref}/documents`),
  docUrl: (ref: string, path: string, download = false) =>
    `/api/cases/${ref}/documents/file?path=${encodeURIComponent(path)}${download ? "&download=true" : ""}`,
  conversation: (ref: string) => req("GET", `/api/cases/${ref}/conversation`),
  transcript: (ref: string, callId: number) => req("GET", `/api/cases/${ref}/calls/${callId}/transcript`),
  audit: (ref: string, callId: number) => req("GET", `/api/cases/${ref}/calls/${callId}/audit`),
  profile: (ref: string) => req("GET", `/api/cases/${ref}/profile`),
  savePersona: (ref: string, persona: Json) => req("PUT", `/api/cases/${ref}/persona`, persona),
  exportAgent: (ref: string) => req("POST", `/api/cases/${ref}/export-agent`),
  browse: (start: string, end: string, q = "") =>
    req("GET", `/api/audit/cases?start=${start}&end=${end}&q=${encodeURIComponent(q)}`),
  fetchJob: (body: Json) => req("POST", "/api/jobs/fetch", body),
  profileJob: (body: Json) => req("POST", "/api/jobs/profile", body),
  jobs: () => req("GET", "/api/jobs"),
  job: (id: string) => req("GET", `/api/jobs/${id}`),
  cancelJob: (id: string) => req("POST", `/api/jobs/${id}/cancel`),
};

export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function daysAgo(n: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - n);
  return isoDay(d);
}

export function fmtDate(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function fmtDay(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function fmtDuration(sec?: string | number | null): string {
  const s = Number(sec);
  if (!s || isNaN(s)) return "—";
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

export function parseRefs(text: string): string[] {
  return Array.from(new Set(text.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean)));
}
