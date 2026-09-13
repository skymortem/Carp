import os
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from app.config import settings
from app.services.starline import StarLineClient, StarLineError, cached_client, configured_client
from app.services import scheduler


@pytest.mark.asyncio
async def test_auth_reused_and_expired_session_retried(monkeypatch, caplog):
    calls = []
    data_calls = 0

    def upstream(request):
        nonlocal data_calls
        calls.append(request.url.path)
        if 'getCode' in request.url.path:
            return httpx.Response(200, json={'state': 1, 'desc': {'code': 'app-code'}})
        if 'getToken' in request.url.path:
            return httpx.Response(200, json={'state': 1, 'desc': {'token': 'private-app-token'}})
        if 'login' in request.url.path:
            return httpx.Response(200, json={'state': 1, 'desc': {'id': 1, 'user_token': 'private-user-token'}})
        if 'auth.slid' in request.url.path:
            return httpx.Response(200, json={'user_id': 2}, headers={'set-cookie': 'slnet=private-session; Path=/'})
        data_calls += 1
        if data_calls == 2:
            return httpx.Response(401, text='Expired')
        return httpx.Response(200, json={'code': 200, 'data': {'obd': {'mileage': 100000}}})

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(upstream), **kwargs))
    client = StarLineClient('app', 'secret', 'owner', 'password')
    assert (await client.get_device_data('device1'))['obd']['mileage'] == 100000
    await client.get_device_data('device1')
    await client.get_device_data('device1')
    assert sum('getCode' in path for path in calls) == 2  # first auth + one expiry
    assert 'private-user-token' not in caplog.text
    assert 'private-app-token' not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize('response', [httpx.Response(502, text='bad gateway'), httpx.Response(200, text='not JSON'), httpx.Response(200, json=[])])
async def test_upstream_errors_are_controlled(monkeypatch, response):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(lambda request: response), **kwargs))
    with pytest.raises(StarLineError):
        await StarLineClient('app', 'secret', 'owner', 'password').auth()


@pytest.mark.asyncio
async def test_connect_persists_and_manual_scheduler_share_client(owner, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in ('starline_app_id', 'starline_app_secret', 'starline_login', 'starline_password'):
        monkeypatch.setattr(settings, name, '')
    cached_client.cache_clear()
    auth = AsyncMock()
    device_data = AsyncMock(return_value={'obd': {'ts': 1780000000, 'mileage': 120000}, 'state': {'motohrs': 9000}})
    monkeypatch.setattr(StarLineClient, 'ensure_auth', auth)
    monkeypatch.setattr(StarLineClient, 'get_device_data', device_data)
    response = await owner.post('/starline/connect', json=dict(app_id='app', app_secret="s'\\ecret", login='owner', password='pass', device_id='device1'))
    assert response.status_code == 200, response.text
    assert (tmp_path / '.env').stat().st_mode & 0o777 == 0o600
    assert configured_client() is cached_client('app', "s'\\ecret", 'owner', 'pass')
    assert (await owner.post('/starline/fetch')).status_code == 200
    await scheduler.collect_all_cars()
    assert (await owner.get('/starline/status')).json()['snaps_count'] == 1
    # New process must read the saved file, with no credentials inherited from the test runner.
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    for key in ('STARLINE_APP_ID', 'STARLINE_APP_SECRET', 'STARLINE_LOGIN', 'STARLINE_PASSWORD'):
        env.pop(key, None)
    check = subprocess.run([os.sys.executable, '-c',
        "from app.config import settings; assert settings.starline_login == 'owner'; assert settings.starline_app_id == 'app'; assert settings.starline_password == 'pass'"],
        cwd=tmp_path, env=env, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    cached_client.cache_clear()


def test_default_secret_is_generated_once(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    env.pop('SECRET_KEY', None)
    code = "from app.config import settings; assert len(settings.secret_key) == 64"
    for _ in range(2):
        result = subprocess.run([os.sys.executable, '-c', code], cwd=tmp_path, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        saved = (tmp_path / '.env').read_text()
        if _ == 0:
            first = saved
        else:
            assert saved == first
