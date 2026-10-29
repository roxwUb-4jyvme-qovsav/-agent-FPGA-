import hashlib
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import Response

from packages.contracts.validation import validate_payload

from ..errors import ApiError
from ..models import UploadRecord, utcnow
from ..security import authenticate, aware, project_role
from ..services.artifacts import (artifact_bytes, atomic_bytes, check_media,
                                  store_artifact)
from ..services.common import event, get_resource, iso, mutate, read

router = APIRouter(prefix='/api/v1')


def get_upload(db, request, upload_id, project_id, user_id):
    record = db.get(UploadRecord, upload_id)
    if not record or record.project_id != project_id or record.owner_id != user_id:
        raise ApiError(404, 'NOT_FOUND', '上传票据不存在')
    if aware(record.expires_at) <= utcnow():
        raise ApiError(410, 'UPLOAD_EXPIRED', '上传票据已过期')
    return record


@router.post('/projects/{project_id}/uploads', status_code=201)
async def upload_init(request: Request, project_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, ('developer', 'reviewer', 'admin'))
        validate_payload('UploadInit', body)
        record = UploadRecord(project_id=project_id, owner_id=user.id, payload=body,
                              expires_at=utcnow() + timedelta(minutes=10))
        db.add(record)
        db.flush()
        return {'upload_id': record.id,
                'put_path': f'/api/v1/projects/{project_id}/uploads/{record.id}/content',
                'expires_at': iso(record.expires_at)}, 201, {}
    return await mutate(request, operation)


@router.put('/projects/{project_id}/uploads/{resource_id}/content', status_code=204)
async def upload_content(request: Request, project_id: str, resource_id: str):
    with request.app.state.db.transaction() as db:
        user = authenticate(db, request, write=True)
        project_role(db, user, project_id, ('developer', 'reviewer', 'admin'))
        record = get_upload(db, request, resource_id, project_id, user.id)
        maximum = record.payload['bytes']
        expected_hash = record.payload['sha256']
        if record.artifact_id:
            raise ApiError(409, 'UPLOAD_COMPLETED', '已完成上传不能重写')
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > maximum:
            raise ApiError(413, 'FILE_TOO_LARGE', '内容超过票据登记大小')
    if len(content) != maximum or hashlib.sha256(content).hexdigest() != expected_hash:
        raise ApiError(422, 'UPLOAD_MISMATCH', '上传字节数或哈希不匹配')
    atomic_bytes(request.app.state.settings.data_dir / 'uploads' / resource_id, bytes(content))
    return Response(status_code=204)


@router.post('/projects/{project_id}/uploads/{resource_id}/complete')
async def upload_complete(request: Request, project_id: str, resource_id: str):
    def operation(db, user, body):
        project_role(db, user, project_id, ('developer', 'reviewer', 'admin'))
        validate_payload('UploadComplete', body)
        record = get_upload(db, request, resource_id, project_id, user.id)
        if record.artifact_id:
            return get_resource(db, 'artifacts', record.artifact_id, project_id).payload, 200, {}
        try:
            content = (request.app.state.settings.data_dir / 'uploads' / record.id).read_bytes()
        except FileNotFoundError:
            raise ApiError(409, 'UPLOAD_INCOMPLETE', '请先上传完整内容')
        digest = hashlib.sha256(content).hexdigest()
        if digest != body['sha256'] or digest != record.payload['sha256']:
            raise ApiError(422, 'UPLOAD_MISMATCH', '完成时工件哈希不匹配')
        check_media(record.payload, content)
        artifact = store_artifact(db, request.app.state.settings, project_id, content, 'attachment',
            filename=record.payload['filename'], media_type=record.payload['content_type'])
        record.artifact_id = artifact.id
        event(db, user.id, 'artifact.uploaded', artifact)
        return artifact.payload, 200, {}
    return await mutate(request, operation)


@router.get('/projects/{project_id}/artifacts/{resource_id}/content')
async def download(request: Request, project_id: str, resource_id: str):
    def operation(db, user):
        project_role(db, user, project_id)
        record = get_resource(db, 'artifacts', resource_id, project_id)
        content = artifact_bytes(request.app.state.settings, record)
        # attachment 与 nosniff 避免把用户 HTML 或未知脚本当同源页面执行。
        return Response(content, media_type='application/octet-stream', headers={
            'ETag': '"' + record.payload['sha256'] + '"',
            'Content-Disposition': f'attachment; filename="{record.id}.bin"'})
    return await read(request, operation)
