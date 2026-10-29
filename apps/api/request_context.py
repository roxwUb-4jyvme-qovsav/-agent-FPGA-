"""跨请求线程保留关联标识；不把请求对象或凭据放入事件。"""

from contextvars import ContextVar

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
