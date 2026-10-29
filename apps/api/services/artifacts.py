"""只按服务端分配的哈希路径写文件；展示文件名永不参与路径拼接。"""
import hashlib
import json
import os
import stat
import zipfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..errors import ApiError
from ..models import ResourceRecord
from .common import canonical


def atomic_bytes(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def store_artifact(db, settings, project_id: str, content: bytes, kind: str,
                   *, filename='artifact.bin', media_type='application/octet-stream',
                   provenance='upload', task_id=None) -> ResourceRecord:
    digest = hashlib.sha256(content).hexdigest()
    target = settings.data_dir / 'objects' / digest[:2] / digest
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ApiError(500, 'ARTIFACT_CORRUPT', '已有工件哈希不匹配')
    else:
        atomic_bytes(target, content)
    record = ResourceRecord(kind='artifacts', project_id=project_id, state='IMMUTABLE',
        payload={}, internal={'filename': filename, 'media_type': media_type, 'bytes': len(content),
                             'provenance': provenance, 'task_id': task_id})
    db.add(record)
    db.flush()
    record.payload = {'id': record.id, 'sha256': digest, 'kind': kind}
    return record


def artifact_bytes(settings, record: ResourceRecord) -> bytes:
    digest = record.payload['sha256']
    target = settings.data_dir / 'objects' / digest[:2] / digest
    try:
        content = target.read_bytes()
    except OSError:
        raise ApiError(503, 'ARTIFACT_UNAVAILABLE', '工件文件缺失，请从备份恢复')
    if hashlib.sha256(content).hexdigest() != digest:
        raise ApiError(503, 'ARTIFACT_CORRUPT', '工件校验失败，已阻止使用')
    return content


def artifact_json(settings, record: ResourceRecord) -> dict:
    try:
        return json.loads(artifact_bytes(settings, record))
    except (ValueError, UnicodeDecodeError):
        raise ApiError(422, 'INVALID_ARTIFACT', '引用的工件不是 JSON 文档')


def check_media(payload: dict, content: bytes) -> None:
    extension = Path(payload['filename']).suffix.lower()
    expected = {'.pdf': b'%PDF-', '.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8',
                '.jpeg': b'\xff\xd8', '.docx': b'PK', '.xlsx': b'PK', '.zip': b'PK'}
    if extension in expected and not content.startswith(expected[extension]):
        raise ApiError(415, 'UNSUPPORTED_MEDIA', '文件扩展名与魔数不一致')
    if extension not in {*expected, '.txt', '.md', '.json', '.csv', '.v', '.sv', '.log'}:
        raise ApiError(415, 'UNSUPPORTED_MEDIA', '此上传通道不支持该文件类型')
    if extension in ('.docx', '.xlsx', '.zip'):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                members = archive.infolist()
                if len(members) > 1000 or sum(m.file_size for m in members) > 200 * 1024 ** 2:
                    raise ValueError('归档成员数量或解压体积超限')
                seen = set()
                for member in members:
                    path = PurePosixPath(member.filename)
                    if (path.is_absolute() or '..' in path.parts or ':' in member.filename
                            or '\\' in member.filename or member.filename in seen
                            or stat.S_ISLNK(member.external_attr >> 16)
                            or member.file_size / max(member.compress_size, 1) > 100):
                        raise ValueError('归档路径、链接、重名或膨胀比不安全')
                    seen.add(member.filename)
        except (zipfile.BadZipFile, ValueError) as error:
            raise ApiError(415, 'UNSAFE_ARCHIVE', str(error))


def store_json(db, settings, project_id, payload, kind, **kwargs):
    return store_artifact(db, settings, project_id, canonical(payload), kind,
                          filename=kind + '.json', media_type='application/json', **kwargs)
