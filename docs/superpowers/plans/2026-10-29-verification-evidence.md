# 独立验收服务身份与证据接入实施计划

> **For agentic workers:** 使用 executing-plans，在当前会话逐任务实现并验证；完成后独立只读审阅。

**Goal:** 接续 WORKER-HANDOFF 的第一项，完成身份隔离、签发证据持久化和当前版本有效性查询。

**Architecture:** 沿用 FastAPI、现有租约/工件/资源表以及 Ed25519 JCS 库。只读本机注册文件定义每个服务的身份、能力和项目范围；验收期望由可信调度侧单独登记，不接受提交方自报。签名原文不可变，有效性按当前任务依赖重新计算。

**Tech Stack:** Python 3.12+、SQLAlchemy、pytest、React TypeScript（回归）。

**Spec:** ../../WORKER-HANDOFF.md；../../reference/development-spec/FPGA多Agent协同开发平台开发规格书_v1.0.md 第6/9/16节；冻结 VerificationEvidence/EvidenceValidity/ActionResult。

## Global Constraints

- 只在 fpga_platform 开发，独立分支 codex/verification-evidence，修改前备份到 .local/backups，原子替换。
- 84项P0范围保留，未执行为 NOT_RUN；scheduler_ready 保持 false。
- 不改冻结契约；私钥只存在于独立验证服务，API只读公钥。
- 兼容旧本机静态 Worker，权限限 contract.validate；配置身份注册表后关闭共享令牌回退。
- 无隔离验收后端，不自动创建验收任务，不运行生成代码，不调用付费服务，不推送远端。

## Review Focus

- 自报其他 worker_id、能力、项目、持有其他任务三元组不能取得保留测试或上传报告。
- 注册撤销或公钥撤销后，幂等命中也重新鉴权；未登记环境明确阻断。
- 同一证据ID不同内容、跨项目报告、旧fence报告、篡改字节和签名不能入库。
- 入库与取消/租约过期互斥，mock/replay不能产生有效独立PASS。
- 签名原文永不被有效性投影或重复请求改写，部分测试PASS不提升完整工程状态。

## Task 1 服务身份

**Files:** apps/api/services/service_identity.py；apps/api/config.py；routers/jobs.py、worker_artifacts.py；tests/api/test_service_identity.py。

**Interfaces:** worker_auth(db,request,write=False) 返回 ServicePrincipal；authorize_task(principal,task) 限制角色、worker_id、项目和能力。

- [x] 先测注册身份不能伪装、跨项目领取/读取或借用另一lease；禁用后重放也被拒。
- [x] 执行失败测试，再实现只读JSON身份登记和每次请求的授权。
- [x] 原静态Worker API/CLI回归通过。

## Task 2 证据入库与投影

**Files:** apps/api/services/verification.py；routers/verification.py；apps/api/main.py；worker_artifacts.py；tests/api/test_verification.py。

**Interfaces:** register_assignment(db,task,expected) 为未来可信调度器登记固定验收预期；POST /verification/evidence 使用租约请求头和 verifier身份；GET /projects/{project_id}/evidence/{resource_id}/validity 返回冻结Schema。

- [x] 先测签名证据持久化、报告受控上传、ACL、依赖失效、ID冲突、密钥撤销、取消及旧fence。
- [x] 观察失败后实现公钥读取、完整绑定、不可变入库、事件和独立投影。
- [x] 验证Run/Revision不因单条PASS升级，普通complete不能绕过验签。

## Task 3 回归与交接

- [x] 完整pytest、前端typecheck/test/build、Ruff、pip check及冻结来源哈希。
- [x] 独立只读审阅；重要问题用红绿测试修复。
- [x] 更新交接、下一任务和真实验证结果，提交本地快照。

## 实施裁定

用户已明确要求按现有开发/交接文档继续开发且无需第二次确认，沿用既定规格实施。保留既有工作目录、使用独立分支；本轮不额外创建worktree。
