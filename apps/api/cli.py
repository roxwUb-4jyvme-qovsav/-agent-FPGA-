"""命令行启动器：本机同源服务，SQLite 模式必须显式选择。"""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 FPGA 协同开发工作台")
    parser.add_argument("--host", choices=("127.0.0.1", "localhost", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--sqlite", action="store_true", help="显式本地开发模式，不计入 PostgreSQL 验收"
    )
    args = parser.parse_args()
    from dotenv import load_dotenv

    load_dotenv(Path(".env"))
    from .config import Settings

    settings = Settings.from_env()
    if args.sqlite:
        from dataclasses import replace

        database = (settings.data_dir / "development.db").resolve().as_posix()
        settings = replace(
            settings, database_url="sqlite+pysqlite:///" + database, allow_sqlite=True
        )
    import uvicorn

    from .main import create_app

    uvicorn.run(create_app(settings), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
