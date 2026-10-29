# Worker 工件与常驻执行交接（2026-10-29）

基线 `827cecf`，沿用 `codex/device-persistence` 和草稿 PR #1。本轮接续 Worker 工件接口、常驻静态任务及 PostgreSQL 并发验证。

## 启动方式

先按 README 安装配置，在两个终端分别启动 API 和 Worker：

```powershell
.\scripts\dev.ps1 -DatabaseMode Sqlite
.\scripts\worker.ps1
# 仅领取并处理一次（队列为空时直接退出）
.\scripts\worker.ps1 -Once
```

PostgreSQL 环境使用 `dev.ps1 -DatabaseMode Postgres`。Worker 从本机 `.env` 或环境读取 `FPGA_WORKER_TOKEN`，不接受命令行明文令牌；身份保存在 `.local/static-worker/worker-id`。不同部署进程可通过 `python -m workers.static_worker --state-dir ...` 指定独立状态目录。默认 API 仅接受回环地址，不跟随重定向。

导入样例、批准冻结、创建预算0的 simulate 运行后，Worker 主动领取 `contract.validate`，下载三个冻结 JSON、校验 SHA256/Schema/项目/版本及契约引用，上传报告和日志并提交 ToolResult。静态任务可 SUCCEEDED；Run 继续 BLOCKED，独立验证保持 NOT_RUN。此 Worker 不执行 HDL、脚本、模型或外部工程工具。

## 任务限定工件接口

实现原冻结路径：

- `POST /worker/jobs/{job_id}/uploads`
- `PUT /worker/jobs/{job_id}/uploads/{resource_id}/content`
- `POST /worker/jobs/{job_id}/uploads/{resource_id}/complete`
- `GET /worker/jobs/{job_id}/artifacts/{resource_id}/content`

所有路径以 `/api/v1` 开头。Bearer 服务凭据外，还要求 `X-Worker-ID`、`X-Task-Attempt`、`X-Fencing-Token`；这些附加请求头避免改写冻结 UploadInit/UploadComplete。GET 只允许该 envelope 中的输入。上传票据绑定任务、Worker、项目及 fence；完成结果再次验证 attempt/fence。

静态输出仅允许 `contract-validation.json`（application/json）与 `worker.log`（text/plain）。每文件1MiB、每 attempt 最多10票据、总登记大小4MiB；失败/过期票据也占该 attempt 配额。票据10分钟过期。慢上传不持有数据库事务，写入前重新验证租约。输入文件在 API 读取前检查文件大小，读取和客户端流式接收均限制4MiB；超限写入确定的失败报告，避免反复耗尽租约。

幂等缓存命中前仍重新鉴权及核验租约，旧 fence 不能重取历史票据。输出工件按当前任务元数据归属，项目成员可从统一工件通道读取；它们不继承人类私有文档上传者规则。

## 心跳、取消、恢复和截止时间

Worker 15秒心跳、默认2秒空闲轮询；网络写入不明确时仅重发原请求体和相同幂等键。Ctrl+C 请求停止新领取，保留日志并结束当前静态任务。退出时等待心跳线程结束。

- 仅固定静态 JSON 检查允许过期租约安全重领；每次重领 attempt/fence 递增，旧输出无法进入新结果。
- 取消与输入下载、报告上传或最终完成竞争时，整个活动任务边界处理 CANCELLATION_PENDING，保存日志并提交 cancelled。静态 Worker 崩溃后的过期 CANCEL_REQUESTED 在10次领取上限内可由新 lease 仅做取消清理，不继续输入处理。
- 检查绝对 deadline，超时不下载新输入、不提升报告、不续租或提交成功；仍可在有效 lease 内保存超时日志和结果。刚领到的已过期排队任务同样产生 timeout，不能算静态通过。
- 上述恢复只适用于无外部程序/模型副作用的固定静态 Worker。其他能力仍需隔离执行与进程对账，不能照搬这一恢复规则。

## 实际验证

新增 API 与 Worker 测试先观察缺失接口/模块、取消竞态、超大输入和 deadline 的失败，再实现或修复。覆盖冻结输入读取、SHA256、跨项目拒绝、票据配额/期限、旧 fence、连续任务、后启动任务、正常停止、失败报告、取消清理及 deadline。

真实独立 CLI 进程经 TCP 连接 API 执行 `--once` 成功，任务 SUCCEEDED、Run BLOCKED；常驻循环测试从空队列开始，接收到后来创建的任务并干净退出。最终全套：**170 passed、6 skipped、1 依赖弃用警告**，其中四项为真实 PostgreSQL 测试；6个跳过仅为 Icarus/vvp 未安装的RTL测试。Ruff 和 pip check 通过，冻结来源179条SHA256一致。原144个操作当前注册141个；存在路由不等于完整产品验收。

## PostgreSQL 实测

本机原无 PostgreSQL/Docker，从 [EDB 官方二进制包页面](https://www.enterprisedb.com/download-postgresql-binaries) 下载 PostgreSQL16.15（Windows x64，16.15-5），解包在 `.local`。归档 SHA256：`43bb45f173a6f08cf1d29a97a6d8deb119e8e8093a24c00d2d1001a0ccaa8281`。这是实际下载字节哈希；没有把工具二进制、密码或数据库放进 Git。

中文路径下 initdb 首次失败，使用临时 ASCII 盘符映射到同一工作区后建立专用 `fpga_test_worker`，只监听127.0.0.1随机空闲端口；完成后停止实例并移除本次映射，不注册系统服务。测试用每项独立 schema 并在结束后清理。

`tests/postgres/test_concurrency.py` 真实通过四项：

1. 多个独立 Python 进程经 API 争领同一任务，只产生一个有效领取。
2. 多项目任务过期后多进程重领，无重复返回三元组，观察到新 attempt/fence。
3. PostgreSQL 下静态 Worker 的下载、上传和完成闭环。
4. 持有第一项目事务时，第二事件等待提交；读取游标不会越过未提交事件。

自行复现时设置 `FPGA_TEST_POSTGRES_URL`，目标数据库名称须以 `fpga_test_` 开头；未配置时测试明确跳过。测试会创建并删除自有临时 schema。此次未测试 pgvector、Alembic、生产压力或完整事务竞争矩阵。

## 审阅与裁定

独立只读审阅发现3项 Important：取消边界可能悬挂、输入限额执行太晚、deadline 未执行。均补红绿回归并修复，最终以全套回归收口。

Ruling: 保留旧 complete 接口允许无产物的静态成功结果以兼容现有调用；新常驻 Worker 始终上传报告/日志。风险：静态任务状态仍不是独立工程验收证明。

Ruling: 原 Worker 共享本机服务令牌继续保留；新路径用 lease 三元组限定当前任务。风险：这不是多租户独立 Worker 身份体系，部署多租户前仍需分离凭据及能力登记。

Ruling: 只对固定静态任务恢复过期与取消状态；其他任务维持原对账约束。风险：未来新增副作用必须移出该自动恢复路径。

Final: minor (deferred): PostgreSQL 多项目回收测试目前验证返回三元组唯一且存在一次重领，尚未逐任务证明所有8个任务最终被领取和整个周期内的公平性。

独立审阅未重跑测试，也未对84项P0、HDL隔离、独立验收、pgvector或整体既有 run/task 锁序作完成判断；本记录的实际运行结果来自主代理执行。取消/完成的完整 PostgreSQL 竞争矩阵仍待补充。

## 后续任务

1. 任务服务身份细分、保留验收 Worker 与签发证据入库、有效性投影。
2. Alembic 与 pgvector 部署、取消/完成竞争、全任务回收/公平性和更大压力。
3. 真正隔离 EDA/CAD 后端及完整 Agent 编排，接入真实模型与预算后才开启相应能力。
4. 现有 scheduler_ready 保持 false：常驻静态 Worker 不代表完整调度器或84项P0就绪。
