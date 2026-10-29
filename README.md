# FPGA 协同开发平台

当前交接的是开发中的工作台，不是已完成的完整首版。两位开发者请先读 [交接说明](docs/HANDOFF.md)，再领取其中的下一任务。

## 启动

需要 Python 3.12+、Node.js 22+。Windows PowerShell 在仓库根目录执行：

```powershell
.\scripts\setup.ps1
# PostgreSQL 部署路线，需已安装 Docker Desktop
 docker compose up -d
.\scripts\dev.ps1
# 当前无 PostgreSQL 时，可显式选择 SQLite 开发模式
.\scripts\dev.ps1 -DatabaseMode Sqlite
```

零预算静态 Worker 在另一终端运行 `.\scripts\worker.ps1`，或用 `-Once` 执行一次。令牌由本机 `.env`/环境读取；完整 Agent、EDA/CAD 及独立验收仍需后续接入。最新接续与 PostgreSQL 实测见 [Worker 交接](docs/WORKER-HANDOFF.md)。

打开 http://127.0.0.1:8765 ，首次使用自行设置管理员账号与密码；没有默认通用密码。SQLite 的结果不计入 PostgreSQL 并发验收。模型预算默认为 0，不会自动调用付费模型。

VS Code 直接打开仓库目录，使用 .vscode 中的启动与测试配置。前端独立开发：`npm.cmd --prefix apps/web run dev`，代理 API 到 8765。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm.cmd --prefix apps/web run typecheck
npm.cmd --prefix apps/web test -- --run
npm.cmd --prefix apps/web run build
```

最新接续回归为 Python 170 项通过、6 项 RTL 跳过、1 项依赖警告，包含四项真实 PostgreSQL 并发/事务测试。当前机器没有 Icarus，跳过不能记为通过；队友机器的历史 RTL 结果保留在原验证记录。最新范围与限制见 [Worker 交接](docs/WORKER-HANDOFF.md)，历史记录见 [verification.md](docs/verification.md)。

## 资料

- [总体设计](docs/reference/overall-design/FPGA多Agent协同开发平台完整设计方案.md)，同目录有 Word 和两张架构图。
- [开发规格书](docs/reference/development-spec/FPGA多Agent协同开发平台开发规格书_v1.0.md)，同目录有 33 页 Word、完整 ZIP、接口契约、验收与工作包。
- `packages/contracts` 是代码所用冻结契约；`docs/reference/source-lock.json` 记录来源哈希。
- `legacy/fpga-agent` 是只读迁移参考，保留来源及第三方授权；不代表已接入新平台。
- `docs/analyzer-implementation.md`、`docs/domain-implementation.md`、`docs/web-implementation.md` 记录各模块细节。

`.env`、.venv、node_modules、.local、数据库、QA账号与工具二进制不上传。真实配置须在接手机器重新登记，勿提交密钥。

独立验收接续与本机服务登记方式见 [VERIFICATION-HANDOFF.md](docs/VERIFICATION-HANDOFF.md)。签名证据接口就绪不代表真实工程验收已执行。
