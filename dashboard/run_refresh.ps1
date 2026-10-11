$ErrorActionPreference = "Stop"

$DashboardDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $DashboardDir

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $Python = $VenvPython
}
else {
    $Python = "python"
}

$LogDir = Join-Path $ProjectRoot "logs"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}

$Timestamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogDir "refresh_$Timestamp.log"

Set-Location $ProjectRoot

Write-Host "NYC 311 refresh started..."
Write-Host "Log: $LogFile"

& $Python "dashboard\refresh_data.py" 2>&1 |
    Tee-Object -FilePath $LogFile

if ($LASTEXITCODE -ne 0) {
    Write-Error "Refresh failed. See $LogFile"
    exit $LASTEXITCODE
}

Write-Host "Refresh completed successfully."
