param([ValidateRange(1,65535)][int]$Port = 8765, [switch]$Once)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw '先运行 scripts/setup.ps1' }
$workerArguments = @('-m', 'workers.static_worker', '--api-url', "http://127.0.0.1:$Port")
if ($Once) { $workerArguments += '--once' }
& '.\.venv\Scripts\python.exe' @workerArguments
exit $LASTEXITCODE
