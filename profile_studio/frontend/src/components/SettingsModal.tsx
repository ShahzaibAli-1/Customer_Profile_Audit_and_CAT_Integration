import { Save, X } from "lucide-react";
import { useEffect, useState } from "react";
import { api, Json } from "../api";
import { ErrorBox, Spinner } from "./ui";

const MODEL_FIELDS: [string, string][] = [
  ["model_segment", "Stage mapping (long context)"],
  ["model_stage", "Stage analysis – standard"],
  ["model_stage_high", "Stage analysis – high quality"],
  ["model_persona", "Customer profile – standard"],
  ["model_persona_high", "Customer profile – high quality"],
  ["customer_model", "Customer agent (live conversation)"],
];

export default function SettingsModal({ onClose, health }: { onClose: () => void; health: Json }) {
  const [s, setS] = useState<Json>(null);
  const [models, setModels] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.settings().then(setS).catch((e) => setError(e.message));
    api.models().then((m) => setModels(m.models || [])).catch(() => {});
  }, []);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.saveSettings(s);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 grid place-items-center p-4" onClick={onClose}>
      <div className="card w-full max-w-2xl max-h-[90vh] overflow-auto scroll-thin" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center px-5 h-14 border-b border-slate-200">
          <h3 className="font-semibold">Settings</h3>
          <button className="ml-auto btn-ghost" onClick={onClose}><X className="h-4 w-4" /></button>
        </div>
        {!s ? (
          <div className="p-8 grid place-items-center"><Spinner /></div>
        ) : (
          <div className="p-5 space-y-5">
            <ErrorBox error={error} />
            <div className="grid grid-cols-3 gap-3 text-sm">
              <div className="card p-3"><div className="label">Audit tool</div>{health.audit_key ? "API key set" : "AUDIT_API_KEY missing"}</div>
              <div className="card p-3"><div className="label">CAT tool</div>{health.cat_reachable ? "Reachable" : "Not reachable (VPN / office network?)"}</div>
              <div className="card p-3"><div className="label">OpenAI</div>{health.openai_key ? `Key set · ${models.length} models` : "OPENAI_API_KEY missing in .env"}</div>
            </div>

            <div>
              <h4 className="font-semibold mb-2">Models</h4>
              <datalist id="models">{models.map((m) => <option key={m} value={m} />)}</datalist>
              <div className="grid sm:grid-cols-2 gap-3">
                {MODEL_FIELDS.map(([k, label]) => (
                  <div key={k}>
                    <label className="label">{label}</label>
                    <input className="input font-mono" list="models" value={s[k] || ""} onChange={(e) => setS({ ...s, [k]: e.target.value })} />
                    {models.length > 0 && s[k] && !models.includes(s[k]) && <div className="text-xs text-amber-600 mt-0.5">Not in your account's model list</div>}
                  </div>
                ))}
                <div>
                  <label className="label">Default quality</label>
                  <select className="input" value={s.quality} onChange={(e) => setS({ ...s, quality: e.target.value })}>
                    <option value="auto">Auto (high for complex / vulnerable cases)</option>
                    <option value="standard">Standard</option>
                    <option value="high">High</option>
                  </select>
                </div>
                <div>
                  <label className="label">Customer agent reasoning effort</label>
                  <select className="input" value={s.customer_effort || "low"} onChange={(e) => setS({ ...s, customer_effort: e.target.value })}>
                    <option value="low">Low – fastest replies</option>
                    <option value="medium">Medium</option>
                    <option value="high">High – slowest</option>
                  </select>
                </div>
                <div>
                  <label className="label">Parallel stage requests</label>
                  <input type="number" min={1} max={10} className="input" value={s.parallel_stage_calls}
                    onChange={(e) => setS({ ...s, parallel_stage_calls: Number(e.target.value) })} />
                </div>
              </div>
            </div>

            <div className="grid sm:grid-cols-2 gap-3">
              <div>
                <label className="label">Default search window (days)</label>
                <input type="number" min={1} max={365} className="input" value={s.lookup_days}
                  onChange={(e) => setS({ ...s, lookup_days: Number(e.target.value) })} />
              </div>
              <label className="flex items-center gap-2 text-sm mt-5">
                <input type="checkbox" className="accent-indigo-600" checked={Boolean(s.anonymise)} onChange={(e) => setS({ ...s, anonymise: e.target.checked })} />
                Anonymise persona identity by default
              </label>
            </div>

            <div>
              <label className="label">AI Call Agent personas file</label>
              <input className="input font-mono text-xs" value={s.agent_personas_path} onChange={(e) => setS({ ...s, agent_personas_path: e.target.value })} />
              <div className="text-xs text-slate-500 mt-1">
                “Export to AI Call Agent” adds the persona here (a timestamped backup is made first).
                {health.agent_personas_exists === false && <span className="text-amber-600"> File not found.</span>}
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button className="btn-secondary" onClick={onClose}>Cancel</button>
              <button className="btn-primary" onClick={save} disabled={saving}>{saving ? <Spinner /> : <Save className="h-4 w-4" />} Save</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
