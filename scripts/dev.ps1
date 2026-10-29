param([ValidateSet('Postgres','Sqlite')][string]$DatabaseMode = 'Postgres', [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) { throw '先运行 scripts/setup.ps1' }
if (-not (Test-Path -LiteralPath 'apps\web\dist\index.html')) {
    & npm.cmd --prefix apps/web run build
    if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
}
$arguments = @('-m', 'apps.api.cli', '--port', "$Port")
if ($DatabaseMode -eq 'Sqlite') { $arguments += '--sqlite' }
& '.\.venv\Scripts\python.exe' @arguments
exit $LASTEXITCODE
