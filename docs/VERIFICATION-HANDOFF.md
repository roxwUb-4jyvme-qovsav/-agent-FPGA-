# 独立验收身份与证据接续（2026-10-29）

基线为远端 main `ef55699`，本轮分支 `codex/verification-evidence`。通过本机系统代理完成 Git fetch 与 fast-forward，保留原工作目录；外层设计资料没有改写。

## 本轮范围

沿用 WORKER-HANDOFF 的下一优先项，接通服务身份隔离、验收报告受控上传、签名证据持久化和有效性查询。没有真实隔离验收后端，本轮不自动创建验收任务、不执行生成程序、不签发实际工程 PASS。84 项 P0 范围保留，scheduler_ready 仍为 false。

新增冻结路径：

- `POST /api/v1/verification/evidence`，返回 ActionResult。
- `GET /api/v1/projects/{project_id}/evidence/{resource_id}/validity`，返回 EvidenceValidity。

原有 evidence 列表与详情返回不可变 VerificationEvidence 原文；任务详情保持冻结 TaskRecord，result 为 ToolResult 摘要与报告引用。原文签名不被摘要或投影覆盖。单条有效 PASS 只完成其任务，Run 保持 BLOCKED、Revision 不自动 VERIFIED，发布入口继续要求完整独立证据链。

## 服务身份登记

可通过 `.env` 设置 `FPGA_SERVICE_PRINCIPALS=.local/service-principals.json`。该文件由本机运维维护，没有浏览器写入口；不得提交凭据或实际身份配置。每次请求重读，文件缺失/损坏返回 503；启用登记表后完全关闭旧共享令牌回退。

格式如下，尖括号内容须由部署者实际配置，不能直接作为可用凭据：

```json
{
  "schema_version": "1.0",
  "principals": [
    {
      "worker_id": "<服务进程的UUID>",
      "role": "worker",
      "enabled": true,
      "token_sha256": "<该进程独有随机令牌的SHA256小写十六进制>",
      "project_ids": ["<已授权项目UUID>"],
      "capabilities": ["contract.validate"],
      "issuer_id": null
    },
    {
      "worker_id": "<另一服务进程的UUID>",
      "role": "verifier",
      "enabled": true,
      "token_sha256": "<验收进程独有随机令牌的SHA256>",
      "project_ids": ["<已授权项目UUID>"],
      "capabilities": ["acceptance.verify"],
      "issuer_id": "<签发者UUID>"
    }
  ]
}
```

`project_ids: ["*"]` 是显式全项目授权，应仅用于确有该范围的本机服务。worker_id 与令牌摘要均须唯一。`worker` 目前只登记 `contract.validate`，`verifier` 只登记 `acceptance.verify`；不接受客户端自行增加能力。静态 CLI 继续从 `FPGA_WORKER_TOKEN` 读取自己的令牌，其 `.local/static-worker/worker-id` 必须与登记表相符。

未配置登记表时，旧 FPGA_WORKER_TOKEN 仍仅支持本机静态 Worker，不能领取验收任务、读取保留输入或写证据。原 FPGA_VERIFIER_TOKEN 单独设置不授予验收身份；验收服务必须逐进程登记。身份、项目和能力撤销在命中幂等缓存前重新检查，包括领取任务的历史信封。

## 公钥登记

设置 `FPGA_VERIFIER_KEYS=.local/verifier-public-keys.json`。文件只存批准的 Ed25519 原始32字节公钥（标准 base64 编码），不存私钥：

```json
{
  "schema_version": "1.0",
  "keys": [
    {
      "issuer_id": "<签发者UUID>",
      "key_id": "acceptance-2026-1",
      "public_key_base64": "<32字节Ed25519公钥的base64>",
      "revoked_at": null
    }
  ]
}
```

登记表无效或缺失时阻断新入库；有效性查询给出不能核验签名的 INDETERMINATE。撤销时设置 revoked_at，入库（包括旧幂等键）拒绝该密钥的新提交。已有证据仍按签名完成时刻与撤销时刻核验；不能删掉旧公钥后仍期待验证历史签名。轮换用新 key_id，不覆盖旧公钥。文件更新应原子替换，避免请求读取半份登记表。

## 可信任务与报告

`apps.api.services.verification.register_assignment(db, task, expected)` 仅供未来可信调度器调用，在与任务同一事务内登记固定预期：test_id、suite_sha256、environment_sha256、tool_version、template_id、seed、mode。input_sha256 从冻结 input_artifacts 完整数组计算；同一任务预期不可改写。没有提供客户端创建或修改验收预期的接口。

**后端可用性条件仍未具备**：当前编排只创建静态 contract.validate；真实隔离后端、可信验收套件/环境锁和能力健康检查接入之后，才能创建 acceptance.verify 任务并登记预期。测试中的任务由测试夹具注入，不能计作真实 EDA 或保留验收执行。

验收任务由登记的 verifier 通过原 `/worker/jobs/claim` 领取，并使用同一任务限定上传路径。仍要求 X-Worker-ID、X-Task-Attempt、X-Fencing-Token；验收报告仅允许 `verification-report.json` / application/json，另允许 `worker.log` / text/plain，配额与静态 Worker 相同。普通 Worker 无法借用验收 lease，输入下载仅限本信封引用。

证据提交要求 Bearer 独立服务令牌、上述三个租约请求头和 Idempotency-Key。服务器同时核验签名、issuer、项目、版本、任务、固定测试/环境/工具/模板/种子/模式/输入摘要、报告完整引用及实际文件字节；报告还必须来自当前 worker/attempt/fence 的 verifier 受控上传。取消、过期租约、截止之后的新提交拒绝入库。验收成功不能走普通 complete 绕开签名；普通 complete 仅允许取消、超时或失败诊断。

同一证据ID同一内容可安全重发，返回原 ActionResult，不重复事件、不改原文；同ID不同内容返回409。重放仍检查当前身份、公钥与报告。mock/replay 的签名 PASS 在有效性投影为 INDETERMINATE。

有效性默认针对证据原版本；可选 `?revision_id=<同项目版本UUID>` 用于检查其他版本适用性。该查询参数为兼容扩展，不改变冻结响应Schema。跨版本复用未登记，返回 STALE；原始 PASS 不改。投影独立保存在 system_values，后续接复用映射时仍需完整依赖核对。

## 验证与限制

已观察失败再修复：身份配置缺失、项目权限收回后的领取缓存、TaskRecord响应不符、PostgreSQL审计字段超长、取消与入库锁序冲突。新增API测试覆盖签名/摘要不符、取消/过期/旧fence、报告字节损坏、签名不可变、ID冲突、撤销、公钥历史核验与mock降级。

本机从已存在的 PostgreSQL16.15 Windows 二进制归档解包至 `.local`，建立仅监听回环地址随机端口的专用 `fpga_test_verification`。测试使用独立schema并清理；不注册系统服务、不修改生产数据库、不提交工具或密码。

新增五项真实PostgreSQL测试：同一证据并发提交只产生一条原文/一个事件；入库等待run锁期间取消事务仍能锁定任务，随后入库返回409。验收普通失败结束也采用 run→task，避免与签名入库交叉死锁；另外分别验证心跳和完成在任务重领等待之后必须再次核验服务身份，旧身份即使携带新三元组也返回403。全部验收租约通道统一 run→task 锁序，并在等待之后重读任务。

最终命令、测试数量及审阅结果见本文件末尾的最终记录；历史交接成绩不覆盖。

## 下一任务

1. 真实隔离验收后端及可信套件/环境登记，再接验收任务创建和完整阻断测试门禁。
2. Alembic、pgvector、数据库备份恢复；更完整的取消/完成竞争与长期回收公平性。
3. EDA/CAD后端与完整Agent编排，模型身份/价格/预算登记后才开放真实调用。

没有推送、合并、发布、采购、烧录或付费模型请求。

## 最终验证与审阅记录

- `.venv/Scripts/python.exe -m pytest -q`，配置专用 `FPGA_TEST_POSTGRES_URL`：197 passed，1项已有Starlette/httpx依赖弃用警告，无跳过。包含9项真实PostgreSQL测试（本轮新增5项）和6项公开Icarus RTL测试。
- `npm.cmd --prefix apps/web run typecheck`、`npm.cmd --prefix apps/web test -- --run`、`npm.cmd --prefix apps/web run build`：类型检查通过，24项前端测试通过，生产构建通过；本轮未改前端行为，没有新增浏览器人工验收声明。
- 变更后端和新增测试的 Ruff 检查通过；`pip check` 无损坏依赖；`git diff --check` 通过；冻结来源179/179条SHA256一致。
- 冻结API方法/路径按占位参数等价核对，当前143/144已注册；`POST /projects/{project_id}/clarifications/answers`仍未注册。注册数量不等于实现完整度或84项P0通过。
- 独立只读审阅发现2项Important：验收complete与入库锁序交叉、重领等待后的身份授权竞态。均先补真实PostgreSQL失败回归，再修复，最终全套回归通过；未再次派复审。
- Final: minor (deferred): 首次并发查询相同的跨版本有效性投影可能因先查后插返回409；原始证据和权限边界不受影响，后续改upsert或投影键串行化。
- 审阅未判定真实EDA/CAD与保留测试充分性、生成代码隔离、84项P0、完整调度器、生产文件权限、pgvector/Alembic或本轮之外的全局锁序矩阵完成。上述工作保留NOT_RUN/明确阻断。

Ruling: 共享本机静态Worker兼容仅保留在未启用身份登记时；启用登记后必须逐进程配置，否则旧Worker会返回401。
Ruling: 没有隔离后端不自动排验收任务；本轮接口与事务验证不能作为真实工程验收。

本地分支保留、工作区路径不变；未推送或合并。测试用PostgreSQL实例在验证结束后停止，临时盘符映射移除。最终提交号见本次交付消息及分支git log。

## GitHub 集成授权记录

2026-10-29 用户明确要求将本轮上传到GitHub并与main合并。上传前重新获取远端，main仍为ef55699；再次执行完整后端和前端验证。本文件上节“未推送或合并”描述本地交付时状态，后续实际PR、检查及合并状态以GitHub仓库记录为准。
