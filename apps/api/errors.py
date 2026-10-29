"""所有错误使用稳定代码，避免把基础设施故障误报为工程失败。"""
from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 fields: list[dict[str, str]] | None = None, retryable: bool = False):
        self.status = status
        self.code = code
        self.message = message
        self.fields = fields or []
        self.retryable = retryable

    def body(self, request_id: str) -> dict[str, Any]:
        return {'error': {'code': self.code, 'message': self.message, 'request_id': request_id,
                'retryable': self.retryable, 'field_errors': [
                    {'path': field.get('path', field.get('field', '')) or '$',
                     'message': field['message']} for field in self.fields]}}
