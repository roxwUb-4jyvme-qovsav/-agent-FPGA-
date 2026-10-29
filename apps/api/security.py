"""人类会话、CSRF 与项目 ACL；全局管理员不隐式获得项目权限。"""
import hashlib
import secrets
from datetime import timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Request
from sqlalchemy import select

from .errors import ApiError
from .models import LoginSession, Member, User, utcnow

PASSWORDS = PasswordHasher()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value: str) -> str:
    if len(value) < 12 or len(value) > 1024:
        raise ApiError(422, 'INVALID_PASSWORD', '密码长度必须为 12 至 1024 个字符')
    return PASSWORDS.hash(value)


def verify_password(stored: str | None, value: str) -> bool:
    try:
        return bool(stored and len(value) <= 1024 and PASSWORDS.verify(stored, value))
    except VerificationError:
        return False


def require_origin(request: Request) -> None:
    if request.headers.get('origin') not in request.app.state.settings.allowed_origins:
        raise ApiError(403, 'ORIGIN_DENIED', '请求来源未获准')


def authenticate(db, request: Request, *, write: bool = False) -> User:
    value = request.cookies.get('fpga_session', '')
    record = db.get(LoginSession, token_hash(value)) if value else None
    user = db.get(User, record.user_id) if record else None
    if not record or aware(record.expires_at) <= utcnow() or not user or not user.enabled:
        raise ApiError(401, 'SESSION_EXPIRED', '请重新登录')
    if write:
        require_origin(request)
        supplied = request.headers.get('x-csrf-token', '')
        if not secrets.compare_digest(supplied, record.csrf_token):
            raise ApiError(403, 'CSRF_DENIED', '写请求缺少有效 CSRF 令牌')
    return user


def require_admin(user: User) -> None:
    if user.global_role != 'admin':
        raise ApiError(403, 'CAPABILITY_DENIED', '此操作需要全局管理员')


def project_role(db, user: User, project_id: str, allowed=None) -> str:
    member = db.scalar(select(Member).where(Member.project_id == project_id,
                                             Member.user_id == user.id))
    if member is None:
        raise ApiError(404, 'NOT_FOUND', '资源不存在')
    if allowed and member.role not in allowed:
        raise ApiError(403, 'CAPABILITY_DENIED', '当前项目角色不能执行此操作')
    return member.role


def session_payload(db, user: User, csrf: str) -> dict:
    members = db.scalars(select(Member).where(Member.user_id == user.id)).all()
    return {'user_id': user.id, 'global_role': user.global_role,
            'project_roles': [{'project_id': m.project_id, 'role': m.role} for m in members],
            'csrf_token': csrf}


def new_session(db, user: User, hours: int) -> tuple[str, dict]:
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=token_hash(token), user_id=user.id, csrf_token=csrf,
                        expires_at=utcnow() + timedelta(hours=hours)))
    return token, session_payload(db, user, csrf)
