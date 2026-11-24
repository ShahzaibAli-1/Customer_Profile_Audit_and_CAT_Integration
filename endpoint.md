Case export API: access details

Base URL: https://sp4rk.co.uk/api/export
Auth: send the header x-api-key: <key> on every request. I'll send you the key separately. Don't share it or commit it to code; keep it in an env variable.
SFM department id: 8
1. List cases
GET /export/cases?department_id=8&from=2026-09-01&to=2026-09-30

Dates are YYYY-MM-DD in UTC, 92 days max per call.
You get up to 1000 cases per page. If next_cursor comes back, call again with &cursor=<next_cursor>.
The response also includes max_batch, the most call ids you can request at once.
2. Get details
POST /export/cases/details with body { "call_ids": [ ... ] }

Send up to max_batch ids (25 in the day, 100 at night UK time), one request at a time.
For each call you get:
the transcript
an audio link
the full compliance report (every question, its status, reasoning and evidence)
CRM results
the SFM case record

Rules

Audio links expire after 2 hours. Download each file as soon as you get it. If a link expires, request that call again.
On a 429, wait for the number of seconds in the Retry-After header, then retry. When the server is busy it pauses you on purpose.
Stop if you see daily_limit_reached. The limits are 5,000 calls and 25,000 list rows a day.
errors: if a call has an errors list, that part (e.g. transcript or audio) was missing for that call only.

API key is present in AUDIT_API_KEY in .env