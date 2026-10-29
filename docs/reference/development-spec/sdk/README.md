# 扩展适配器接口

engine_api 为 1.0。插件与后端运行在不同进程或受控 HTTP 服务中。登记清单、内容校验、权限审批及项目绑定先于调用；进程启动命令只能来自受管包配置，模型不能传任意 Shell。

## 调用信封

输入验证 `contracts/AdapterRequest.schema.json`。input_schema 指向已登记 Schema 的 urn，input 必须通过该 Schema 的额外校验。通用 input 对象允许不同适配类型，但不免除网关的第二阶段结构及授权校验。输出同理：AdapterResponse.output_schema 绑定实际输出 Schema，输出对象再校验一次；不允许插件自行注册 Schema 来绕过受控能力。

网关从认证身份产生 project_id、revision_id、invocation_id 和批准范围，插件不能扩大它们。deadline 为绝对时间；stdout 是 UTF8 单行 JSON，每条请求一条响应，诊断只写 stderr。单消息上限建议 1 MiB，超时或非法输出结束该调用，错误信息必须脱敏。取消通过受管进程组终止或已登记 cancel 方法，不暴露任意进程 ID。

| 类型 | operation | input_schema | output_schema |
|---|---|---|---|
| KnowledgeProvider | search | SearchRequest | SearchResponse |
| ModelProvider | generate | ModelRequest | ModelResponse |
| QuoteProvider | lookup | QuoteLookup | QuoteOffers |
| ToolProvider | 已批准语义工具名 | 工具独立参数 Schema | ToolResult |

ModelRequest 传递版本化配置、消息、受控工件引用、输出 Schema、输出上限、绝对截止时间和预算预留。QuoteLookup 按完整制造商、MPN、封装、数量、地区和币种查询。各供应商私有参数放在已校验连接配置内，不进入任意工具参数。平台的 ModelProfile、QuoteOffer 和 ToolResult 定义持久化结果边界。

## 参考样例

`examples/knowledge_stdio.py` 接受上述信封并返回一条明确标为夹具的 Evidence，数据来自内置常量，不访问网络、磁盘或真实知识库。样例只演示独立进程消息边界。真实扩展必须从网关获得允许的 collection_ids，复核 ACL，返回来源定位，并受超时与大小限制。

包结构建议为 manifest.json、config.schema.json 和 payload/。payload_sha256 计算独立 payload 压缩包的原始字节，不覆盖外置 manifest，避免自哈希。签名验证算法、密钥 ID 和可信发布者策略属于安装器固定配置，不能由包选择关闭校验。空 signature 显示未签名，不代表可信。

升级生成新安装版本，新的权限必须重新批准。历史运行绑定旧版本。不可逆迁移禁止仅切回文件；先建立备份与恢复策略。禁用阻止新调用，撤信任同时触发在途任务风险处理，卸载默认保留历史工件。

## 合规检查

至少测试正确结果、无答案、权限拒绝、非法字段、响应超限、超时、限流、撤销、损坏包、依赖循环、版本不兼容、卸载后调用以及配置恢复。新成员依据此接口编写适配器后，不修改核心即可登记；这项能力仍需由产品实现并验收。
