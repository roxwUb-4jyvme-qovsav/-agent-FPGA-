# FPGA RTL Agent 队内二次开发仓库

本仓库供队员开发前端、可视化和应用接口，**不是比赛提交仓库，也不会自动部署到 AMD 实例**。初始运行源码来自已验证提交 `f6cc14271ebd54280d5de538ae540b06e882122d`；采用 Agent v2.4、冻结 Skill v4、最终 RL 版 CodeV-R1-RL-Qwen-7B 和原样官方 Baseline。

## 从哪里开始

- [前端接入指南](docs/FRONTEND_INTEGRATION.md)：鉴权、异步任务、轮询、状态展示与错误处理。
- [真实架构](docs/ARCHITECTURE.md)：模型、Agent、工具、评测边界和文件位置。
- [部署说明](serve/README.md)：Linux 环境及服务启动方法。
- [模型说明](model/MODEL.md)：模型来源、固定版本和预算。
- [测试说明](docs/TESTING.md)、[已知限制](docs/KNOWN_LIMITATIONS.md)、[历史验证](docs/PUBLIC_VALIDATION.md)。

## 目录

|目录/文件|用途|
|---|---|
|agent/、rtl_agent/|正式入口、候选提取、诊断、Repair、预算与 EDA 留证|
|serve/|模型启动、HTTP 服务、异步任务与资源准入|
|configs/|原有 Agent 配置|
|skill/|冻结的生成指导；运行 Prompt 保持原始字节|
|model/|模型声明和摘要锁，不含权重|
|tests/、tools/|已有部署定向测试与无模型包检查|
|run.sh、run_baseline.sh、baseline.py|单题入口及原样官方 Baseline|
|docs/|中文接入、开发与验证说明|

## 最小本地检查

```bash
python3 -B tools/check_package.py
```

此命令只读包内文件，不调用模型或 EDA。真正运行 Agent 需要 Linux、已部署的本地模型及 Vivado；Windows 可以编辑源码和运行适用的 CPU 检查，不能据此认定完整 EDA 链路可运行。

模型及依赖准备好后，运维人员才可启动服务：

```bash
export MODEL_PATH=/绝对路径/已预置模型权重
bash serve/start.sh
# 在环境中安全提供 FPGACHINA_TOKEN，勿放入前端或 Git
bash serve/http.sh
```

上述命令有启动模型/服务的副作用，本次上传没有执行。已有服务不要重复启动。前端建议通过自己后端代理接入，不能将鉴权令牌编译进浏览器。

## 开发原则

先从新分支开发前端；运行内核、Skill 或配置若要调整，应单独评审和测试。不得把二次开发仓库的改动自动同步回比赛仓库或在比赛实例上热更新。先前公开开发集 Agent 40/40、Baseline 25/40 完整通过属于历史证据，不是本次上传的新成绩。仓库不包含模型权重、令牌、独立评测 TB、参考答案或原始大日志。

所有面向开发者的说明采用中文；代码标识、API 字段、许可证原文和实际注入模型的冻结 Skill 保留原文，以维持兼容与授权条款。没有为整个工程另行指定新许可证，详见 THIRD_PARTY_NOTICES.md。
