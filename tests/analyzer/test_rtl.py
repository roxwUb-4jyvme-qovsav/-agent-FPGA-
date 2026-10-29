"""Public RTL execution is explicitly skipped when the simulator is absent."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest


def tool_environment():
    root = Path(__file__).parents[2]
    environment = dict(os.environ)
    local_bin = root / ".local/tools/iverilog/mingw64/bin"
    environment["PATH"] = str(local_bin) + os.pathsep + environment.get("PATH", "")
    return environment


@pytest.mark.parametrize("channels", [8, 16])
def test_public_capture_core_rtl(channels, tmp_path):
    environment = tool_environment()
    if not shutil.which("iverilog", path=environment["PATH"]) or not shutil.which("vvp", path=environment["PATH"]):
        pytest.skip("NOT_RUN: Icarus Verilog / vvp not installed; model tests do not replace RTL")
    root = Path(__file__).parents[2]
    executable = tmp_path / "capture.vvp"
    compile_result = subprocess.run([
        shutil.which("iverilog", path=environment["PATH"]), "-g2012", "-s", "capture_core_tb", f"-Pcapture_core_tb.CHANNELS={channels}",
        "-o", str(executable), str(root / "products/analyzer/rtl/capture_core.v"),
        str(root / "tests/analyzer/rtl/capture_core_tb.sv")], capture_output=True, text=True, timeout=30, env=environment)
    assert compile_result.returncode == 0, compile_result.stderr
    result = subprocess.run([shutil.which("vvp", path=environment["PATH"]), str(executable)], capture_output=True, text=True, timeout=30, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PUBLIC_CAPTURE_PASS" in result.stdout


@pytest.mark.parametrize("top,marker", [("uart_tb", "PUBLIC_UART_PASS"), ("capture_device_tb", "PUBLIC_DEVICE_PASS"), ("register_bus_tb", "PUBLIC_BUS_PASS"), ("serial_top_tb", "PUBLIC_SERIAL_PASS")])
def test_public_uart_and_device_wire_rtl(top, marker, tmp_path):
    environment = tool_environment()
    if not shutil.which("iverilog", path=environment["PATH"]) or not shutil.which("vvp", path=environment["PATH"]):
        pytest.skip("NOT_RUN: Icarus/vvp absent")
    root = Path(__file__).parents[2]
    executable = tmp_path / f"{top}.vvp"
    sources = [str(path) for path in sorted((root / "products/analyzer/rtl").glob("*.v"))]
    compiled = subprocess.run([shutil.which("iverilog", path=environment["PATH"]), "-g2012", "-s", top, "-o", str(executable), *sources,
                               str(root / f"tests/analyzer/rtl/{top}.sv")], capture_output=True, text=True, timeout=30, env=environment)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    result = subprocess.run([shutil.which("vvp", path=environment["PATH"]), str(executable)], capture_output=True, text=True, timeout=60, env=environment)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker in result.stdout
