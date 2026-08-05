$ErrorActionPreference = 'Stop'
# SelfTracker silent installer - Windows v2
# Usage: $u='<webhook-url>'; irm <url>/install.ps1 | iex
$Dir = Join-Path $HOME '.spytracker'
New-Item -ItemType Directory -Force -Path $Dir | Out-Null
Invoke-WebRequest 'https://raw.githubusercontent.com/inf-nicolasperez/self-tracker/main/tracker.py' -OutFile (Join-Path $Dir 'tracker.py')
$py = 'python'
try { & $py --version 2>$null | Out-Null } catch { $py = 'py' }
& $py (Join-Path $Dir 'tracker.py') --install $u
