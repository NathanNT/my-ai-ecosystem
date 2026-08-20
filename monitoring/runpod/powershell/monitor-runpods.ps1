param(
    [string]$ApiBaseUrl = "https://rest.runpod.io/v1",
    [string]$ApiKey = $env:RUNPOD_API_KEY,
    [ValidateSet("all", "running", "stopped", "starting", "ending", "error", "other")]
    [string]$State = "all",
    [string[]]$PodIds,
    [switch]$Raw,
    [int]$TimeoutSec = 30
)

if (-not $ApiKey) {
    throw "RUNPOD_API_KEY manquante. Définis `$env:RUNPOD_API_KEY ou passe -ApiKey."
}

$headers = @{
    Authorization = "Bearer $ApiKey"
    "Content-Type" = "application/json"
}

$listUri = "$ApiBaseUrl/pods"

try {
    $response = Invoke-RestMethod -Method Get -Uri $listUri -Headers $headers -TimeoutSec $TimeoutSec -ErrorAction Stop
} catch {
    throw "Erreur API lors de la liste des pods: $($_.Exception.Message)"
}

$pods = $null
if ($response -and $response.pods) { $pods = $response.pods }
elseif ($response -and $response.data) { $pods = $response.data }
elseif ($response -and $response.Data) { $pods = $response.Data }
else { $pods = @() }

if ($PodIds -and $PodIds.Count -gt 0) {
    $pods = $pods | Where-Object { $PodIds -contains $_.id }
}

if ($State -ne "all") {
    $pods = $pods | Where-Object {
        $status = $_.state
        if (-not $status) { $status = $_.status }
        if (-not $status) { $status = $_.currentStatus }
        $status -and $status.ToLower() -eq $State.ToLower()
    }
}

if ($Raw) {
    $pods | ConvertTo-Json -Depth 20
    exit 0
}

if (-not $pods -or $pods.Count -eq 0) {
    Write-Host "Aucun pod correspondant."
    exit 0
}

$rows = foreach ($pod in $pods) {
    [PSCustomObject]@{
        id = $pod.id
        name = $pod.name
        state = $(if ($pod.state) { $pod.state } else { $pod.status })
        region = $pod.region
        gpuType = $pod.gpuType
        imageName = $pod.imageName
        createdAt = $pod.createdAt
        publicIp = $pod.publicIp
    }
}

$rows | Sort-Object state, name | Format-Table -AutoSize
