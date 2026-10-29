"""管理动作在资源锁事务内执行；外部条件缺失时保留明确阻断。"""
from copy import deepcopy
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from packages.contracts.validation import validate_payload
from packages.knowledge.parsing import parse_document
from packages.orchestration.dag import DependencyGraph
from packages.quotes.pricing import price_quote
from packages.tool_gateway.extensions import ExtensionError, validate_package

from ..errors import ApiError
from ..models import ResourceHistory, ResourceRecord, UploadRecord, User, uid, utcnow
from ..security import project_role, require_admin
from .artifacts import artifact_bytes, artifact_json, store_json
from .common import event, get_resource, history, iso, sha


def _artifact(db: Any, user: Any, artifact_id: str | None) -> ResourceRecord:
    artifact = db.get(ResourceRecord, artifact_id) if artifact_id else None
    if not artifact or artifact.kind != 'artifacts' or artifact.state == 'DELETED':
        raise ApiError(409, 'ARTIFACT_REQUIRED', '请提供已上传的工件引用')
    if artifact.project_id:
        project_role(db, user, artifact.project_id)
    else:
        require_admin(user)
    return artifact


def _blocked(record: ResourceRecord, code: str) -> None:
    record.state = 'BLOCKED'
    record.internal = {**(record.internal or {}), 'health': {'status': 'BLOCKED', 'code': code,
                        'checked_at': iso(utcnow()), 'payload_sha256': sha(record.payload)}}


def _document_command(db: Any, user: Any, request: Any, record: ResourceRecord, action: str) -> None:
    if action == 'revoke':
        record.state = 'REVOKED'
        record.internal = {**record.internal, 'revoked_at': iso(utcnow())}
        return
    if action in ('parse', 'retry'):
        if record.state not in ('DRAFT', 'UPLOADED', 'FAILED', 'BLOCKED', 'PARSED'):
            raise ApiError(409, 'INVALID_STATE', '当前文档版本不可重新解析')
        upload = db.get(UploadRecord, record.payload['upload_id'])
        if not upload or upload.project_id != record.project_id or not upload.artifact_id:
            raise ApiError(404, 'NOT_FOUND', '已完成上传不存在')
        if record.payload.get('collection_id'):
            get_resource(db, 'collections', record.payload['collection_id'], record.project_id)
        artifact = get_resource(db, 'artifacts', upload.artifact_id, record.project_id)
        parsed = parse_document(artifact_bytes(request.app.state.settings, artifact),
            artifact.internal['media_type'], source_uri='artifact:' + artifact.id)
        record.state = parsed['status']
        record.internal = {**record.internal, 'parsed': parsed, 'artifact_id': artifact.id,
                           'version_id': uid(), 'reviewed_by': None}
        return
    if action == 'publish':
        project_role(db, user, record.project_id, ('reviewer', 'admin'))
        if record.state != 'PARSED' or not record.internal.get('parsed', {}).get('chunks'):
            raise ApiError(409, 'DOCUMENT_PARSE_REQUIRED', '完整解析成功后才能审阅发布')
        if record.payload['scope'] != 'project_knowledge' or not record.payload['collection_id']:
            raise ApiError(409, 'COLLECTION_REQUIRED', '仅项目知识文档可以发布至知识库')
        collection = get_resource(db, 'collections', record.payload['collection_id'], record.project_id)
        if collection.payload.get('embedding_profile_version_id'):
            raise ApiError(409, 'EMBEDDING_NOT_CONFIGURED', '向量模型尚未经过真实能力测试')
        # 人工 publish 命令同时完成审阅；索引使用已提取片段的本地关键词路径。
        record.state = 'PUBLISHED'
        record.internal = {**record.internal, 'reviewed_by': user.id, 'published_at': iso(utcnow()),
                           'index_kind': 'keyword', 'generation': sha([record.id, record.version])}
        return
    raise ApiError(409, 'ACTION_NOT_IMPLEMENTED', '该文档动作尚未实现')


def _health_current(record: ResourceRecord) -> bool:
    health = (record.internal or {}).get('health', {})
    return health.get('status') == 'PASS' and health.get('payload_sha256') == sha(record.payload)


def _rollback(db: Any, record: ResourceRecord, target_id: str | None) -> None:
    previous = db.get(ResourceHistory, target_id) if target_id else None
    if not previous or previous.resource_id != record.id:
        raise ApiError(404, 'NOT_FOUND', '目标历史版本不存在')
    record.payload = deepcopy(previous.payload)
    if 'enabled' in record.payload:
        record.payload = {**record.payload, 'enabled': False}
    record.state = 'DRAFT'
    record.internal = {'rollback_source_version': previous.version,
                       'rollback_scope': 'platform_configuration_only'}


def _extension_validate(db: Any, user: Any, request: Any, record: ResourceRecord,
                        parameters: dict[str, Any]) -> None:
    artifact = _artifact(db, user, parameters.get('artifact_id'))
    if record.kind == 'skills':
        expected = record.payload['content_sha256']
        if artifact.payload['sha256'] != expected:
            raise ApiError(409, 'PAYLOAD_HASH_MISMATCH', 'Skill 内容与冻结哈希不一致')
        if record.payload.get('dependencies'):
            raise ApiError(409, 'SKILL_DEPENDENCY_LOCK_REQUIRED', 'Skill 依赖尚未解析为精确版本锁')
        record.state = 'VALIDATED'
        record.internal = {**record.internal, 'validated_sha256': sha(record.payload), 'payload_artifact_id': artifact.id}
        return
    dependencies = {item.payload['id']: {**item.payload, 'status': item.state,
        'trusted': item.internal.get('trusted', False)} for item in db.scalars(select(ResourceRecord).where(
            ResourceRecord.kind == 'extensions', ResourceRecord.id != record.id)).all()}
    registries = db.scalars(select(ResourceRecord).where(ResourceRecord.kind == 'registries',
        ResourceRecord.state.in_(('APPROVED', 'ENABLED')))).all()
    sources = [value for registry in registries if registry.internal.get('trusted')
               for value in (registry.payload.get('endpoint'), registry.payload.get('name')) if value]
    try:
        result = validate_package(record.payload, artifact_bytes(request.app.state.settings, artifact),
            dependencies, trusted=record.internal.get('trusted', False), approved_sources=sources)
    except ExtensionError as exc:
        raise ApiError(409, str(exc).split(':')[0], '扩展内容、信任、来源或依赖校验未满足') from exc
    record.state = 'VALIDATED'
    record.internal = {**record.internal, 'validation': result, 'validated_sha256': sha(record.payload),
                       'payload_artifact_id': artifact.id}


def _compare_quote(db: Any, user: Any, request: Any, record: ResourceRecord,
                   parameters: dict[str, Any]) -> None:
    artifact = _artifact(db, user, parameters.get('artifact_id'))
    if artifact.project_id != record.project_id:
        raise ApiError(404, 'NOT_FOUND', '报价查询工件不属于此项目')
    lookup = artifact_json(request.app.state.settings, artifact)
    validate_payload('QuoteLookup', lookup)
    for key in ('manufacturer', 'mpn', 'package', 'country_class', 'currency'):
        if lookup[key] != record.payload[key]:
            raise ApiError(422, 'QUOTE_PRODUCT_MISMATCH', '完整商品键和比较口径必须一致')
    priced = price_quote(record.payload, units=1, qty_per_unit=lookup['quantity'])
    record.internal = {**record.internal, 'comparison': priced, 'lookup_artifact_id': artifact.id}
    record.state = 'COMPARABLE' if priced['comparable'] else 'INCOMPLETE'


def _change_preview(db: Any, user: Any, request: Any, record: ResourceRecord) -> None:
    parent = get_resource(db, 'revisions', record.payload['parent_revision_id'], record.project_id)
    original = get_resource(db, 'artifacts', parent.payload['requirement_artifact_id'], record.project_id)
    proposed = get_resource(db, 'artifacts', record.payload['proposed_requirement_artifact_id'], record.project_id)
    before, after = artifact_json(request.app.state.settings, original), artifact_json(request.app.state.settings, proposed)
    validate_payload('RequirementSpec', after)
    if after['project_id'] != record.project_id:
        raise ApiError(404, 'NOT_FOUND', '变更工件不属于此项目')
    fields = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
    graph = DependencyGraph({'requirements': [], 'contract': ['requirements'], 'rtl': ['contract'],
        'host': ['contract'], 'cad': ['contract'], 'bom': ['cad'], 'quotes': ['bom'],
        'verify': ['rtl', 'host', 'cad'], 'smoke': ['verify']})
    record.internal = {**record.internal, 'preview': {'changed_fields': fields,
        'differences': [{'field': key, 'before': before.get(key), 'after': after.get(key)} for key in fields],
        'stale': graph.invalidate(['requirements']) if fields else {},
        'rebuild_order': graph.rebuild_plan(['requirements']) if fields else ['smoke'],
        'full_regression': bool(fields), 'system_smoke_required': True,
        'baseline_sha256': original.payload['sha256'], 'proposed_sha256': proposed.payload['sha256']}}
    record.state = 'PREVIEWED'


def management_command(db: Any, user: Any, request: Any, record: ResourceRecord,
                       body: dict[str, Any]) -> tuple[dict[str, Any], int, dict[str, str]]:
    action, parameters = body['action'], body['parameters']
    if record.project_id:
        project_role(db, user, record.project_id, ('developer', 'reviewer', 'admin'))
    else:
        require_admin(user)
    if record.state in ('REMOVED', 'DELETED'):
        raise ApiError(410, 'RESOURCE_REMOVED', '已卸载资源不能再调用')
    # 写历史与事实、审计同一事务；失败会连同历史一起回滚。
    history(db, record)
    if record.kind == 'documents':
        _document_command(db, user, request, record, action)
    elif record.kind == 'users' and action in ('enable', 'disable'):
        if action == 'disable' and record.id == user.id:
            raise ApiError(409, 'LAST_ADMIN', '不能停用当前管理员会话')
        target = db.get(User, record.id)
        if target is None:
            raise ApiError(404, 'NOT_FOUND', '用户不存在')
        target.enabled = action == 'enable'
        record.payload = {**record.payload, 'enabled': target.enabled}
        record.state = 'ENABLED' if target.enabled else 'DISABLED'
    elif action == 'disable':
        record.state = 'DISABLED'
        if 'enabled' in record.payload:
            record.payload = {**record.payload, 'enabled': False}
    elif action == 'rollback':
        _rollback(db, record, parameters.get('target_version_id'))
    elif action == 'export':
        artifact = store_json(db, request.app.state.settings, record.project_id, record.payload,
                              'configuration_export', provenance='configuration-export')
        record.internal = {**record.internal, 'export_artifact': artifact.payload}
    elif record.kind == 'changes':
        if action == 'preview':
            _change_preview(db, user, request, record)
        elif action == 'cancel':
            record.state = 'CANCELLED'
        else:
            raise ApiError(409, 'CHANGE_REBUILD_NOT_CONFIGURED', '变更应用需新版本契约和完整重建，当前仅支持预览')
    elif record.kind == 'quotes':
        if action == 'compare':
            _compare_quote(db, user, request, record, parameters)
        else:
            _blocked(record, 'QUOTE_SOURCE_NOT_CONFIGURED')
    elif record.kind == 'collections':
        if record.payload.get('embedding_profile_version_id') or record.payload.get('external_connection_id'):
            _blocked(record, 'KNOWLEDGE_PROVIDER_NOT_CONFIGURED')
        else:
            record.state = 'READY'
            record.internal = {**record.internal, 'index_kind': 'keyword', 'generation': uid(),
                               'vector_status': 'NOT_CONFIGURED'}
    elif record.kind in ('extensions', 'skills') and action == 'validate':
        _extension_validate(db, user, request, record, parameters)
    elif action == 'trust' and record.kind in ('extensions', 'registries'):
        record.state = 'QUARANTINED'
        record.internal = {**record.internal, 'trusted': True, 'trusted_by': user.id,
                           'trusted_sha256': sha(record.payload)}
    elif action == 'revoke' and record.kind in ('extensions', 'registries'):
        record.state = 'DISABLED'
        record.internal = {**record.internal, 'trusted': False, 'revoked_at': iso(utcnow())}
    elif action == 'uninstall' and record.kind in ('extensions', 'skills'):
        if record.state == 'ENABLED':
            raise ApiError(409, 'DISABLE_REQUIRED', '卸载前请先停用')
        record.state = 'REMOVED'
    elif action == 'approve':
        if not _health_current(record):
            raise ApiError(409, 'HEALTH_TEST_REQUIRED', '需要当前配置的真实健康测试和审核')
        record.state = 'APPROVED'
        record.internal = {**record.internal, 'approved_by': user.id, 'approved_sha256': sha(record.payload)}
    elif action == 'enable':
        if record.kind == 'budgets':
            if Decimal(record.payload['daily_limit_cny']) < 0 or Decimal(record.payload['per_run_limit_cny']) < 0:
                raise ApiError(422, 'INVALID_BUDGET', '额度不可为负')
        elif not _health_current(record):
            raise ApiError(409, 'HEALTH_TEST_REQUIRED', '需要当前配置的真实健康测试，不能以静态校验替代')
        record.state = 'ENABLED'
        if 'enabled' in record.payload:
            record.payload = {**record.payload, 'enabled': True}
    elif action == 'import' and record.kind == 'model-profiles':
        incoming = artifact_json(request.app.state.settings, _artifact(db, user, parameters.get('artifact_id')))
        validate_payload('ModelProfile', incoming)
        if incoming.get('secret_ref'):
            raise ApiError(409, 'SECRET_REBIND_REQUIRED', '导入后须重新绑定本机凭证')
        record.payload = {**incoming, 'enabled': False}
        record.state, record.internal = 'DRAFT', {}
    elif action in ('test', 'discover', 'start', 'stop', 'rotate', 'refresh'):
        _blocked(record, {'model-profiles': 'PAID_CAPABILITY_TEST_NOT_CONFIGURED',
                         'mcp-connections': 'MCP_CONNECTION_NOT_CONFIGURED',
                         'bindings': 'BOUND_PROVIDER_NOT_CONFIGURED'}.get(record.kind, 'EXTERNAL_ADAPTER_NOT_CONFIGURED'))
    else:
        raise ApiError(409, 'ACTION_NOT_IMPLEMENTED', '此动作尚未具备可执行条件')
    emitted = event(db, user.id, record.kind + '.' + action, record,
                    payload={'state': record.state, 'reason': parameters['reason']})
    db.flush()
    return {'resource_id': record.id, 'state': record.state, 'run_id': None,
            'approval_id': parameters.get('approval_id'), 'event_cursor': emitted.id}, 202, {}
