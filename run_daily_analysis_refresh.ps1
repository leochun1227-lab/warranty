param(
    [string]$Python = "",
    [switch]$SkipFirebase
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "analysis_refresh_$Stamp.log"

function Find-AnalysisPython {
    if ($Python) {
        return $Python
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.14-64\python.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
        "python"
    )

    foreach ($candidate in $candidates) {
        try {
            & $candidate -c "import pyodbc,pandas,openpyxl,firebase_admin" 2>$null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        } catch {
            continue
        }
    }

    throw "No Python runtime with pyodbc, pandas, openpyxl, and firebase_admin was found."
}

$SelectedPython = Find-AnalysisPython
$ScriptArgs = @(
    "rebuild_model_series_assets.py",
    "--log-level",
    "INFO"
)

if ($SkipFirebase) {
    throw "--SkipFirebase is no longer supported because model-series rebuild now depends on current Firebase tickets."
}

"[$(Get-Date -Format s)] Using Python: $SelectedPython" | Tee-Object -FilePath $LogPath
"[$(Get-Date -Format s)] Starting analysis refresh" | Tee-Object -FilePath $LogPath -Append

Push-Location $Root
try {
    $ProcessInfo = New-Object System.Diagnostics.ProcessStartInfo
    $ProcessInfo.FileName = $SelectedPython
    $ProcessInfo.WorkingDirectory = $Root
    $ProcessInfo.UseShellExecute = $false
    $ProcessInfo.RedirectStandardOutput = $true
    $ProcessInfo.RedirectStandardError = $true
    $ProcessInfo.Arguments = ($ScriptArgs | ForEach-Object {
        '"' + ($_ -replace '"', '\"') + '"'
    }) -join " "

    $Process = [System.Diagnostics.Process]::Start($ProcessInfo)
    $StdOut = $Process.StandardOutput.ReadToEnd()
    $StdErr = $Process.StandardError.ReadToEnd()
    $Process.WaitForExit()

    if ($StdOut) {
        $StdOut.TrimEnd() | Tee-Object -FilePath $LogPath -Append
    }
    if ($StdErr) {
        $StdErr.TrimEnd() | Tee-Object -FilePath $LogPath -Append
    }

    $ExitCode = $Process.ExitCode
    if ($ExitCode -ne 0) {
        throw "Analysis refresh failed with exit code $ExitCode."
    }
    "[$(Get-Date -Format s)] Analysis refresh completed" | Tee-Object -FilePath $LogPath -Append
} finally {
    Pop-Location
}
