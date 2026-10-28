# Real desktop captures of the app: popup views, tray and taskbar pill. Offscreen
# renders cannot exercise Explorer, DWM or native window placement.
param([Parameter(Mandatory = $true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
# A fresh demo config: no provider calls, no runner credentials/settings.
@{ floatingPill = $true; windowsTaskbar = $true } | ConvertTo-Json |
    Set-Content -Encoding utf8 $env:TIDESI_CONFIG
# The runner's own console sits on this desktop and would end up in every
# shot: minimize everything first. The app's windows open after this, so they
# are unaffected. Best effort: a failure here only costs a cosmetic cleanup.
try {
    (New-Object -ComObject Shell.Application).MinimizeAll()
    Start-Sleep -Seconds 1
} catch { Write-Warning "Could not minimize runner windows: $_" }
# --tour runs the real app (tray, popup, docked pill) and photographs the
# screen once per view: a few popup tabs and settings sections.
python hosts/desktop/app.py --demo --tour $OutputDirectory
if ($LASTEXITCODE -ne 0) { throw "Desktop tour failed ($LASTEXITCODE)" }
