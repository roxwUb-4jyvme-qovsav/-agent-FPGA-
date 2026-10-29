"""模块化单体入口；请求层只调度工件和能力，不加载未知扩展。"""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from packages.contracts.validation import ContractError

from .config import Settings
from .database import Database
from .errors import ApiError
from .request_context import request_id_context
from .routers import auth, resources


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(settings)
    database.initialize()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        database.engine.dispose()

    app = FastAPI(title="FPGA 协同开发平台", version="0.1.0", lifespan=lifespan)
    app.state.settings, app.state.db = settings, database

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        token = request_id_context.set(request.state.request_id)
        try:
            result = await call_next(request)
        finally:
            request_id_context.reset(token)
        result.headers["X-Request-ID"] = request.state.request_id
        result.headers["X-Content-Type-Options"] = "nosniff"
        result.headers["Referrer-Policy"] = "same-origin"
        return result

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        return JSONResponse(error.body(request.state.request_id), status_code=error.status)

    @app.exception_handler(ContractError)
    async def contract_error(request, error):
        return await api_error(request, ApiError(422, "INVALID_SCHEMA", str(error), error.fields))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, _error):
        return await api_error(request, ApiError(422, "INVALID_REQUEST", "路径或参数格式不正确"))

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, _error):
        return await api_error(
            request, ApiError(409, "CONSTRAINT_CONFLICT", "唯一性或引用约束冲突")
        )

    @app.exception_handler(StaleDataError)
    async def stale_error(request, _error):
        return await api_error(request, ApiError(412, "VERSION_CONFLICT", "并发更新冲突"))

    app.include_router(auth.router)
    app.include_router(resources.router)
    # 可选功能在各自模块注册，避免入口与产品实现互相耦合。
    from .routers import devices, domain, events, files, jobs, system, worker_artifacts, workflow

    app.include_router(system.router)
    app.include_router(files.router)
    app.include_router(workflow.router)
    app.include_router(jobs.router)
    app.include_router(domain.router)
    app.include_router(devices.router)
    app.include_router(events.router)
    app.include_router(worker_artifacts.router)
    if settings.web_dist.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=settings.web_dist, html=True), name="workbench")
    return app
