# Pinned organizer gfx1100 / W7900 base; built by the organizer if needed.
ARG BASE_IMAGE=fpgachina-rtlhls@sha256:a7cf627e11708e8a6dcd9de689ff934f7b450b79369ebc7bb0217e8b54d81e56
FROM ${BASE_IMAGE}
WORKDIR /opt/rtl-agent
# Explicit whitelist. No COPY . and no development data enters the image.
COPY agent/ agent/
COPY rtl_agent/ rtl_agent/
COPY skill/ skill/
COPY serve/ serve/
COPY configs/ configs/
COPY model/ model/
COPY run.sh run_baseline.sh baseline.py manifest.json REPORT.md ./
RUN chmod +x run.sh run_baseline.sh serve/vllm.sh serve/start.sh serve/http.sh
ENV MODEL_NAME=codev-r1-7b \
    LLM_MODEL=codev-r1-7b \
    LLM_BASE_URL=http://127.0.0.1:8002/v1 \
    MODEL_PATH=/opt/rtl-agent/model/weights \
    AGENT_CONTEXT_TOKENS=32768 \
    HIP_VISIBLE_DEVICES=0 \
    GPU_MEMORY_UTILIZATION=0.55 \
    AGENT_TMP=/var/tmp \
    EDA_TMP=/var/tmp/eda \
    AGENT_REQUIRE_VIVADO_VERSION=2026.1 \
    AGENT_REQUIRE_VRAM=1 \
    XILINXD_LICENSE_FILE=2100@vivado-lic.fpgachina.svc.cluster.local \
    FLEXLM_TIMEOUT=300000000 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    VLLM_NO_USAGE_STATS=1 \
    VLLM_DO_NOT_TRACK=1 \
    DO_NOT_TRACK=1
EXPOSE 7860 8002
CMD ["/bin/bash"]
