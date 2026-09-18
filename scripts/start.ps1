param(
    [string]$Python = "python",
    [int]$Port = 8000
)
$ErrorActionPreference = "Stop"
$projectPath = Split-Path -Parent $PSScriptRoot
$dataPath = Join-Path $projectPath "data\workbench"
$logPath = Join-Path $projectPath "output"
New-Item -ItemType Directory -Force -Path $logPath | Out-Null
$pythonPath = (Get-Command $Python -ErrorAction Stop).Source
$workerArguments = @('-m', 'disclosure.cli', '--data-dir', ('"' + $dataPath + '"'), 'worker')
$workerProcess = Start-Process -FilePath $pythonPath -ArgumentList $workerArguments -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'worker.out.log') -RedirectStandardError (Join-Path $logPath 'worker.err.log')
try {
    Write-Host "Disclosure Desk: http://127.0.0.1:$Port (worker PID $($workerProcess.Id))"
    & $pythonPath -m disclosure.cli --data-dir $dataPath serve --port $Port
} finally {
    if (-not $workerProcess.HasExited) {
        # Windows virtualenv Python may launch a child interpreter. Stop only
        # the process tree started above, so no orphan worker keeps running.
        & taskkill.exe /PID $workerProcess.Id /T /F | Out-Null
    }
}
