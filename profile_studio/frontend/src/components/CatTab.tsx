import { FileText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, fmtDate, Json } from "../api";
import { Badge, Empty, ErrorBox, Spinner, StatusBadge } from "./ui";

function cell(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function DataTable({ rows }: { rows: Json[] }) {
  const cols = useMemo(() => {
    const c: string[] = [];
    rows.forEach((r) => Object.keys(r || {}).forEach((k) => !c.includes(k) && c.push(k)));
    return c.filter((k) => !/(_id|document|line_id)$/.test(k)).slice(0, 10);
  }, [rows]);
  if (!rows.length) return <div className="text-sm text-slate-400 px-4 py-3">No rows</div>;
  return (
    <div className="overflow-auto scroll-thin max-h-[420px]">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-slate-50 text-left uppercase text-slate-500">
          <tr>{cols.map((c) => <th key={c} className="p-2 whitespace-nowrap">{c.replace(/_/g, " ")}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-slate-100">
              {cols.map((c) => (
                <td key={c} className="p-2 align-top max-w-[320px]">
                  {c === "status" ? <StatusBadge status={r[c]} /> : <span className="line-clamp-3">{cell(r[c])}</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CatTab({ refId, hasCat }: { refId: string; hasCat: boolean }) {
  const [data, setData] = useState<Json>(null);
  const [error, setError] = useState<string | null>(null);
  const [table, setTable] = useState(0);

  useEffect(() => {
    if (!hasCat) return;
    api.cat(refId).then(setData).catch((e) => setError(e.message));
  }, [refId, hasCat]);

  const sections = useMemo(() => {
    const m: Record<string, Json[]> = {};
    (data?.fields || []).forEach((f: Json) => {
      (m[f.section] ||= []).push(f);
    });
    return m;
  }, [data]);

  if (!hasCat) return <div className="card"><Empty icon={<FileText className="h-10 w-10" />} title="No CAT data for this case">Fetch the case with “Include CAT tool data” ticked.</Empty></div>;
  if (error) return <ErrorBox error={error} />;
  if (!data) return <div className="p-8 grid place-items-center"><Spinner /></div>;
  const tables: Json[] = data.tables || [];
  const docs: Json[] = data.documents || [];
  const meta = data._meta || {};

  return (
    <div className="space-y-5">
      <div className="card p-4 flex flex-wrap gap-6 text-sm">
        <div><div className="label">Status</div><StatusBadge status={meta.status} /> {meta.frozen && <Badge tone="blue">frozen</Badge>}</div>
        <div><div className="label">Generated</div>{fmtDate(meta.generated_at)}</div>
        <div><div className="label">Schema</div>{meta.schema_version}</div>
        <div><div className="label">Documents</div>{docs.length}</div>
      </div>

      <div className="card overflow-hidden">
        <div className="flex gap-1 px-3 pt-2 border-b border-slate-200 overflow-x-auto">
          {tables.map((t, i) => (
            <button key={i} className={`tab ${i === table ? "tab-active" : ""}`} onClick={() => setTable(i)}>
              {t.label} <span className="text-xs text-slate-400">({(t.rows || []).length})</span>
            </button>
          ))}
        </div>
        {tables[table] && (
          <>
            <div className="px-4 py-2 text-xs text-slate-500">
              CRM total: {tables[table].total_crm ?? "—"} · Verified total: {tables[table].total_verified ?? "—"}
            </div>
            <DataTable rows={tables[table].rows || []} />
          </>
        )}
      </div>

      <div className="grid lg:grid-cols-2 gap-5">
        {Object.entries(sections).map(([sec, fs]) => (
          <div key={sec} className="card p-4">
            <div className="font-semibold mb-2 capitalize">{sec.replace(/_/g, " ")}</div>
            <div className="space-y-1">
              {fs.map((f) => (
                <div key={f.field_id} className="grid grid-cols-5 gap-2 text-sm">
                  <span className="col-span-2 text-slate-500">{f.label}</span>
                  <span className="col-span-3 break-words">{cell(f.value)}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="card overflow-hidden">
        <div className="px-4 py-3 font-semibold">Documents ({docs.length})</div>
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs uppercase text-slate-500">
            <tr><th className="p-2 pl-4">File</th><th className="p-2">Type</th><th className="p-2">Belongs to</th><th className="p-2">Uploaded</th><th className="p-2">Processing</th></tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.document_id} className="border-t border-slate-100">
                <td className="p-2 pl-4 max-w-[360px] truncate" title={d.file_name}>{d.file_name}</td>
                <td className="p-2"><Badge tone="blue">{d.document_type}</Badge></td>
                <td className="p-2">{d.belongs_to}</td>
                <td className="p-2">{fmtDate(d.uploaded_at)}</td>
                <td className="p-2"><StatusBadge status={d.processing_status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="px-4 py-2 text-xs text-slate-500 border-t border-slate-100">
          The CAT export lists documents but doesn't give download links yet – document files can plug into this pipeline once a download endpoint exists.
        </div>
      </div>
    </div>
  );
}
