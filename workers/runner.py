"""固定模板执行边界；缺少可信隔离后端时保持 blocked。"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

TEMPLATES: dict[str, tuple[str, ...]] = {
    "rtl.lint": ("iverilog", "-g2012", "-tnull", "candidate.sv"),
    "rtl.simulate": ("iverilog", "-g2012", "-o", "simulation", "candidate.sv", "testbench.sv"),
    "rtl.synthesize": ("yosys", "-p", "read_verilog candidate.sv; synth; stat"),
    "cad.export_netlist": ("kicad-cli", "sch", "export", "netlist", "candidate.kicad_sch"),
}


class IsolatedBackend(Protocol):
    """后端必须落实无网络、无模型密钥、只读工具链、进程/磁盘限制及进程树取消。"""
    def health(self) -> Mapping[str, Any]: ...
    def run(self, argv: tuple[str, ...], workspace: Path, config: Mapping[str, Any]) -> Mapping[str, Any]: ...


class TemplateRunner:
    def __init__(self, backend: IsolatedBackend | None = None) -> None:
        self.backend = backend

    def execute(self, template_id: str, artifact_dir: str | Path, config: Mapping[str, Any]) -> dict[str, Any]:
        start = datetime.now(timezone.utc).isoformat()
        result: dict[str, Any] = {"status": "blocked", "error_code": None, "template_id": template_id, "exit_code": None,
                                  "started_at": start, "finished_at": start, "stdout": "", "stderr": ""}
        if template_id not in TEMPLATES:
            return {**result, "error_code": "TEMPLATE_NOT_ALLOWED"}
        if self.backend is None:
            return {**result, "error_code": "ISOLATION_UNAVAILABLE"}
        health = self.backend.health()
        required = ("network_disabled", "secrets_absent", "process_limits", "disk_quota", "process_tree_cancel")
        if health.get("status") != "READY" or not all(health.get(key) is True for key in required):
            return {**result, "error_code": "ISOLATION_UNAVAILABLE"}
        if set(config) - {"timeout_seconds", "memory_mb", "max_log_bytes"}:
            return {**result, "error_code": "CONFIG_NOT_ALLOWED"}
        workspace = Path(artifact_dir).resolve()
        if not workspace.is_dir():
            return {**result, "error_code": "WORKSPACE_NOT_FOUND"}
        try:
            response = dict(self.backend.run(TEMPLATES[template_id], workspace, dict(config)))
        except Exception as exc:
            return {**result, "status": "failed", "error_code": "BACKEND_FAILED", "stderr": type(exc).__name__}
        # rc=0 只说明工具执行完毕；独立验收不能由此自动提升。
        if response.get("status") not in {"completed", "failed", "timeout", "cancelled"}:
            return {**result, "status": "failed", "error_code": "BACKEND_RESULT_INVALID"}
        if response["status"] == "completed" and response.get("exit_code") != 0:
            return {**result, "status": "failed", "error_code": "BACKEND_RESULT_INVALID"}
        return {**result, **response, "finished_at": datetime.now(timezone.utc).isoformat(), "verification_status": "NOT_RUN"}
