# Customer Profile Studio

Fetches real SFM calls (Audit tool) and case data (CAT tool), breaks every call into the
32 script stages, and builds a customer persona for the AI Call Agent
(`C:\AI_Call_Agent\customer_agent`) that behaves like the real customer did in each stage.

## Start

```powershell
powershell -ExecutionPolicy Bypass -File ".\profile_studio\start.ps1"
```
Open http://localhost:8600. (`-Rebuild` rebuilds the UI after frontend changes.)

Needs in `Test_Customer_Scripts\.env`:
```
AUDIT_API_KEY=...
OPENAI_API_KEY=sk-...
```
The CAT tool (192.168.80.52) is only reachable on the office network / VPN.

## Pipeline (per case)

1. **Fetch** – calls from the Audit tool (transcript, audit report, CRM, case record; no audio)
   into `audit_output/`, and the CAT case export into `cat_output/`. Listings are cached in
   `audit_output/case_index.json`, so searching by case number doesn't use up the daily allowance.
2. **Segment** – for every call, a long-context model marks where each of the 32 stages happens
   (audit timestamps are used as hints; speaker roles come from the audit's labelled evidence).
3. **Analyse** – each stage excerpt is analysed: advisor approach, customer behaviour (tone,
   cooperation, disclosure style, triggers, resistance, speech patterns, verbatim quotes), facts
   disclosed, and how a simulated customer should behave.
4. **Persona** – everything plus the CAT data becomes a `transcript_personas.json` row with a
   stage-by-stage `behavior_guide`. Output in `profiles_output/<case>/`.

"Auto" quality uses the stronger models when the customer mentions several vulnerability
topics, the call is very long, or there are more than two calls. Models are set in Settings.

**Export to AI Call Agent** adds/replaces the persona in the agent's
`data/personas/transcript_personas.json` (a timestamped backup is made first). Restart the
customer agent service to load it.

## Development

```
cd backend;  python -m uvicorn app:app --port 8600 --reload
cd frontend; npm run dev        # http://localhost:5180, proxies /api to 8600
```
