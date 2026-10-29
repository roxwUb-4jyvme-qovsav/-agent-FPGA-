# 参数化公开采集核心

`capture_core.v` 实现双触发器同步、系统时钟使能、8/16 路采集、软件/上升沿触发、原子配置、掩码、4096 字深 RAM、capture_id 和读取准入。输入在下降沿改变，核心消费上升沿之前的第二级同步值。

运行公开测试：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/analyzer/test_rtl.py -q
```

测试自动查找 `.local/tools/iverilog/mingw64/bin` 或 PATH 中的 `iverilog` 和 `vvp`。测试分别编译 CHANNELS=8/16，并核对 tick、同步延迟、触发首样本、深度、掩码、冻结读取和 ABORT/复位优先级；另运行 UART、字节设备、MMIO 和真实串行顶层公开 TB。公开测试不是保留验收集。本机未登记仿真器时明确跳过为 NOT_RUN；Python 模型通过不能替代此层证据。

`uart_rx`/`uart_tx` 提供参数化 8N1；`frame_codec`/`frame_encoder` 处理有界帧、CRC、接收恢复与背压；`control_regs` 完成六命令、原子配置、响应缓存与 seq 冲突；`capture_device` 为字节级集成，`capture_top` 接串口。串口桥在 `cfg_accepted`/`arm_accepted` 后 ACK，READ_CAPTURE 在核心冻结数据上组块。

`capture_register_bus` 是独立的 32 位 ready/valid MMIO 适配器，按冻结 register-map 解码、拒绝保留位、只读写和未对齐地址。它尚未与 UART 路径整合为有仲裁的共同总线，不能无仲裁并联驱动核心。当前 UART 顶层使用原子控制端口。这是保留的规格差距。

本机便携 MSYS2 Icarus 13.0 已实际执行六项公开 TB 通过；下载来源与哈希见 `docs/analyzer-implementation.md`。尚未覆盖 UART 停止位/溢出故障注入、RTL CRC 损坏/半帧超时的专门 TB、完整 8/16 字节设备参数矩阵及保留验收。缓冲搬移、CRC 组合逻辑与缓存复制尚未做面积/时序优化。没有器件、管脚、CDC/时序约束、综合或布局布线证据，不能据此烧录。
