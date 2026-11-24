import { Download, ExternalLink, FileImage, FileText, FolderOpen, RefreshCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, fmtDate, Json } from "../api";
import { Badge, Empty, ErrorBox, Spinner, StatusBadge } from "./ui";

function size(n?: number) {
  if (!n) return "—";
  return n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} KB`;
}

function isImage(name: string) {
  return /\.(png|jpe?g|gif|webp|bmp)$/i.test(name);
}
function isPdf(name: string) {
  return /\.pdf$/i.test(name);
}

export default function DocumentsTab({ refId, refreshKey, onFetch, busy, configured }: {
  refId: string; refreshKey: number; onFetch: () => void; busy: boolean; configured: boolean;
}) {
  const [data, setData] = useState<Json>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<Json>(null);

  useEffect(() => {
    api.documents(refId).then((d) => {
      setData(d);
      setSel((s: Json) => s && d.files.find((f: Json) => f.path === s.path) ? s : d.files[0] || null);
    }).catch((e) => setError(e.message));
  }, [refId, refreshKey]);

  const groups = useMemo(() => {
    const m: Record<string, Json[]> = {};
    (data?.files || []).forEach((f: Json) => (m[f.folder || "Other"] ||= []).push(f));
    return Object.entries(m).sort(([a], [b]) => a.localeCompare(b));
  }, [data]);

  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="p-8 grid place-items-center"><Spinner /></div>;

  if (!data.downloaded) {
    return (
      <div className="card">
        <Empty icon={<FolderOpen className="h-10 w-10" />} title="No documents downloaded yet">
          Signs in to the CAT tool, finds this case and uses the Document vault's “Download all”.
          <div className="mt-4">
            <button className="btn-primary" onClick={onFetch} disabled={busy || !configured}>
              {busy ? <Spinner /> : <Download className="h-4 w-4" />} Download documents
            </button>
            {!configured && <div className="mt-2 text-xs text-rose-600">Set CAT_URL, CAT_USERNAME and CAT_PASSWORD in .env</div>}
          </div>
        </Empty>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="card p-3 flex flex-wrap items-center gap-3 text-sm">
        <span className="font-medium">{data.files.length} files</span>
        <span className="text-slate-500">downloaded {fmtDate(data.fetched_at)}</span>
        <span className="text-xs text-slate-400 font-mono truncate max-w-[420px]" title={data.folder}>{data.folder}</span>
        <button className="btn-secondary ml-auto" onClick={onFetch} disabled={busy}>
          {busy ? <Spinner /> : <RefreshCw className="h-4 w-4" />} Check for new documents
        </button>
      </div>
      <div className="flex gap-4 items-start">
        <div className="w-[380px] shrink-0 card overflow-hidden">
          {groups.map(([folder, files]) => (
            <div key={folder}>
              <div className="px-3 py-1.5 bg-slate-50 border-y border-slate-100 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                {folder} <span className="text-slate-400">({files.length})</span>
              </div>
              {files.map((f) => (
                <button key={f.path} onClick={() => setSel(f)}
                  className={`w-full text-left flex items-start gap-2 px-3 py-2 text-sm border-t border-slate-50 ${sel?.path === f.path ? "bg-indigo-50" : "hover:bg-slate-50"}`}>
                  {isImage(f.name) ? <FileImage className="h-4 w-4 mt-0.5 text-sky-500 shrink-0" /> : <FileText className="h-4 w-4 mt-0.5 text-rose-400 shrink-0" />}
                  <span className="min-w-0 flex-1">
                    <span className="block truncate" title={f.name}>{f.name}</span>
                    <span className="flex gap-1.5 mt-0.5 items-center">
                      {f.document_type && <Badge tone="blue">{f.document_type}</Badge>}
                      {f.processing_status && f.processing_status !== "processed" && <StatusBadge status={f.processing_status} />}
                      <span className="text-[11px] text-slate-400">{size(f.size)}</span>
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ))}
        </div>
        <div className="flex-1 min-w-0 card overflow-hidden sticky top-0">
          {sel ? (
            <>
              <div className="flex items-center gap-2 px-4 py-2.5 border-b border-slate-200">
                <span className="font-medium text-sm truncate">{sel.name}</span>
                <span className="text-xs text-slate-400">{fmtDate(sel.upload_date)}</span>
                <a className="btn-ghost ml-auto text-xs" href={api.docUrl(refId, sel.path)} target="_blank" rel="noreferrer">
                  <ExternalLink className="h-3.5 w-3.5" /> Open
                </a>
                <a className="btn-ghost text-xs" href={api.docUrl(refId, sel.path, true)}>
                  <Download className="h-3.5 w-3.5" /> Download
                </a>
              </div>
              <div className="bg-slate-100 h-[calc(100vh-300px)] min-h-[420px] grid place-items-center">
                {isImage(sel.name) ? (
                  <img src={api.docUrl(refId, sel.path)} alt={sel.name} className="max-h-full max-w-full object-contain" />
                ) : isPdf(sel.name) ? (
                  <iframe title={sel.name} src={api.docUrl(refId, sel.path)} className="w-full h-full bg-white" />
                ) : (
                  <Empty title="No preview for this file type">Use Open or Download.</Empty>
                )}
              </div>
            </>
          ) : (
            <Empty title="Pick a document" />
          )}
        </div>
      </div>
    </div>
  );
}
