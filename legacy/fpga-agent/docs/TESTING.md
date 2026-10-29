# 测试与检查

先运行 `python3 -B tools/check_package.py`：核对当前来源锁、配置和 Python 语法，不启动模型、网络或 EDA。该检查通过只说明包内一致性。

已有部署定向测试入口为 `python3 -B -m unittest discover -s tests -p test_deployment_revision.py -v`，其中包含 Windows/Linux 与环境前提，先读夹具再运行；不要为了通过而跳过断言。CPU/MOCK 不能证明真实模型生成或 RTL 功能。

前端开发可以使用显式标注的模拟 API 返回检查界面流程，但模拟服务必须单独实现并显著标记，不替换真实服务状态。正式集成测试先取得独立开发资源和调用授权，单独记录请求次数、Token、耗时、响应和错误；不要向比赛实例做无界重试或压力测试。

真实候选按固定独立评测协议先 L1 编译/详细描述，再 L2 固定 TB 功能比对，再 L3 综合；前级失败停止后级。没有 TB/工具/许可时标记 NOT_RUN 或 BLOCKED，不根据退出码 0 自动造成功记录。本仓库不包含独立 TB，需由评测侧另行提供。
