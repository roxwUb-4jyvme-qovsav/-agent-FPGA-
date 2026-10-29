"""所有运行条件由显式配置决定，缺少外部工具时保持阻断。"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://fpga@127.0.0.1:5432/fpga_platform"
    data_dir: Path = field(default_factory=lambda: Path(".local/data").resolve())
    allow_sqlite: bool = False
    secure_cookies: bool = False
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    )
    worker_token: str | None = None
    verifier_token: str | None = None
    verifier_public_keys: Path | None = None
    session_hours: int = 8
    web_dist: Path = field(default_factory=lambda: Path("apps/web/dist").resolve())

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("FPGA_DATABASE_URL", cls.database_url),
            data_dir=Path(os.getenv("FPGA_DATA_DIR", ".local/data")).resolve(),
            allow_sqlite=os.getenv("FPGA_ALLOW_SQLITE") == "1",
            secure_cookies=os.getenv("FPGA_SECURE_COOKIES") == "1",
            worker_token=os.getenv("FPGA_WORKER_TOKEN"),
            verifier_token=os.getenv("FPGA_VERIFIER_TOKEN"),
            verifier_public_keys=(
                Path(os.environ["FPGA_VERIFIER_KEYS"]) if os.getenv("FPGA_VERIFIER_KEYS") else None
            ),
        )
