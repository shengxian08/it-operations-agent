$ErrorActionPreference = "Stop"

function Invoke-Compose {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $output = & docker compose @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    if ($exitCode -ne 0) {
        throw "docker compose $($Arguments -join ' ') failed:`n$($output -join [Environment]::NewLine)"
    }
    $output | ForEach-Object { Write-Host $_ }
    return ($output -join "`n")
}

function Wait-ForHttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][scriptblock]$AssertResponse,
        [int]$Attempts = 60,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 5
            & $AssertResponse $response
            return
        }
        catch {
            if ($attempt -eq $Attempts) {
                throw "Endpoint $Uri did not become ready. Last error: $($_.Exception.Message)"
            }
            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

function Invoke-JsonPost {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][hashtable]$Body,
        [hashtable]$Headers = @{}
    )

    return Invoke-WebRequest -Uri $Uri -Method Post -UseBasicParsing `
        -ContentType "application/json; charset=utf-8" -Headers $Headers `
        -Body ($Body | ConvertTo-Json -Depth 10 -Compress) -TimeoutSec 30
}

function Get-SseEvent {
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][string]$Name
    )

    $pattern = "(?m)^event: $([regex]::Escape($Name))`r?`ndata: (.+)$"
    $match = [regex]::Match($Content, $pattern)
    if (-not $match.Success) {
        throw "SSE event '$Name' was not found."
    }
    return ($match.Groups[1].Value.Trim() | ConvertFrom-Json)
}

Write-Host "[1/8] Verifying database migration"
$migration = Invoke-Compose -Arguments @("exec", "-T", "api", "alembic", "current")
if ($migration -notmatch "\(head\)") {
    throw "Alembic is not at head."
}

Write-Host "[2/8] Verifying idempotent seed data"
$seedFirst = Invoke-Compose -Arguments @("exec", "-T", "api", "python", "/app/scripts/seed_data.py")
$seedSecond = Invoke-Compose -Arguments @("exec", "-T", "api", "python", "/app/scripts/seed_data.py")
if ($seedFirst -notmatch "Seeded 2 users, 2 conversations, 100 tickets" -or $seedSecond -notmatch "Seeded 2 users, 2 conversations, 100 tickets") {
    throw "Seed data did not produce the expected stable result."
}

Write-Host "[3/8] Verifying idempotent knowledge import"
$ingestFirst = Invoke-Compose -Arguments @("exec", "-T", "api", "python", "/app/scripts/ingest_knowledge.py")
$ingestSecond = Invoke-Compose -Arguments @("exec", "-T", "api", "python", "/app/scripts/ingest_knowledge.py")
if ($ingestFirst -notmatch "Ingested \d+ documents and \d+ chunks" -or $ingestSecond -notmatch "Ingested \d+ documents and \d+ chunks") {
    throw "Knowledge import did not complete twice."
}

Write-Host "[4/8] Verifying API and web"
Wait-ForHttpEndpoint -Uri "http://localhost:18000/health" -AssertResponse {
    param($response)
    $payload = $response.Content | ConvertFrom-Json
    if ($response.StatusCode -ne 200 -or $payload.status -ne "ok") {
        throw "API health response was not healthy."
    }
}
Wait-ForHttpEndpoint -Uri "http://localhost:5173" -AssertResponse {
    param($response)
    if ($response.StatusCode -ne 200 -or $response.Content -notmatch 'id="root"') {
        throw "Web application was not available."
    }
}

$streamUri = "http://localhost:18000/api/conversations/c-001/messages:stream"
Write-Host "[5/8] Verifying VPN citation and ticket lookup SSE"
$vpnResponse = Invoke-JsonPost -Uri $streamUri -Body @{
    user_id = "u-001"
    content = "VPN cannot connect"
}
$vpnCitations = Get-SseEvent -Content $vpnResponse.Content -Name "citations"
$vpnFinal = Get-SseEvent -Content $vpnResponse.Content -Name "final"
$vpnSourcePaths = @($vpnCitations.citations | ForEach-Object { $_.source_path })
if ($vpnFinal.final_state -ne "answered" -or $vpnSourcePaths -notcontains "vpn-connection.md") {
    throw "VPN flow did not return the expected answer and citation."
}

$lookupResponse = Invoke-JsonPost -Uri $streamUri -Body @{
    user_id = "u-001"
    content = "Check ticket IT-2026-0001"
}
$lookupFinal = Get-SseEvent -Content $lookupResponse.Content -Name "final"
if ($lookupFinal.final_state -ne "ticket_status" -or $lookupFinal.answer -notmatch "IT-2026-0001") {
    throw "Ticket lookup did not return ticket_status for IT-2026-0001."
}

Write-Host "[6/8] Verifying draft, rejection, and confirmed creation"
$traceId = "smoke-ticket-$([guid]::NewGuid().ToString('N'))"
$draftResponse = Invoke-JsonPost -Uri $streamUri -Headers @{ "X-Trace-Id" = $traceId } -Body @{
    user_id = "u-001"
    content = "Please create ticket for VPN outage"
}
$draftEvent = Get-SseEvent -Content $draftResponse.Content -Name "ticket_draft"
$draftFinal = Get-SseEvent -Content $draftResponse.Content -Name "final"
if ($draftFinal.final_state -ne "awaiting_confirmation") {
    throw "Ticket request did not stop at awaiting_confirmation."
}

$confirmationUri = "http://localhost:18000/api/conversations/c-001/ticket-confirmations"
$rejected = $false
try {
    Invoke-JsonPost -Uri $confirmationUri -Body @{
        user_id = "u-001"
        draft = $draftEvent.draft
        idempotency_key = "smoke-missing-token"
    } | Out-Null
}
catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 422) {
        $rejected = $true
    }
    else {
        throw
    }
}
if (-not $rejected) {
    throw "Ticket creation without a confirmation token was not rejected."
}

$createdResponse = Invoke-JsonPost -Uri $confirmationUri -Headers @{ "X-Trace-Id" = $traceId } -Body @{
    user_id = "u-001"
    confirmation_token = $draftEvent.confirmation_token
    draft = $draftEvent.draft
    idempotency_key = "smoke-$([guid]::NewGuid().ToString('N'))"
}
$created = $createdResponse.Content | ConvertFrom-Json
if ($createdResponse.StatusCode -ne 201 -or $created.ticket_number -notmatch '^IT-2026-\d{4,}$') {
    throw "Confirmed ticket creation did not return HTTP 201 and a 2026 ticket number."
}

Write-Host "[7/8] Running fixed evaluation"
Invoke-Compose -Arguments @(
    "exec", "-T", "api", "python", "/app/scripts/run_evaluation.py",
    "--input", "/app/data/eval/cases.jsonl",
    "--report", "/app/docs/evaluation-report.md"
) | Out-Null

Write-Host "[8/8] Verifying evaluation report"
$reportPath = Join-Path $PSScriptRoot "..\docs\evaluation-report.md"
if (-not (Test-Path -LiteralPath $reportPath)) {
    throw "Evaluation report was not generated."
}
$report = [IO.File]::ReadAllText((Resolve-Path -LiteralPath $reportPath))
if ($report -notmatch "Recall@5" -or $report -notmatch "Citation precision" -or $report -notmatch "Unconfirmed ticket writes \| 0") {
    throw "Evaluation report is missing release metrics or reports an unsafe write."
}

Write-Host "Smoke test passed: migration, idempotent imports, web/API, three flows, safety gate, and evaluation are valid."
