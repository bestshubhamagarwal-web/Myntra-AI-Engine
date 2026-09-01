# Daily pipeline for Task Scheduler. Repo root is two levels above this file.
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $RepoRoot

$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

& $Python -m src.cli pipeline --sources all --cluster
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if ((Get-Date).DayOfWeek -eq "Sunday") {
    & $Python -m src.cli ngrams
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m src.cli report
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $Python -m src.cli eval --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
