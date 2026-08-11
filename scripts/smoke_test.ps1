$ErrorActionPreference = "Stop"

function Wait-ForHttpEndpoint {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Uri,

        [Parameter(Mandatory = $true)]
        [scriptblock]$AssertResponse,

        [int]$Attempts = 30,
        [int]$DelaySeconds = 2
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 3
            & $AssertResponse $response
            return
        }
        catch {
            if ($attempt -eq $Attempts) {
                throw "Endpoint $Uri did not become ready after $Attempts attempts. Last error: $($_.Exception.Message)"
            }

            Start-Sleep -Seconds $DelaySeconds
        }
    }
}

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
        throw "Frontend placeholder page was not available."
    }
}

Write-Host "Smoke test passed: API and web endpoints are healthy."
