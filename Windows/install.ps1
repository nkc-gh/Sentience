$ErrorActionPreference = "Stop"

$repoUrl = if ($env:SENTIENCE_REPO_URL) { $env:SENTIENCE_REPO_URL } else { "https://github.com/nkc-gh/Sentience.git" }
$installRoot = if ($env:SENTIENCE_INSTALL_DIR) { $env:SENTIENCE_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Sentience" }
$source = Join-Path $installRoot "source"
$venv = Join-Path $installRoot "venv"

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { throw "Git for Windows is required. Install Git and run this command again." }
$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) { throw "CPython 3.13.x is required. Install it and run this command again." }
try {
    & $python.Source -3.13 -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" 2>&1 | Out-Null
} catch {
    throw "CPython 3.13.x is required. Install it and run this command again."
}
if ($LASTEXITCODE -ne 0) { throw "CPython 3.13.x is required. Install it and run this command again." }

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
if (Test-Path (Join-Path $source ".git")) {
    git -C $source pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "Could not update the Sentience source directory." }
} else {
    if (Test-Path $source) { Remove-Item -Recurse -Force $source }
    git clone --depth 1 $repoUrl $source
    if ($LASTEXITCODE -ne 0) { throw "Could not download Sentience from $repoUrl" }
}

$project = Join-Path $source "Windows"
if (-not (Test-Path (Join-Path $project "pyproject.toml"))) { throw "Windows project was not found in $repoUrl" }

if (Test-Path (Join-Path $venv "Scripts\python.exe")) {
    & (Join-Path $venv "Scripts\python.exe") -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 13) else 1)" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $venv }
}
& $python.Source -3.13 -m venv $venv
if ($LASTEXITCODE -ne 0) { throw "Could not create the Python virtual environment." }
$venvPython = Join-Path $venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
& $venvPython -m pip install -e "${project}[dev]"
if ($LASTEXITCODE -ne 0) { throw "Sentience dependencies could not be installed. Check the Python version and error above." }
& $venvPython -c "import cli.entrypoint"
if ($LASTEXITCODE -ne 0) { throw "Sentience was installed, but its CLI package could not be imported." }

$bin = Join-Path $installRoot "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$cmd = Join-Path $bin "sentience.cmd"
@"
@echo off
setlocal
"%~dp0..\venv\Scripts\python.exe" -m cli.entrypoint %*
exit /b %ERRORLEVEL%
"@ | Set-Content -Encoding ASCII $cmd

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$bin*") { [Environment]::SetEnvironmentVariable("Path", (($userPath.TrimEnd(';') + ";" + $bin).Trim(';')), "User") }

Write-Host "Sentience Windows installed. Restart PowerShell, then run: sentience"
