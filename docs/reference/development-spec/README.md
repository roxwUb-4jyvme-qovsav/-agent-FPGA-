# 开发规格书交付包

主文档为同目录的 `FPGA多Agent协同开发平台开发规格书_v1.0.docx`，Markdown 为可维护正文。本包细化现有总体设计，不包含已实现的平台，也不声称真实模型、EDA、KiCad 或供应商接入已经验收。

| 目录或文件 | 用途 |
|---|---|
| FPGA多Agent协同开发平台开发规格书_v1.0.md | 25 章开发规格，包括行为、状态、协议、页面、权限和发布条件 |
| api/openapi.json | OpenAPI 3.1 请求响应与鉴权契约 |
| api/resource-catalog.json | 各资源的可修改范围及允许动作 |
| contracts | JSON Schema 2020-12，使用可离线解析的 urn 标识 |
| protocol | 寄存器、错误码与 capture/1.0 完整二进制测试向量 |
| examples | 未批准的 8/16 通道契约、虚拟板及扩展与报价夹具 |
| acceptance/catalog.json | 84 项 P0 验收计划，实际执行状态尚未记录 |
| planning/backlog.json | 7 个工作包和 28 个可分工任务，29 到 49 编码人日 |
| sdk | 扩展调用规则及无外网的参考检索适配器 |
| tools | 交付物生成与离线一致性检查脚本 |
| 内部校验 | 版本备份、渲染和规格校验记录，不作为平台运行证据 |

建议先读正文第 1 至 7 章，A 再读模型、管理、知识、前端和运维；B 再读采集协议、验证、图纸与报价。双方联调以同一版本契约为准。不得用示例中的测试报价、零哈希、example.invalid 或未批准参数代替真实来源。

修改 Schema 时同步更新正文、示例、调用方与验收。运行 `tools/build_contracts.py` 重新生成契约，`tools/build_package_data.py` 更新验收与工作包。校验入口为 `tools/validate_spec.py`，要求 jsonschema 和 openapi-spec-validator；它只检查规格包，不运行产品验收。

本轮未安装产品依赖、调用付费模型、推送仓库或执行 FPGA/CAD 工程。Word 排版与规格一致性校验单独记录，不据此改变 84 项产品验收计划中的 NOT_RUN。

完整交付压缩包包含 Word、Markdown、契约、协议、示例、验收计划、工作包、SDK 和工具，不包含本地的内部校验目录。package-manifest.json 记录包内文件的 SHA256，清单本身不参与自哈希。
