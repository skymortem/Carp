from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select, func
from app.database import async_session
from app.models.car import StarSnap, ServicePlan, ServiceLog
from app.services.starline import StarLineClient
from app.services.collection import save_snapshot, collect_snapshot
from app.services.maintenance import current_readings


@pytest.mark.asyncio
async def test_registration_cookie_and_single_owner(client):
    response = await client.post('/auth/register', json=dict(email='a@test', username='Owner', password='secret123'))
    assert response.status_code == 200
    assert 'token' not in response.json()
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert (await client.get('/auth/me')).status_code == 200
    assert (await client.post('/auth/register', json=dict(email='b@test', username='Other', password='secret123'))).status_code == 403
    await client.post('/auth/logout')
    assert (await client.get('/auth/me')).status_code == 401
    assert (await client.post('/auth/login', json=dict(email='a@test', password='secret123'))).status_code == 200
    assert (await client.post('/auth/login', json=dict(email='a@test', password='x'*100))).status_code == 401


@pytest.mark.asyncio
async def test_dashboard_zero_one_and_missing_readings(owner):
    assert 'function dashboard()' in (await owner.get('/dashboard')).text
    async with async_session() as db:
        db.add(StarSnap(car_id=1, ts=datetime.now(timezone.utc), mileage_km=None))
        db.add(ServicePlan(car_id=1, name='Oil', interval_km=10000, last_mileage_km=100000,
            last_date=datetime.now(timezone.utc)))
        await db.commit()
    dashboard = await owner.get('/dashboard')
    assert dashboard.status_code == 200 and 'function dashboard()' in dashboard.text
    page = await owner.get('/service-plan/page')
    assert page.status_code == 200 and 'нет показаний' in page.text
    assert (await owner.get('/starline/status')).json()['snaps_count'] == 1


@pytest.mark.asyncio
async def test_last_known_values_and_snapshot_dedup(owner):
    now = datetime.now(timezone.utc)
    snapshot = StarLineClient.parse_snapshot({'obd': {'ts': int(now.timestamp()), 'mileage': 0}, 'state': {'motohrs': 0}})
    async with async_session() as db:
        first = await save_snapshot(db, 1, snapshot)
        second = await save_snapshot(db, 1, snapshot)
        assert first.id == second.id
        db.add(StarSnap(car_id=1, ts=now + timedelta(hours=1)))
        await db.commit()
        assert await current_readings(db, 1) == (0, 0)
        assert await db.scalar(select(func.count()).select_from(StarSnap)) == 2


@pytest.mark.asyncio
async def test_complete_clear_interval_and_preserve_history(owner):
    response = await owner.post('/service-plan/add', json=dict(name="Oil's filter", interval_km=10000, interval_motohours=250))
    pid = response.json()['id']
    response = await owner.post('/service-plan/complete', json=dict(plan_ids=[pid, pid], mileage_km=100000, motohours=150, date='2026-01-01'))
    assert response.status_code == 200
    async with async_session() as db:
        plan = await db.get(ServicePlan, pid)
        assert plan.last_motohours == 9000
        assert await db.scalar(select(func.count()).select_from(ServiceLog)) == 1
    response = await owner.put(f'/service-plan/update/{pid}', json=dict(interval_km=None))
    assert response.status_code == 200
    async with async_session() as db:
        assert (await db.get(ServicePlan, pid)).interval_km is None
    page = (await owner.get('/service-plan/page')).text
    assert '150 ч 0 мин' in page and ':data-name=' not in page
    assert (await owner.get('/service-plan/history')).json()[0]['motohours'] == '150 ч 0 мин'
    assert (await owner.post(f'/service-plan/delete/{pid}')).status_code == 200
    async with async_session() as db:
        history = await db.scalar(select(ServiceLog))
        assert history.plan_id is None and history.plan_name == "Oil's filter"


@pytest.mark.asyncio
async def test_validation_and_unknown_baseline(owner):
    for body in [dict(name='Oil'), dict(name='Oil', interval_km=-1), dict(name='', interval_km=10000)]:
        assert (await owner.post('/service-plan/add', json=body)).status_code == 422
    pid = (await owner.post('/service-plan/add', json=dict(name='Oil', interval_km=10000))).json()['id']
    assert (await owner.post('/service-plan/complete', json=dict(plan_ids=[pid]))).status_code == 422
    assert (await owner.post('/service-plan/complete', json=dict(plan_ids=[pid], date='bad'))).status_code == 422
    assert (await owner.put(f'/service-plan/update/{pid}', json=dict(interval_km=None))).status_code == 422


@pytest.mark.asyncio
async def test_console_restricts_endpoints_before_network(owner):
    response = await owner.post('/starline/api-raw', json=dict(method='POST', endpoint='/json/v2/device/other/command'))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_events_not_used_as_accumulated_counter():
    client = AsyncMock()
    client.get_device_data.return_value = {'obd': {'mileage': 100000}}
    snapshot = await collect_snapshot(client, 'device1')
    assert snapshot['motohours_minutes'] is None
    client.get_engine_hours_from_events.assert_not_called()


def test_empty_and_malformed_telemetry():
    snapshot = StarLineClient.parse_snapshot({})
    assert snapshot['ts'].year == datetime.now(timezone.utc).year
    assert snapshot['engine_on'] is None
    snapshot = StarLineClient.parse_snapshot({'obd': {'mileage': 'bad', 'ts': []}, 'state': {'motohrs': -1, 'ign': '0'}})
    assert snapshot['mileage_km'] is None and snapshot['motohours_minutes'] is None
    assert snapshot['engine_on'] is False


@pytest.mark.asyncio
async def test_application_lifespan(client):
    import asyncio
    from app.main import app, lifespan
    from app.services.scheduler import scheduler
    async with lifespan(app):
        assert scheduler.running
        assert (await client.get('/')).status_code == 200
        assert (await client.get('/login')).status_code == 200
    await asyncio.sleep(0)
    assert not scheduler.running
