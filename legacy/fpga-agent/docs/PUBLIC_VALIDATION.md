# 已有验证记录与适用范围

以下均为 2026-10-27 及以前的已封存记录，本次上传不重复执行模型、Repair、GPU 或 EDA。

|公开开发题|Baseline 完整通过|Agent 完整通过|
|---|---:|---:|
|signed_sat8|5/5|5/5|
|shift16|0/5|5/5|
|pipeline_mul3|5/5|5/5|
|fsm_overlap1101|0/5|5/5|
|fifo4|5/5|5/5|
|rr_arbiter4|5/5|5/5|
|crc8_serial|5/5|5/5|
|cascade_counter8|0/5|5/5|
|合计|25/40|40/40|

固定公共题集、同一模型/服务与独立 TB、Vivado 2026.1 下的历史结果：Agent 比 Baseline 高 37.5 个百分点。Agent 共 65 次模型调用、25 次 Repair、83720 Token，平均入口耗时 38.645525 秒；Baseline 40 次模型调用、18515 Token，平均 4.6617 秒。预算不相同，且题目参与过开发，不能推出未见题泛化或正式比赛总分。五次重复也不等于五道新题。

后续部署修订 f6cc142 的官方公开 popcount8 两组均独立 L3，通过三份公开参考解校准。累计 200 次串行 HTTP 请求无中途服务/模型重启、无生成重试；其中新增 198 次属于稳定性检查，没有逐个追加独立 judge。

最新实例外公网检查由 Windows 通过平台隧道实际完成 health、solve、jobs：单次模型调用、0 Repair、701 Token、27.875 秒。最终 RTL 摘要与既有公开 L3 候选一致；此请求未重跑独立验收。隧道地址与凭据不纳入仓库。

部署 CPU 最终 26 PASS，相关部署回归 20 PASS，外部串行控制器 4 PASS。更早算法测试组有重叠，不能累计冒充新测试。原始失败、完整模型/EDA 日志和评测资产保留在独立证据中，不复制给 Agent 或 RAG；来源摘要见 SOURCE_BINDING.json 和 DEVELOPMENT_SOURCE.json。
