# 适配器边界

`PluginManifest` 是本项目格式。HTTP 层首先使用 `packages.contracts.validation.validate_payload` 验证清单；随后调用 `extensions.validate_package` 验证独立 payload 的 SHA256、显式信任、来源和精确依赖锁。此函数只返回 VALIDATED，不安装或执行任何脚本。

适配器运行在独立服务或隔离进程，通过冻结的 AdapterRequest / AdapterResponse 交换数据。宿主先用 `ToolPolicy.authorize(request, context)` 验证项目、版本、登记能力、当前信任与批准，随后调用服务。响应交给 `sdk.validate_adapter_response`，其 `output_validator` 可以使用冻结 Schema 校验器。返回 completed 不代表独立验收 PASS。

最小服务响应：

```json
{
  "schema_version": "1.0",
  "invocation_id": "4f27667a-b6e9-4827-b2a4-249790ca1530",
  "status": "completed",
  "output_schema": "QuoteOffers",
  "output": {"schema_version": "1.0", "offers": []},
  "diagnostics": [],
  "request_id": null
}
```

`invocation_id` 必须原样回传。status 仅可为 completed / failed / timeout / denied；completed 必须有通过指定 Schema 的 output。超时、限流或未知消费需要调用层保留状态并先对账，不重复产生外部副作用。插件提交的 admin、角色或额外能力一律不作为授权依据。

`ControlledMCPClient` 支持 MCP 2025-03-26 的 HTTP JSON 初始化、通知、工具发现和受控工具调用，配置锁定工具 Schema SHA256。它拒绝重定向、Schema 漂移、未登记工具和非法输入。SSE、分页发现以及受管 stdio 当前明确阻断，不能宣称完整 MCP 传输兼容。`disconnect` 只清除本平台连接。

新增适配器无需修改业务模块：提供 AdapterRequest 服务，登记精确版本和哈希、输出 Schema、能力绑定、允许端点、凭证引用、数据政策，运行 SDK/网关测试后再申请批准。默认不加载任意 Python/npm 包到 API 进程。
