$ErrorActionPreference = 'Stop'
$RepoBase = 'https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/main'
$Dir = Join-Path $HOME '.spytracker'
$Script = Join-Path $Dir 'tracker.py'
$Cfg = Join-Path $Dir 'config.json'

New-Item -ItemType Directory -Force -Path $Dir | Out-Null

Write-Host 'Downloading SelfTracker...' -ForegroundColor Cyan
Invoke-WebRequest "$RepoBase/tracker.py" -OutFile $Script

$py = $null
foreach ($cand in @('py', 'python')) {
    try {
        $v = & $cand --version 2>&1
        if ($LASTEXITCODE -eq 0) { $py = $cand; break }
    } catch { }
}
if (-not $py) {
    Write-Host 'Python 3 is required. Install it from https://www.python.org/downloads/ (tick "Add to PATH").' -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $Cfg)) {
    Write-Host 'Paste your Discord webhook URL (Discord server > channel settings > Integrations > Webhooks > New Webhook).' -ForegroundColor Yellow
    $url = Read-Host 'Webhook URL (press Enter to skip)'
    if ($url) {
        @{ webhook_url = $url } | ConvertTo-Json | Set-Content -Path $Cfg -Encoding UTF8
    }
}

& $py $Script --install
