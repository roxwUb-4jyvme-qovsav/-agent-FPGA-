"""PostgreSQL 事务入口；SQLite 开发模式显式串行，不能据此声明多 Worker 验收。"""

import threading
from contextlib import contextmanager, nullcontext

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import Settings
from .models import Base, SystemValue, uid


class Database:
    def __init__(self, settings: Settings):
        self.sqlite = settings.database_url.startswith("sqlite")
        if self.sqlite and not settings.allow_sqlite:
            raise ValueError("SQLite 仅允许显式开发/测试模式，设置 FPGA_ALLOW_SQLITE=1")
        self.engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"check_same_thread": False} if self.sqlite else {},
        )
        self.lock = threading.RLock()
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        if self.sqlite:

            @event.listens_for(self.engine, "connect")
            def configure_sqlite(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA journal_mode=WAL")

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.transaction() as session:
            if session.get(SystemValue, "local_team") is None:
                session.add(SystemValue(key="local_team", value={"id": uid()}))

    @contextmanager
    def transaction(self):
        # 本机 SQLite 的锁只保护单进程开发；生产依赖数据库行锁和唯一约束。
        with self.lock if self.sqlite else nullcontext():
            with self.sessions.begin() as session:
                yield session
