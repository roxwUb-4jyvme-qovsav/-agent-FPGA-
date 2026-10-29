"""从用户操作验证权限边界，避免只断言内部函数的调用次数。"""
import uuid


ORIGIN = {"Origin": "http://testserver"}


def open_client(tmp_path, **overrides):
    from fastapi.testclient import TestClient
    from apps.api.config import Settings
    from apps.api.main import create_app

    settings = Settings(database_url=f"sqlite+pysqlite:///{tmp_path / 'test.db'}",
                        data_dir=tmp_path / 'data', allow_sqlite=True,
                        allowed_origins=('http://testserver',), **overrides)
    return TestClient(create_app(settings))


def setup(client):
    response = client.post('/api/v1/setup', json={
        'username': 'owner', 'password': 'my-long-local-password'}, headers=ORIGIN)
    assert response.status_code == 201, response.text
    return response.json()['csrf_token']


def write(client, method, path, payload=None, csrf='', etag=None, key=None):
    headers = {**ORIGIN, 'X-CSRF-Token': csrf, 'Idempotency-Key': key or str(uuid.uuid4())}
    if etag:
        headers['If-Match'] = etag
    return getattr(client, method)(f'/api/v1{path}', json=payload, headers=headers)


def project(client, csrf, name='示例工程'):
    team = client.get('/api/v1/setup/status').json()['team_id']
    response = write(client, 'post', '/projects',
                     {'name': name, 'description': '', 'team_id': team}, csrf)
    assert response.status_code == 201, response.text
    return response


def test_bootstrap_is_single_use_and_cookie_is_http_only(tmp_path):
    with open_client(tmp_path) as client:
        assert client.get('/api/v1/setup/status').json()['initialized'] is False
        setup(client)
        assert client.cookies.get('fpga_session')
        session = client.get('/api/v1/auth/session')
        assert session.json()['global_role'] == 'admin'
        repeated = client.post('/api/v1/setup', json={
            'username': 'other', 'password': 'another-long-password'}, headers=ORIGIN)
        assert repeated.status_code == 409


def test_mutation_requires_csrf_and_known_origin(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        team = client.get('/api/v1/setup/status').json()['team_id']
        payload = {'name': 'x', 'description': '', 'team_id': team}
        assert write(client, 'post', '/projects', payload).status_code == 403
        bad = client.post('/api/v1/projects', json=payload, headers={
            'Origin': 'https://attacker.invalid', 'X-CSRF-Token': csrf,
            'Idempotency-Key': str(uuid.uuid4())})
        assert bad.status_code == 403


def test_etag_preconditions_and_idempotency_conflict(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        team = client.get('/api/v1/setup/status').json()['team_id']
        body = {'name': 'x', 'description': '', 'team_id': team}
        first = write(client, 'post', '/projects', body, csrf, key='stable-key')
        retry = write(client, 'post', '/projects', body, csrf, key='stable-key')
        assert first.json()['id'] == retry.json()['id']
        conflict = write(client, 'post', '/projects', {**body, 'name': 'changed'}, csrf,
                         key='stable-key')
        assert conflict.status_code == 409
        path = '/projects/' + first.json()['id']
        assert write(client, 'patch', path, {'name': 'new'}, csrf).status_code == 428
        changed = write(client, 'patch', path, {'name': 'new'}, csrf, first.headers['etag'])
        assert changed.status_code == 200
        stale = write(client, 'patch', path, {'name': 'lost'}, csrf, first.headers['etag'])
        assert stale.status_code == 412


def test_project_acl_global_admin_has_no_automatic_project_access(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        original = project(client, csrf).json()
        created = write(client, 'post', '/admin/users',
                        {'username': 'second', 'global_role': 'admin', 'enabled': True}, csrf)
        assert created.status_code == 201, created.text
        user_id = created.json()['id']
        reset = write(client, 'post', f'/system/users/{user_id}/password',
                      {'password': 'second-long-password'}, csrf)
        assert reset.status_code == 204, reset.text
        client.delete('/api/v1/auth/session', headers={**ORIGIN, 'X-CSRF-Token': csrf})
        login = client.post('/api/v1/auth/session', json={
            'username': 'second', 'password': 'second-long-password'}, headers=ORIGIN)
        assert login.status_code == 200, login.text
        assert client.get('/api/v1/projects').json()['items'] == []
        assert client.get('/api/v1/projects/' + original['id']).status_code == 404


def test_project_and_session_survive_application_restart(tmp_path):
    with open_client(tmp_path) as client:
        csrf = setup(client)
        stored = project(client, csrf).json()
        cookie = client.cookies.get('fpga_session')
    with open_client(tmp_path) as restarted:
        restarted.cookies.set('fpga_session', cookie)
        assert restarted.get('/api/v1/projects/' + stored['id']).json()['payload']['name'] == '示例工程'
