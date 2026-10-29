from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import func, select

from packages.contracts.validation import validate_payload

from ..errors import ApiError
from ..models import LoginFailure, LoginSession, SystemValue, User, utcnow
from ..security import (authenticate, new_session, password_hash, require_origin,
                        session_payload, token_hash, verify_password)
from ..services.common import event

router = APIRouter(prefix='/api/v1')


@router.get('/setup/status')
def setup_status(request: Request):
    with request.app.state.db.transaction() as db:
        return {'initialized': bool(db.scalar(select(func.count()).select_from(User))),
                'team_id': db.get(SystemValue, 'local_team').value['id']}


def logged_in_response(request, token, body, status=200):
    response = JSONResponse(body, status_code=status)
    response.set_cookie('fpga_session', token, httponly=True, samesite='strict',
                        secure=request.app.state.settings.secure_cookies,
                        max_age=request.app.state.settings.session_hours * 3600, path='/')
    return response


@router.post('/setup', status_code=201)
async def setup(request: Request):
    require_origin(request)
    body = await request.json()
    validate_payload('Login', body)
    with request.app.state.db.transaction() as db:
        # 已存在的团队行同时充当初始化锁，避免两个首次请求产生两个管理员。
        db.scalar(select(SystemValue).where(SystemValue.key == 'local_team').with_for_update())
        if db.scalar(select(func.count()).select_from(User)):
            raise ApiError(409, 'ALREADY_INITIALIZED', '初始化已完成')
        user = User(username=body['username'], password_hash=password_hash(body['password']),
                    global_role='admin', enabled=True)
        db.add(user)
        db.flush()
        token, payload = new_session(db, user, request.app.state.settings.session_hours)
        event(db, user.id, 'system.initialized')
    return logged_in_response(request, token, payload, 201)


@router.post('/auth/session')
async def login(request: Request):
    require_origin(request)
    body = await request.json()
    validate_payload('Login', body)
    denied = False
    with request.app.state.db.transaction() as db:
        identity = f'{request.client.host}:{body["username"]}'[:200]
        failures = db.scalar(select(func.count()).select_from(LoginFailure).where(
            LoginFailure.identity == identity,
            LoginFailure.created_at > utcnow() - timedelta(minutes=15)))
        if failures >= 10:
            raise ApiError(429, 'RATE_LIMITED', '登录失败过多，15 分钟后重试', retryable=True)
        user = db.scalar(select(User).where(User.username == body['username']))
        if not user or not user.enabled or not verify_password(user.password_hash, body['password']):
            db.add(LoginFailure(identity=identity))
            denied = True
        else:
            token, payload = new_session(db, user, request.app.state.settings.session_hours)
    if denied:
        raise ApiError(401, 'INVALID_CREDENTIALS', '用户名或密码不正确')
    return logged_in_response(request, token, payload)


@router.get('/auth/session')
def current(request: Request):
    with request.app.state.db.transaction() as db:
        user = authenticate(db, request)
        record = db.get(LoginSession, token_hash(request.cookies['fpga_session']))
        return session_payload(db, user, record.csrf_token)


@router.delete('/auth/session', status_code=204)
def logout(request: Request):
    with request.app.state.db.transaction() as db:
        authenticate(db, request, write=True)
        record = db.get(LoginSession, token_hash(request.cookies['fpga_session']))
        db.delete(record)
    result = Response(status_code=204)
    result.delete_cookie('fpga_session', path='/')
    return result
