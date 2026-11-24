# Test Customer Scripts

Tools to fetch real SFM customer calls and case data, and turn them into stage-by-stage customer profiles for the AI Call Agent.

| Tool | What it does |
|---|---|
| **Customer Profile Studio** (`profile_studio/`) | Web app: fetch cases, view transcripts / audits / CAT data, build customer profiles, export them to the AI Call Agent |
| `fetch_customers.py` | Command line: fetch calls from the **Audit tool** (transcript, audit report, CRM, case record) |
| `fetch_case_export.py` | Command line: fetch case data from the **CAT tool** |
| `fetch_cat_documents.py` | Command line: sign in to the **CAT tool** and download a case's documents ("Download all") |

---

## 1. One-time setup

### Requirements
- **Python 3.11+** – check with `python --version`
- **Node.js 18+** – check with `node -v` (only needed to build the web app the first time)
- **Office network or VPN** – the CAT tool (`192.168.80.52`) is only reachable from inside

### Install Python packages
Open PowerShell in this folder (`C:\A2A Communication\Test_Customer_Scripts`) and run:
```powershell
pip install -r profile_studio\backend\requirements.txt
```

### Add your keys to `.env`
Create or edit the file `.env` in this folder:
```
AUDIT_API_KEY=your-audit-tool-key
OPENAI_API_KEY=sk-your-openai-key
CAT_URL=http://192.168.80.52:2330
CAT_USERNAME=you@theinsolvencygroup.co.uk
CAT_PASSWORD=your-cat-password
```
- `AUDIT_API_KEY` – needed to fetch calls.
- `OPENAI_API_KEY` – needed to build customer profiles.
- `CAT_URL`, `CAT_USERNAME`, `CAT_PASSWORD` – your CAT tool sign-in, needed to download case documents.
- Optional: `CASE_EXPORT_URL=http://192.168.80.52:2510/api/case-export/` (CAT case data – this is the default).
- Optional: `CAT_API_URL=http://192.168.80.52:2530` (found automatically from `CAT_URL`).

Never commit or share the `.env` file.

---

## 2. Run Customer Profile Studio

```powershell
powershell -ExecutionPolicy Bypass -File .\profile_studio\start.ps1
```
Then open **http://localhost:8600** in your browser.

- The first start builds the web app (takes about a minute).
- Keep the PowerShell window open while you use it; press `Ctrl+C` to stop.
- Different port: `.\profile_studio\start.ps1 -Port 8700`
- After changing frontend code: `.\profile_studio\start.ps1 -Rebuild`

The three dots at the top (Audit tool, CAT tool, OpenAI) should be green. Red means a missing key in `.env` or no access to the CAT tool.

### Using it
1. **Fetch cases**
   - *By case number*: type one or more case numbers (e.g. `435864 435898 436216`), pick the date range to search, and click **Fetch**.
   - *Browse Audit tool*: pick dates, click **Load calls**, tick customers, click **Fetch selected**.
   - Options: include CAT case data, **download CAT documents**, build the profile straight away, anonymise the persona, quality (Auto / Standard / High).
   - One fetch gets everything for the case number: transcripts + audits from the Audit tool, case data from the CAT export, and all documents from the CAT Document vault.
2. **Follow progress** in **Jobs** (top right).
3. **Open a case** from the left sidebar:
   - **Overview** – calls, audit scores, stage coverage, CAT summary
   - **Stages (1–32)** – for each stage: how the advisor ran it and how the customer behaved, with quotes and facts
   - **Customer profile** – the persona; edit it, download the JSON, or **Export to AI Call Agent**
   - **Transcript** – advisor/customer view, filter by stage, search
   - **Audit** – every audit question with status and reasoning
   - **Documents** – the case's documents from the CAT Document vault, grouped by folder, with preview / open / download
   - **CAT data** – fields, tables (creditors, income, expenditure, bank audit) and documents

**Export to AI Call Agent** adds the persona to
`C:\AI_Call_Agent\customer_agent\data\personas\transcript_personas.json` (a backup is made first).
Restart the customer agent service to load it. The path can be changed in **Settings**.

---

## 3. Realistic customer agent (two-way conversation)

Every case with a finished profile becomes a **customer you can talk to**. The customer agent plays the real
customer: their facts, personality, way of speaking, emotions and understanding, stage by stage. On every
turn it is shown how the *real* customer answered the most similar thing the real advisor said, so each
customer sounds like themselves. It also handles document requests with the customer's **real documents**
from the CAT document vault (photo ID, bank statements, UC statement…): it says it's opening the email,
uploads on a later turn, and never sends something it doesn't have.

**In the Studio:** open a case → **Talk to customer**. You are the advisor; quick buttons insert Katie's
usual lines (DPA, photo ID, "send links and wait", income…). Uploads appear as attachments you can open.

**For the AI Call Agent (Katie, agent-to-agent):**
```powershell
powershell -ExecutionPolicy Bypass -File .\start_customer_agent.ps1        # http://localhost:8020
```
Then in `C:\AI_Call_Agent\backend\.env` set:
```
CUSTOMER_AGENT_URL=http://localhost:8020
```
and restart Katie's backend. The customers appear on Katie's A2A page under **Real transcript personas**
(or run `venv\Scripts\python.exe a2a_runner.py --customer <persona id or case number> --full`).
The API is the same one Katie already uses (`/api/customers/{id}`, `/api/call`, `/api/call/{id}`,
`DELETE /api/conversation/{id}`). Each reply also carries:
- `actions` – e.g. `{"type": "upload", "documents": ["bank_statement"], "files": [{"file_name", "url"}]}`,
  `will_upload_later`, `cannot_provide`
- `stage` – the script stage (1–32) the customer thinks the call is in, and `mood`

Other endpoints: `GET /api/customers/{id}/documents`, `GET /api/customers/{id}/documents/{key}` (the file an
upload points to), `GET /api/sessions/{session_id}` (transcript + uploads), `GET /api/customer-agent/health`.
The model is set in Studio **Settings → Customer agent** (default `gpt-6.1-terra`, low effort: replies in ~3–5 s).

---

## 4. Command-line scripts (optional)

Fetch Audit tool calls (no audio by default):
```powershell
python fetch_customers.py --from 2026-11-01 --to 2026-11-23 --case 435864 435898
python fetch_customers.py --from 2026-11-01 --to 2026-11-23 --customers 10
```

Fetch CAT tool case data:
```powershell
python fetch_case_export.py 435864 435898 436216
```

Download CAT documents (signs in with `CAT_USERNAME` / `CAT_PASSWORD`):
```powershell
python fetch_cat_documents.py 436696 435898
```

Useful options: `--refetch` (fetch again), `--limit N` (max calls), `--audio` (also download recordings – large and slow).

---

## 5. Where data is saved

| Folder | Contents |
|---|---|
| `audit_output/customers/<case>_<name>/call_<id>/` | `transcript.txt`, `audit_report.json/.txt`, `crm.json`, `case_record.json`, `details.json` |
| `cat_output/cases/<case>_<name>/` | `case_export.json`, fields, tables, documents list, summary |
| `cat_output/cases/<case>_<name>/documents/` | The downloaded documents (ID/, Income/, Bank Statements/, POD/ …) plus `documents.json` / `.csv` |
| `customer_agent_sessions/` | Conversations with the customer agent (transcript + uploads per call) |
| `profiles_output/<case>/` | `persona.json` (AI Call Agent format), `stages.json` (32-stage analysis), per-call stage mapping |

Already-fetched calls and cases are skipped automatically.

---

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| `python` not found | Install Python and tick "Add to PATH", or use `py` instead of `python` |
| `npm` not found | Install Node.js LTS from nodejs.org |
| CAT tool dot is red | Connect to the office network / VPN |
| "CAT sign-in failed" / CAT documents dot is red | Check `CAT_URL`, `CAT_USERNAME`, `CAT_PASSWORD` in `.env` (sign in to the CAT site in a browser to confirm), then restart the app |
| "case not found in the CAT tool" | The case number isn't in CAT yet (documents are only there once the case has been created in CAT) |
| OpenAI dot is red | Add `OPENAI_API_KEY` to `.env`, then restart the app |
| "No calls in the Audit tool between …" | Widen the date range – the Audit tool can only search by date |
| "Daily limit reached" | The Audit tool allows 5,000 calls / 25,000 listed rows a day – try again tomorrow |
| Port 8600 already in use | Another copy is running – close it, or use `-Port 8700` |
| A model name is rejected | Pick a model your OpenAI account has in **Settings** |
