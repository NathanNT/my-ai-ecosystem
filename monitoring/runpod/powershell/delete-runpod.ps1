param(
    [Parameter(Mandatory = $true)]
    [string]$PodIdentifier,
    [string]$ApiBaseUrl = "https://rest.runpod.io/v1",
    [string]$ApiKey = $env:RUNPOD_API_KEY,
    [ValidateSet("delete", "terminate")]
    [string]$Action = "delete",
    [int]$TimeoutSec = 60
)

if (-not $ApiKey) {
    throw "RUNPOD_API_KEY manquante. Définis `$env:RUNPOD_API_KEY ou passe -ApiKey."
}

$headers = @{
    Authorization = "Bearer $ApiKey"
    "Content-Type" = "application/json"
}

$podId = $PodIdentifier

if (-not ($PodIdentifier -match "^[A-Za-z0-9_-]+$")) {
    throw "PodIdentifier semble invalide."
}

if ($PodIdentifier -notmatch "^[0-9a-fA-F-]{6,}$") {
    Write-Host "PodIdentifier ne ressemble pas à un ID -> tentative de résolution par nom."
    $listUri = "$ApiBaseUrl/pods"
    try {
        $list = Invoke-RestMethod -Method Get -Uri $listUri -Headers $headers -TimeoutSec $TimeoutSec -ErrorAction Stop
    } catch {
        throw "Erreur API lors de la liste des pods: $($_.Exception.Message)"
    }

    $pods = $null
    if ($list -and $list.pods) { $pods = $list.pods }
    elseif ($list -and $list.data) { $pods = $list.data }
    else { $pods = @() }

    $matches = @($pods | Where-Object { $_.name -eq $PodIdentifier })
    if ($matches.Count -eq 0) {
        throw "Aucun pod trouvé avec ce nom: $PodIdentifier"
    }
    if ($matches.Count -gt 1) {
        throw "Plusieurs pods portent ce nom. Utilise l'ID exact."
    }
    $podId = $matches[0].id
}

if ($Action -eq "delete") {
    $uri = "$ApiBaseUrl/pods/$podId"
    try {
        $response = Invoke-RestMethod -Method Delete -Uri $uri -Headers $headers -TimeoutSec $TimeoutSec -ErrorAction Stop
    } catch {
        throw "Erreur API lors du delete du pod ${podId}: $($_.Exception.Message)"
    }
} else {
    $uri = "$ApiBaseUrl/pods/$podId/terminate"
    $body = @{ reason = "requested_via_script" } | ConvertTo-Json
    try {
        $response = Invoke-RestMethod -Method Post -Uri $uri -Headers $headers -Body $body -TimeoutSec $TimeoutSec -ErrorAction Stop
    } catch {
        throw "Erreur API lors du terminate du pod ${podId}: $($_.Exception.Message)"
    }
}

Write-Host "Action '$Action' envoyée sur pod: $podId"
$response | ConvertTo-Json -Depth 20
