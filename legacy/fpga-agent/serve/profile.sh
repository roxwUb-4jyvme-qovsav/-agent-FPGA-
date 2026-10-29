#!/usr/bin/env bash
# Non-secret defaults shared by both deployment entrypoints. Never stores tokens.
export MODEL_NAME="${MODEL_NAME:-codev-r1-7b}"
export LLM_MODEL="${LLM_MODEL:-$MODEL_NAME}"
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8002/v1}"
export AGENT_CONTEXT_TOKENS="${AGENT_CONTEXT_TOKENS:-32768}"
export MODEL_PATH="${MODEL_PATH:-/opt/rtl-agent/model/weights}"
export HIP_VISIBLE_DEVICES="${HIP_VISIBLE_DEVICES:-0}"
export AGENT_REQUIRE_VRAM="${AGENT_REQUIRE_VRAM:-1}"
export EDA_TMP="${EDA_TMP:-/var/tmp/eda}"
export AGENT_TMP="${AGENT_TMP:-/var/tmp}"
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

# Runtime privacy; does not change model, sampling or Agent budgets.
export HF_HUB_DISABLE_TELEMETRY=1
export VLLM_NO_USAGE_STATS=1
export VLLM_DO_NOT_TRACK=1
export DO_NOT_TRACK=1

# Preserve the actual validated serving reservation at cold startup.
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.55}"

# Organizer-documented license infrastructure, not credentials. Caller wins.
export XILINXD_LICENSE_FILE="${XILINXD_LICENSE_FILE:-2100@vivado-lic.fpgachina.svc.cluster.local}"
export FLEXLM_TIMEOUT="${FLEXLM_TIMEOUT:-300000000}"
