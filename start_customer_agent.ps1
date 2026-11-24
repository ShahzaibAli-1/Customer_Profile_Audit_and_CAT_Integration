# Starts the realistic customer agent for the AI Call Agent (Katie) to talk to.
#   powershell -ExecutionPolicy Bypass -File .\start_customer_agent.ps1            (port 8020)
#   powershell -ExecutionPolicy Bypass -File .\start_customer_agent.ps1 -Port 8010 (replace the old agent's port)
# Then set CUSTOMER_AGENT_URL=http://localhost:8020 in C:\AI_Call_Agent\backend\.env and restart Katie's backend.
param([int]$Port = 8020)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $here
Write-Host "Customer agent: http://localhost:$Port   (personas: http://localhost:$Port/api/new-customers)"
python -m uvicorn customer_agent.server:app --host 127.0.0.1 --port $Port
Pop-Location
