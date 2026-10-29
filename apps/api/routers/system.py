"""初始化之后的管理诊断和真实健康状态。"""
import shutil

from fastapi import APIRouter, Request

from ..errors import ApiError
from ..models import SystemValue, User
from ..security import password_hash, require_admin
from ..services.common import event, mutate, read

router = APIRouter(prefix='/api/v1')


@router.get('/health')
def health(request: Request):
    return {'status': 'degraded', 'api_version': 'v1', 'database_ready': True,
            'scheduler_ready': False, 'eda_ready': False, 'cad_ready': False}


@router.get('/system/status')
async def system_status(request: Request):
    def operation(db, user):
        return {**health(request), 'team_id': db.get(SystemValue, 'local_team').value['id'],
                'database_mode': 'sqlite_development' if request.app.state.db.sqlite else 'postgresql',
                'model_budget_default_cny': '0', 'tools': {
                    tool: {'installed': bool(shutil.which(tool)), 'verified': False}
                    for tool in ('iverilog', 'yosys', 'kicad-cli', 'docker')},
                'product_acceptance': {'total': 84, 'executed': 0, 'status': 'NOT_RUN'}}
    return await read(request, operation)


@router.post('/system/users/{user_id}/password', status_code=204)
async def set_password(request: Request, user_id: str):
    def operation(db, user, body):
        require_admin(user)
        if set(body) != {'password'} or not isinstance(body['password'], str):
            raise ApiError(422, 'INVALID_SCHEMA', '必须提供 password')
        target = db.get(User, user_id)
        if target is None:
            raise ApiError(404, 'NOT_FOUND', '用户不存在')
        target.password_hash = password_hash(body['password'])
        event(db, user.id, 'user.password_changed', payload={'target_id': user_id})
        return {}, 204, {}
    return await mutate(request, operation)
