# 冻结 Skill v4

rtl_generation_v4.md 是实际注入模型的运行输入，保持原始字节与语言，SHA256 为 d7cb45dae750ee0045fcff6343d7b14e15cb6c71836a93679c5c175b634c5ee2。

它提供公开 RTL 规格的生成指导；Agent 读取它，官方 Baseline 不读取。它不是参考答案数据库，不允许使用隐藏 TB、Oracle 或未公开题目调优。有限公共语义检查无法判断时须保留 INDETERMINATE。

本次仅将开发说明改为中文，不翻译运行 Skill，以免改变已有验证对应的 Prompt。若将来要调整 Skill，须作为独立算法改动重新验证。
