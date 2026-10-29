# 模型与 HTTP 服务部署

仅由运维人员在独立开发环境操作。先确认没有共享任务或已有服务，禁止照抄命令启动第二份模型。Linux 为默认运行环境，完整 EDA 使用 Vivado 2026.1；Windows 文件存在不等于 Linux 工具可用。

1. 预置 model/MODEL.md 指定版本的权重及模型许可证，不在运行时下载。
2. 设置 MODEL_PATH；serve/profile.sh 提供别名 codev-r1-7b、本地 8002 端口、32768 上下文、BF16、单卡、预留 0.55 等既有配置。
3. 配置 AMD GPU、ROCm、目标器件 xczu3eg-sbva484-1-e、许可及可写本地 EDA_TMP。GPU 物理计数器通过 render-node PCI 映射核对，不凭 HIP 索引猜卡。
4. 执行 bash serve/start.sh：启动或检查常驻推理，全部就绪后返回。该入口不下载权重、不生成令牌、不启动 HTTP。
5. 私下配置 FPGACHINA_TOKEN 后，由独立进程启动 bash serve/http.sh。模型端口不向公网暴露。

直接单题运行前，按需在调用 shell 加载 serve/profile.sh，再运行 run.sh；这会实际调用模型和 EDA。本次开发仓库上传没有启动任何这些命令。

HTTP 绑定 127.0.0.1:7860。开发前端通过受控业务后端代理接入，详见 docs/FRONTEND_INTEGRATION.md。平台隧道是部署操作，不属于单题求解路径；地址会失效或变化，不在仓库硬编码实际网址和凭据。

任务采用独立进程组、出生时间/UID 绑定和子进程回收。运行小证据放 AGENT_EVIDENCE_DIR，EDA 工作现场放可写的本地 EDA_TMP。服务不自动重启，不具备任务重启恢复能力；排队时间也消耗请求时限。当前任务与 nonce 记录有容量限制，见前端接入指南。不要删除活动任务目录或共享历史证据。
