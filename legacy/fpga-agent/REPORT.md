# 队内开发交接说明

这是面向二次开发的说明，不是最终比赛报告。实际运行核心来自提交 f6cc14271ebd54280d5de538ae540b06e882122d。

Agent 通过公开规格、冻结 Skill、真实分词容量与累计预算检查调用本地 CodeV 模型，严格提取单一 TopModule，执行接口、静态、编译、有限公共语义检查和综合，再按既有预算生成 Repair 或终止。候选、原始响应、Trace、ledger 与 EDA 留证构成复核链。独立测试台及参考实现始终在评测侧。

已合入的通用改进包括声明与驱动诊断、动态普通 part-select、旋转边界/位宽风险、饱和运算、流水线、轮询仲裁与序列时序的有限公共性质反馈。部分结构无法可靠判定，必须弃权，不得把非空 RTL 或综合 PASS 当作功能正确。

本次只为队员建立独立源码快照和中文文档；运行代码、官方 Baseline、配置与冻结 Skill 的字节保持来源一致。没有调用模型、Repair、EDA，也没有修改比赛仓库和现有服务。前端接入应从 docs/FRONTEND_INTEGRATION.md 开始，开发与比赛环境严格分离。

历史公开结果及局限见 docs/PUBLIC_VALIDATION.md、docs/KNOWN_LIMITATIONS.md。项目没有为所有第三方文件统一重新授予许可证；按 THIRD_PARTY_NOTICES.md 保留原授权。
