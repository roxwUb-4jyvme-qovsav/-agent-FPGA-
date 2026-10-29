# FPGA 编程 Agent 源码审查与迁移评估

审查日期：2026 年 10 月 28 日。面向三人参赛团队。证据来自用户放入项目目录的 `fpga-agent-development-master.zip`，不是对无法访问的 Gitee 网页进行推测。本文是源码审查和实施建议，不是完成远程 API 改造的声明。

## 一 结论与适用边界

**可以作为编程 Agent 的 RTL 子内核，建议复用；可以更换为 API 模型，但不能仅修改 `base_url` 和模型名。** 需要把模型协议、预算核算及部署准入从旧本地比赛运行链中拆分。三人团队应先完成“需求规格 → 单模块 RTL → 独立仿真 → 诊断修复”的可验证闭环，再扩展多模块工程和其他产品阶段。

它不是从模糊自然语言直接得到可生产 FPGA 产品的完整系统。仓库没有硬件选型数据库、实时价格源、原理图 CAD 生成器、PCB 检查器、完整项目编排器或产品上位机。API 大模型也不会自动补齐这些能力。

本轮原样保存 67 个源文件，共 439978 字节。ZIP SHA256 为 `e8a04c9b12622518b50051b4d8d4101a3075d71772f95e35e521c9723cb7ca79`。逐文件字节与 ZIP 相同；包内当前 `FILE_SHA256SUMS` 的 66 项校验均相符。这是快照导入，不是保留原 Git 历史的完整仓库迁移。

## 二 实际运行链

当前主链为 `serve/submission.py` → `run.sh` → `agent/main.py` → `rtl_agent/feedback_agent_v24.py` → `rtl_agent/local_model.py`。模型返回代码后，执行候选提取、接口检查、编译、有限公共语义检查和综合，再在原预算内决定是否修复，最终保存 RTL、Trace 和账本。

可复用模块如下。

| 模块 | 已有能力 | 使用边界 |
|---|---|---|
| `local_model.py` 中候选处理函数 | 提取 RTL、限制危险语法 | 文本筛查不是完整代码沙箱 |
| `interface_contract.py` | 公开端口契约检查 | 不等同于系统级软硬件协议自动设计 |
| `feedback_agent_v24.py` | 多轮诊断与受限修复 | 与本地分词和 usage 校验耦合 |
| `candidate_tools_v24.py` | 候选编译、综合和诊断摘要 | 以 DUT 为范围，不是完整产品验证 |
| `runtime_audit.py`、`eda_evidence.py` | 时限、进程控制及执行证据 | 不是多租户安全隔离 |
| `serve/submission.py` | 有界队列、异步查询、nonce 去重 | 内存状态，串行求解，重启恢复需新增 |

主要生成入口围绕唯一 `TopModule` 和公开模块规格设计；不是通用多文件 Vivado 工程生成器。`rtl_agent/core.py:16` 把综合器件固定为 `xczu3eg-sbva484-1-e`，不能当作你们两块开发板的配置。顶层模块名、器件、时钟、引脚和约束应由新项目配置明确提供。

## 三 API 迁移的关键阻断点

### 1 地址和传输被明确限制为本地

`agent/main.py:41–43` 只接受 loopback HTTP 的 `/v1` 地址；`rtl_agent/local_model.py:37–43` 和 `54–84` 再次约束本地、无认证 URL。`_json_request` 使用本地 HTTP 客户端，没有可直接套用的远端 HTTPS 和 API 鉴权逻辑。

这是原来的安全边界，不是偶然漏配。建议新增独立远程 Provider，使用 HTTPS、受控目的地址和后端环境密钥，不要全局放开旧 URL 校验，也不要允许用户从提示词指定任意代理目标。

### 2 强依赖 vLLM 分词接口

`rtl_agent/local_model.py:171–216` 调用 `/tokenize`，要求完整 tokens、count 和 max_model_len。`feedback_agent_v24.py:325–340` 在调用前依赖这个结果做精确容量准入；`356–359` 要求响应的 prompt token 数与本地结果严格相等。

远程 API 不保证提供相同接口、分词器或字段。因此，不能伪造 `/tokenize` 返回值来让检查通过。新模式须区分预估 token、服务商支持的计数能力和实际 usage；无法精确计数时明确记录估算状态，用保守容量余量与实际消耗账本控制预算。旧比赛模式的精确规则保持不变。

### 3 请求和返回格式不是完全通用的

`local_model.py:219–261` 使用 Chat Completions 请求、固定采样参数和 `choices[0].message.content`，并严格检查响应模型名。`agent/main.py:113–120` 还要求可用模型列表恰好只有配置的一个模型，并执行原 Baseline 准入。

新模型可能不接受原有参数组合，可能返回模型快照名，也可能使用 Responses 的 output items、input/output usage 和不同完成状态。需要按服务商协议适配，不能仅依赖“OpenAI 兼容”四个字。截断、拒绝、空文本、工具调用、usage 缺失和超时都必须分别处理。

### 4 本地 GPU 与比赛部署条件仍会挡住远程模式

`serve/deployment_profile.py`、`serve/start.sh`、`serve/startup.py`、`Dockerfile` 绑定本地模型、AMD GPU 显存测量和原比赛环境。若仍走旧健康检查，远端 API 可用也可能被判未就绪。

远程模式不应要求本地推理 GPU，但仍需要能运行 EDA 的本地或受控 Worker。云端 LLM 不会替你执行电脑上的 Vivado。旧许可服务器地址是原环境配置，不是你们已有的有效许可证明。

## 四 需要优先管理的质量风险

**验证标签不能混用。** `candidate_tools_v24.py:43–69` 是 DUT 编译和展开（elaboration）；综合通过不代表功能正确。公共语义探针只覆盖有限结构，部分分支还绑定指定公开规格的哈希，不是任意 RTL 的通用证明器。`INDETERMINATE` 必须保留，不能显示为绿色通过。

**正常退出不等于任务成功。** `agent/main.py:203–219` 的异常路径会写空结果并按原协议返回零退出码，`run.sh` 也保留这种封装语义。新业务后端必须检查结构化任务结果、代码可用性及验证证据，不能只看 HTTP 200、job done 或 rc=0。

**尚不适合直接作为公网多人服务。** 现有队列是有界内存队列，求解串行，缺少账户体系、租户隔离、重启恢复和公开取消 API。应新增业务后端与持久化任务表，EDA 使用独立工作目录和最小权限运行；生成代码不应读取主机密钥或执行任意 shell。

**历史成绩只属于原实验。** 原文的 40/40 等记录是仓库自带历史结果，且原文明确不等预算；本轮没有复现。换模型、Prompt、预算、器件或工具版本后必须重新评估，不能沿用这些成绩。

## 五 本轮真实测试结果

测试命令在未改动的开发快照中执行，Python 启用 UTF8 模式，不启动模型或 EDA。

| 检查 | 本轮结果 | 含义 |
|---|---|---|
| `python -X utf8 -B tools/check_package.py` | 通过 | 当前 47 个运行哈希与 34 个 Python 语法检查通过 |
| 原部署定向 unittest | 26 项中 23 通过 3 失败 | 不能宣称全部测试通过 |
| 使用实际 Git Bash 的补充 profile 检查 | 默认值与覆盖值两项通过 | 仅排查路径问题，不替代原测试结果 |
| 真实 API 和模型推理 | 未运行 | 未验证 Key、权限、网络、质量、延迟或费用 |
| Vivado 仿真综合及上板 | 未运行 | 未证明 RTL 功能和硬件行为 |

三项失败的原因如下。

1. `tests/test_deployment_revision.py:129–130` 把 Git Bash 写死为 `C:/Program Files/Git/bin/bash.exe`，本机安装在 D 盘，导致两项失败。补充检查使用实际安装路径且未修改源测试，两种 profile 断言通过。正式修复应采用可配置路径或 PATH 发现，并维护 Windows/Linux 测试。
2. `tests/test_deployment_revision.py:155–160` 按历史绑定检查 `skill/README.md`，历史 SHA256 为 `01d6d197…f9e444`，当前为 `0c64708d…4bf0f2`。当前内容是中文开发说明，当前文件清单也记录新哈希；实际注入的 `rtl_generation_v4.md` 及当前运行锁没有因此失配。判断为文档更新与历史断言边界未协调，不是直接证据表明运行 Prompt 被改。本轮保留原文件和失败记录，不回填历史哈希掩盖问题。

在 Windows 默认 GBK 下，不加 `-X utf8` 还会出现两项文本解码错误。统一明确 UTF8 是应修复的跨平台问题，不能通过删除中文文档解决。

证据保存在 `docs/audit_evidence/` 和 `docs/IMPORT_AUDIT_2026-10-28.json`。有限静态模式扫描未命中常见密钥、私钥和令牌格式；这不构成对不存在任何秘密的形式证明。本轮未上传模型权重、ZIP 或本地 API Key。

## 六 模型和工具接入建议

不建议给每个角色训练或部署一个模型。先选择一个可配置的主模型，难例才升级，另设独立测试而不是只让另一个模型说“通过”。

按本次核实的官方 OpenAI 文档，可将 `gpt-6.1-sol` 作为编码和修复的候选主模型，将 `gpt-6-astra` 用于困难需求、架构复核和疑难诊断。官方定位是通用能力与成本取舍，**并不能证明对本项目 RTL 的通过率一定高于原 7B 模型**；应以独立测试集、费用和时延实测决定。模型可用性取决于账号权限，具体单价以部署当日价目和账户为准。

如采用该路线，建议使用 Responses API；其请求参数、文本提取和 usage 映射与旧实现不同，需要明确适配。其他服务商也可接入相同 Provider 接口，但必须分别测试，不能承诺直接互换。

**换模型不要求 MCP。** 模型调用和工具调用是两层。第一版可由 Python 函数或受控子进程包装编译、仿真和日志读取；当多个 Agent 需要复用统一的工具发现与调用协议时，再封装 MCP。MCP 不是仿真器、数据库、安全沙箱或验证正确性的保证。

## 七 三人团队的实施顺序

先完成 API Provider 和单模块回归，再接需求澄清及界面，最后扩展板卡配置、多文件工程和硬件工具。详细接口、错误处理和验收建议见 `API_MIGRATION_PROPOSAL.md` 与 `TEAM_WORKFLOW.md`。

当前尚待确认：模型服务商和账号可用性、单任务及整场预算、EDA 机器和许可、两块板卡型号、比赛是否允许远程推理。没有这些信息也能完成源码审查和快照导入，但不能声称部署已跑通。

## 八 资料来源

- 本地 ZIP 内源码及随附文档，文件与行号均针对本次原始快照。
- [原 Gitee 仓库](https://gitee.com/AGSujhajusgh/fpga-agent-development)：用户指定来源，网页不可读，实际证据使用本地 ZIP。
- [团队 GitHub 仓库](https://github.com/roxwUb-4jyvme-qovsav/-agent-FPGA-)：用户指定目标。
- [OpenAI 模型和迁移指南](https://developers.openai.com/api/docs/guides/latest-model)，核实日期 2026 年 10 月 28 日。
- [OpenAI Responses 迁移指南](https://developers.openai.com/api/docs/guides/migrate-to-responses)，核实日期 2026 年 10 月 28 日。
