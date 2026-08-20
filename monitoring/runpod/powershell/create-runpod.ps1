param(
    [string]$ApiBaseUrl = "https://rest.runpod.io/v1",
    [string]$ApiKey = $env:RUNPOD_API_KEY,
    [string]$TemplatePath = "$PSScriptRoot\create-pod-template.example.json",
    [int]$TimeoutSec = 60
)

if (-not $ApiKey) {
    throw "RUNPOD_API_KEY manquante. Définis `$env:RUNPOD_API_KEY ou passe -ApiKey."
}

if (-not (Test-Path -LiteralPath $TemplatePath)) {
    throw "Template introuvable: $TemplatePath"
}

$headers = @{
    Authorization = "Bearer $ApiKey"
    "Content-Type" = "application/json"
}

$rawTemplate = Get-Content -LiteralPath $TemplatePath -Raw
try {
    $template = $rawTemplate | ConvertFrom-Json -Depth 20
} catch {
    throw "Le template JSON n'est pas valide: $($_.Exception.Message)"
}

$createUri = "$ApiBaseUrl/pods"
$body = $template | ConvertTo-Json -Depth 20

try {
    $response = Invoke-RestMethod -Method Post -Uri $createUri -Headers $headers -Body $body -TimeoutSec $TimeoutSec -ErrorAction Stop
} catch {
    throw "Erreur API lors de la création du pod: $($_.Exception.Message)"
}

Write-Host "Réponse API create :"
$response | ConvertTo-Json -Depth 20
