param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.12 或更新版本' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw '冻结依赖安装失败' }
& '.\.venv\Scripts\python.exe' -m pip install --no-deps -e '.[dev]'
if ($LASTEXITCODE -ne 0) { throw 'Python 依赖安装失败' }
if (-not (Test-Path -LiteralPath '.env')) {
    @'
from pathlib import Path
import secrets
from cryptography.fernet import Fernet
p=secrets.token_urlsafe(30)
content=(f'FPGA_DATABASE_URL=postgresql+psycopg://fpga:{p}@127.0.0.1:5432/fpga_platform\n'
         f'POSTGRES_PASSWORD={p}\nFPGA_DATA_DIR=.local/data\nFPGA_ALLOW_SQLITE=0\n'
         f'FPGA_WORKER_TOKEN={secrets.token_urlsafe(40)}\n'
         f'FPGA_VERIFIER_TOKEN={secrets.token_urlsafe(40)}\n'
         f'FPGA_SECRET_KEY={Fernet.generate_key().decode()}\n')
with Path('.env').open('x',encoding='utf-8') as f: f.write(content)
print('本机配置已生成；凭据仅保存于被 Git 忽略的 .env')
'@ | & '.\.venv\Scripts\python.exe' -
    if ($LASTEXITCODE -ne 0) { throw '本机配置生成失败' }
}
& npm.cmd --prefix apps/web ci
if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
& npm.cmd --prefix apps/web run build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
Write-Output '安装完成。PostgreSQL: docker compose up -d；随后 scripts/dev.ps1。无数据库时可显式使用 scripts/dev.ps1 -DatabaseMode Sqlite。'
