# Task 2 产品与独立证据实现记录

## 首轮软件快照（2026-10-29）

依据冻结规格第 11–14 章与 `packages/contracts/protocol` 的 15 条原始向量实施，未修改来源契约。Python 3.12.14，pytest 9.2.0，cryptography 50.0.2，rfc8785 0.1.4。

红测先执行：`python -m pytest tests/analyzer tests/acceptance -q --tb=short`，56 failed、2 skipped；56 项均因尚无产品与签名模块而失败。实现后 56 passed、2 skipped。新增跨版本复用检查先实际观察 1 failed/15 passed（未提供完整依赖指纹仍返回 PASS），修复后本任务达到 **60 passed、2 skipped**。两项跳过为 Icarus/vvp 缺失的 CHANNELS=8/16 公开 RTL 测试，状态为 NOT_RUN。

软件测试覆盖：15 条完整帧逐字节一致、CRC16 `123456789=29b1`、损坏 CRC、分片、同步噪声、超长候选与负载内同步字、半帧超时；8/16 路、divider=10/100、双触发器沿前值、软件/通道 15 上升沿、非 tick 短脉冲、掩码、深度终点、冻结读取、4096 样本分块、ABORT 与 reset 优先；重复 ARM、seq 冲突、可信语义错误缓存、ID 回绕与失效；CSV、二进制清单与离线 UART；签名篡改、未批准/撤销公钥、绑定、不可变入库与独立失效投影。

范围 Ruff 检查：`python -m ruff check products/analyzer acceptance_service tests/analyzer tests/acceptance`，All checks passed。

首轮全仓 `python -m pytest -q --tb=short` 实际失败于领域代理仍在开发的 `tests/domain/test_regressions.py`：导入 `packages.knowledge.index.transition_document` 失败。此结果不属于本任务范围，已通知主代理；不能据本任务局部通过宣称全仓通过。

## 公开 Python 接口

| 模块 | 接口与行为 |
|---|---|
| `products.analyzer.protocol` | `Frame(type, sequence, payload=b'', major=1, minor=0, flags=0)`，不可变；`encode_frame(Frame)->bytes`、`decode_frame(bytes)->Frame`、`crc16(bytes)->int`。物理错误抛 `FrameError`，不会生成 ERROR 响应。|
| 同上 | `FrameDecoder(inter_byte_timeout=0.1)`；`feed(bytes, now=None)->list[Frame]`、`expire(now=None)`、`buffered_bytes`。合法长度收满再验 CRC，错误候选从后一字节重同步，持久缓冲有界。|
| `products.analyzer.capture` | `CaptureConfig(trigger_mode=0, trigger_channel=0, divider=10, channel_mask=0xff, depth=4096)`；`CaptureEngine(channels=8, clock_hz=100000000)` 的初始掩码按位宽全开。|
| 同上 | `configure(config)`、`arm()->int`、`abort()->int`、`reset()`、`step(raw_inputs, *, arm=False, abort=False, reset=False)->bool`。step 推进一步系统上升沿，返回活动采样 tick；引擎 `mode='mock'`。|
| 同上 | `state`、`config`、`capture_id`、`sample_count`、`error_code`、`overflow`、只读 tuple `samples`；`read_capture(id,offset,count)->bytes` 返回小端原始样本。失败抛带整数 `code` 的 `CaptureError`。|
| 同上 | `handle_frame(Frame)->Frame`，只接受已通过 CRC 的 Frame；重放检查先于 BUSY，错误缓存遵循规范。原始串口字节必须先经 FrameDecoder。STATUS 可发 `Frame(5,seq)` 获取。|
| `products.analyzer.export` | `csv_text(samples,channels,divider,clock_hz)->str`，UTF8 CSV、样本零点、ch0→ch15；`export_bundle(samples,metadata)->dict[str,bytes]` 返回 metadata.json、samples.bin、SHA256SUMS，不负责存储/ACL。|
| 同上 | `decode_uart(samples,channel,sample_rate,baud=115200)->list[UARTFrame]`，仅空闲高 8N1、LSB 先行、位中心采样、停止位校验、最低 8 倍采样率。UARTFrame 字段 byte/start_sample/end_sample/frame_error/uncertain；缺尾帧 byte=None，保留不确定，不零填。|
| `acceptance_service.signing` | `canonical_bytes(value)->bytes` 使用成熟 RFC8785 JCS；`sign_evidence(dict,Ed25519PrivateKey,key_id)->dict` 深拷贝，移除整个 auth_tag 后签 JCS UTF8 字节；不创建或持久化生产私钥。|
| `acceptance_service.verification` | `ApprovedKey(issuer_id,key_id,Ed25519PublicKey,revoked_at=None)`；`verify_evidence(dict,keys,expected=None)->None`，无效抛 EvidenceError。批准公钥按 issuer/key 双键，保留撤销前历史证据。|
| 同上 | `evaluate_validity(dict,keys,*,current_revision_id,expected=None,reuse_approved=False,evaluated_at=None)->dict` 返回独立 EvidenceValidity；签名失败为 INDETERMINATE，依赖变更为 STALE，mock/replay 不可提升为独立验收 PASS。跨 revision 复用须明确批准并核对 input/suite/environment/tool_version/template_id/report_sha256。|
| 同上 | `EvidenceRegistry.register(...)/get(id)` 保留规范化不可变内容，同 ID 异内容拒绝，返回深拷贝。仅进程内边界辅助，不是数据库、ACL 或独立部署服务的替代品。|

签发与验签都验证冻结 VerificationEvidence/ArtifactRef Schema、uint32 seed、时间顺序与 PASS 的退出码。验签 expected 可绑定 task_id、revision_id、input_sha256、suite_sha256、environment_sha256、report_sha256 等事实；调用方必须传入任务数据库的真实期望值。签名证明来源与完整性，不能证明测试充分。

## 验证层级与限制

| 层级 | 首轮实际结果 |
|---|---|
| 物理帧/软件模型/导出/证据公开测试 | 60 PASS |
| Python 静态检查 | PASS |
| 公开 RTL 仿真（8/16） | NOT_RUN：仿真器未登记 |
| 独立保留测试及签发部署隔离 | NOT_RUN：未提供保留测试环境/密钥挂载边界 |
| 综合、布局布线、时序、CDC、厂商原语/XZ 检查 | NOT_RUN |
| 真实串口、电气/管脚、板卡与硬件 | NOT_RUN |

Python 引擎永远明确标为 mock，不把它当 RTL 仿真或板卡验收。本轮没有访问真实硬件、调用付费模型或执行用户生成程序。公开手写 RTL 测试仅验证受版本管理的固定核心与固定 TB，不开放任意生成代码运行入口。没有传感器的数据不可伪造。

首轮 RTL 已有参数化 `capture_core.v` 与公开 `capture_core_tb.sv`；UART、frame_codec、control_regs、capture_top 外壳尚缺，后续增量记录另列。本核心没有器件约束与板卡授权，不能烧录。

## 变更文件

- `products/analyzer/__init__.py`、`protocol.py`、`capture.py`、`commands.py`、`export.py`。
- `products/analyzer/rtl/capture_core.v`、`rtl/README.md`。
- `acceptance_service/__init__.py`、`validation.py`、`signing.py`、`verification.py`。
- `tests/analyzer/test_protocol.py`、`test_capture.py`、`test_export.py`、`test_rtl.py`、`rtl/capture_core_tb.sv`。
- `tests/acceptance/test_signing.py`、本报告。

修改前副本保存于 `.local/backups/analyzer/20261029-01`；该目录仅本机回滚用途，不提交凭据或生产密钥。

## 后续 RTL 增量

已按主代理要求完成参数化 UART RX/TX 与 capture/1.0 命令/寄存器/分帧外壳，在 `.local/tools` 放置 MSYS2 官方发行的便携仿真器，未系统安装。上面的首轮快照保留历史事实，**以下为最终交接状态**。

### 最终实际运行

- `python -m pytest tests/analyzer tests/acceptance -q --tb=short`：**77 passed，无跳过**。
- `python -m pytest -q --tb=short`：**138 passed，1 个 Starlette/httpx 依赖弃用警告**。此前领域收集错误已由相应开发者修复，本次完整回归实际通过。
- 本范围 `python -m ruff check products/analyzer acceptance_service tests/analyzer tests/acceptance`：All checks passed。
- 六项 Icarus/vvp 13.0 公开 TB 实际编译并执行：8/16 路 capture_core、UART 三已知字节与 RX 背压、16 路 capture_device 完整命令/重复 ARM/冲突/读取/ABORT、32 位 register_bus 对齐/只读/配置/背压、8 路 capture_top 的真实串行 GET_CAPS 与固定预期响应。

增量红测记录：UART/字节设备 TB 首次运行 2 failed/2 passed，失败为缺少 uart_rx/uart_tx/capture_device，补实现后四项通过；MMIO TB 首次因 capture_register_bus 缺失失败，补实现后通过。串行 TB 首次因测试夹具的预期 CRC 抄写错误失败（a9 ba 被误写 2b 5e），使用独立标准库 `binascii.crc_hqx` 校正夹具后通过，未据该失败声称修复产品缺陷。最初 Windows subprocess 无法按子进程 env 的 PATH 定位 exe，已改为显式工具路径；这属于测试启动环境修复。

新增 JSON 快照先实际运行 **11 failed**（snapshot/from_snapshot 尚不存在），实现后 **11 passed**。接口：`CaptureEngine.snapshot()->dict`、`CaptureEngine.from_snapshot(dict)->CaptureEngine`，实现于 `products/analyzer/snapshot.py`，包含 schema_version/mode/channels/clock_hz/config/state/id/error/overflow/有效性/samples/phase/两级同步/触发前值/带 CRC 的请求响应帧缓存。拒绝未知字段、非法位宽/计数/状态组合和损坏缓存，不使用 pickle。DONE 恢复可读取；恢复后重复 ARM 返回原响应，不重复增加 ID；返回容器不共享可变样本。

### 新增 RTL 文件和职责

| 文件 | 职责与限制 |
|---|---|
| `uart_rx.v`、`uart_tx.v` | 参数化 8N1、同步、位中心采样、停止位检测、字节背压、溢出脉冲；公开 TB 尚未覆盖故障注入。|
| `frame_codec.v`、`frame_encoder.v` | 最大 1024 字节、CRC 覆盖、合法长度先收全、单字节重同步、100 ms 半帧超时、响应流化及背压。错误帧不输出可信请求。|
| `control_regs.v` | 固定六条命令、配置影子验证、等待核心接受后 ACK、当前采集准入、容量限制、完整最近请求/响应缓存及 seq 冲突。|
| `capture_device.v`、`capture_top.v` | 字节级设备与 UART 串口顶层，均不绑定板卡或管脚。|
| `capture_register_bus.v` | 独立 32 位对齐 MMIO 适配、一次在途、只读/只写/保留位/地址拒绝和核心接受后响应；尚未与 UART 路径整合为共同有仲裁总线。|
| `tests/analyzer/rtl/*_tb.sv` | 新增 uart_tb、capture_device_tb、register_bus_tb、serial_top_tb；固定公开手写测试，不是用户生成代码执行入口。|

最终变更另含 `tests/analyzer/test_snapshot.py`、`products/analyzer/snapshot.py`；原 `test_rtl.py` 自动检测便携工具路径。修改前本机副本另存 `.local/backups/analyzer/20261029-rtl`。

### 仍未完成或未执行

- UART 顶层现走原子控制端口，MMIO 适配器独立测试通过；两者尚未整合为规格所述共同内部 ready/valid 总线与仲裁。
- 完整字节设备 TB 当前为 16 路，串口顶层 TB 为 8 路；尚无完整两位宽 × 所有命令 × 故障条件 RTL 矩阵。
- RTL CRC 翻位、超长候选、负载内同步字、半帧超时、UART 帧错/溢出等处理已有源码，但这些故障的专门 RTL TB 尚未执行；Python 相应物理协议测试通过不能替代 RTL 故障验证。
- 未做综合、布局布线、实际资源/时序、CDC、约束完整性、厂商原语和 X/Z 专项检查。CRC 组合循环、缓冲搬移、缓存复制尚未做面积与时序优化，不能声称满足目标 FPGA 的 PPA。
- 未执行独立保留测试；签发/公钥校验库不是已部署的隔离验收服务。需要主进程或部署层限制私钥、验证真实 task/revision/hash/report、完成数据库唯一性、不可变工件及授权。
- 无真实串口或硬件证据；没有调用付费服务或烧录。Python 采集引擎保持 `mock` 标签。

下一开发者先把 MMIO 与 UART 原子事务路径接入有仲裁总线，补上上述 RTL 故障和参数矩阵；再在隔离验收 Worker 冻结独立测试与签发密钥，取得真实目标器件后运行综合/时序/CDC，再完成硬件前提登记。公开 TB 通过只代表这些公开场景通过，不等于 84 项 P0 完成。

### 便携工具来源

从 [MSYS2 官方 mingw64 仓库](https://repo.msys2.org/mingw/mingw64/) 通过 HTTPS 下载并用 Windows tar 解压到 `.local/tools/iverilog`；未写系统安装或全局 PATH。测试仅为子进程设置依赖 DLL 路径。以下是实际下载包 SHA256（未另行验证仓库签名）：

```text
mingw-w64-x86_64-iverilog-1~13.0-2-any.pkg.tar.zst e22c86dad1a0658d5f769362bfbec6533028c0d0bb63059b4fda0cc1084d1406
mingw-w64-x86_64-bzip2-1.0.8-4-any.pkg.tar.zst 123768f30ae14ba654a6feb70f8526146a331bc85f831a91a549ffb3f6cbffc7
mingw-w64-x86_64-gcc-libs-16.2.0-6-any.pkg.tar.zst e65d2da59bb22c13eafc0fb7684b0539f63902bb94a550df1d64531a6c08363a
mingw-w64-x86_64-libwinpthread-14.0.0.r536.g7ce22f663-1-any.pkg.tar.zst 1e9ea9580b1b9121b4d1d646385424704acef1f6dba7161a49333c4c09dbb99d
mingw-w64-x86_64-readline-8.3.003-1-any.pkg.tar.zst b943d7e2a61ac6e0304eac11cdcee15370a8c2ccee5b84487f8ad6c1e103d66c
mingw-w64-x86_64-termcap-1.3.1-8-any.pkg.tar.zst 205ece965d693274f200ff6c15bca8d29a093860951aeb4dbb9660817c255cc0
mingw-w64-x86_64-zlib-1.3.2-3-any.pkg.tar.zst ffde6f4ed2d879477c93b37ff14c319baf480a2aeafc065edeceafba26b71e90
```
