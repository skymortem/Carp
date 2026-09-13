import os
import sys
import tempfile
from pathlib import Path

# Import the application only after isolating configuration and the database.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_sandbox = tempfile.TemporaryDirectory(prefix='carp-tests-')
os.environ['SECRET_KEY'] = 'test-only-secret-key-with-at-least-32-characters'
os.environ['DATABASE_URL'] = 'sqlite+aiosqlite:///' + _sandbox.name + '/test.db'
for key in ('STARLINE_APP_ID', 'STARLINE_APP_SECRET', 'STARLINE_LOGIN', 'STARLINE_PASSWORD'):
    os.environ[key] = ''

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from app.main import app
from app.database import engine, Base, async_session
from app.models.user import User
from app.models.car import Car
from app.services.auth import create_jwt, hash_password


@pytest_asyncio.fixture
async def client():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
        yield client
    await engine.dispose()


@pytest_asyncio.fixture
async def owner(client):
    async with async_session() as db:
        db.add(User(id=1, email='owner@example.test', username='Owner', hashed_password=hash_password('secret123')))
        db.add(Car(id=1, user_id=1, name='Test car', starline_device_id='device1'))
        await db.commit()
    client.cookies.set('access_token', create_jwt(1))
    return client
