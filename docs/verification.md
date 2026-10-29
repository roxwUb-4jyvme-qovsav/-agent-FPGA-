# 交接验证记录（2026-10-29）

这是开发快照的验证记录，不是完整首版或全部 84 项 P0 的验收报告。未完成工作及接手顺序见 [HANDOFF.md](HANDOFF.md)。

## 实际执行结果

| 检查 | 结果与边界 |
| --- | --- |
| Python 全套 `python -m pytest -q` | 最终 143 passed，1 项 Starlette/httpx 依赖弃用警告；无跳过。 |
| 前端 `npm run typecheck` | 通过。 |
| 前端 `npm test` | 15 passed，4 个测试文件。 |
| 前端 `npm run build` | 通过。 |
| 依赖 `python -m pip check` | 无依赖冲突。 |
| 公开 RTL TB | 6 项通过；本机实际使用 Icarus 13.0，不是模拟通过或跳过。来源见 analyzer-implementation.md。 |
| 冻结参考字节 | source-lock 的 179/179 条与原始来源、工作区及 Git index 的 SHA256 一致。 |
| 契约样例 | 9/9 个样例字节一致且 Schema 校验通过。 |

Ruff 最终执行 `python -m ruff check apps/api packages products acceptance_service workers tests` 通过。后续修改必须重新执行与变更相关的检查。其他机器无 Icarus 时，RTL 测试会跳过，不能沿用本机通过数字。

实际浏览器通过 Vite 连接 FastAPI：首次初始化、创建项目、16 路样例导入、批准后冻结到 FROZEN v3、零预算运行排队及 mock 会话 OPEN。设备命令/采集 API 返回 404，SSE 未接通，均如实显示。详情见 [前端实施记录](web-implementation.md)。

## 交接前审阅修复

独立审阅发现并已增加红绿回归的三项问题：

1. 幂等响应缓存命中前重新验证当前项目/管理权限，撤销成员后不能用旧 key 读取原响应。
2. collection 的 ACL 继承到来源文档、原始工件列表/详情和下载，不能绕过检索直接下载私有原文。
3. 静态 Worker 的 passed 结果必须为零退出码、匹配冻结输入摘要，log_ref 也校验任务和项目归属。

三项原始复现场景已经独立复核闭环。审阅与回归测试不替代完整权限矩阵、PostgreSQL 并发、独立验收或实板验证。

复核同时发现无 collection 的 task_only 文档被隐藏；已补上传者可读、同项目其他成员不可读及错误上传归属/删除状态回归，先观察 2 项失败再修复。最终全套从 141 增至 143 项通过。

## 尚未执行

PostgreSQL/pgvector 部署与并发、Alembic 迁移、完整 144 操作逐项验收、常驻 Worker、SSE/outbox、设备 API 完整闭环、真实模型/外部工具与沙箱、完整 RTL 参数/故障矩阵、综合/CDC/时序、KiCad/ERC、隔离保留验收及实板测试均不在通过声明中。

## GitHub 上传核对

初次推送因账号 sch9999 缺少目标仓库写权限返回 403。用户完成授权后，已成功推送到指定仓库 main；首次上传提交 `5012f21dffcf87e051c7034788c9efeeb10c6399`，已通过 ls-remote 核对远端与本地 SHA 一致。后续补充提交以仓库 main 的最新提交及 Git 历史为准。
