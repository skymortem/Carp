from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app.services.maintenance import plan_status, format_hours


def plan(**kwargs):
    values = dict(interval_km=10000, interval_months=12, interval_motohours=250,
        last_mileage_km=100000, last_motohours=60000,
        last_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    return SimpleNamespace(**(values | kwargs))


@pytest.mark.parametrize('km,mh,now', [
    (109000, 60000, datetime(2026, 2, 1, tzinfo=timezone.utc)),
    (100000, 72000, datetime(2026, 2, 1, tzinfo=timezone.utc)),
    (100000, 60000, datetime(2026, 12, 15, tzinfo=timezone.utc)),
])
def test_any_interval_warns(km, mh, now):
    state = plan_status(plan(), km, mh, now)
    assert state['urgent'] and not state['overdue']


@pytest.mark.parametrize('km,mh,now', [
    (110000, 60000, datetime(2026, 2, 1, tzinfo=timezone.utc)),
    (100000, 75000, datetime(2026, 2, 1, tzinfo=timezone.utc)),
    (100000, 60000, datetime(2027, 1, 1, tzinfo=timezone.utc)),
])
def test_any_interval_due(km, mh, now):
    state = plan_status(plan(), km, mh, now)
    assert state['urgent'] and state['overdue']


def test_unknown_and_reset_counters():
    state = plan_status(plan(), None, None, datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert not state['overdue'] and 'нет показаний' in state['status']
    state = plan_status(plan(), 100, 100, datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert state['urgent'] and 'проверьте счетчик' in state['status']


def test_calendar_month_and_exact_deadline():
    p = plan(interval_months=1, interval_km=None, interval_motohours=None,
        last_date=datetime(2024, 1, 31, 12, tzinfo=timezone.utc))
    assert not plan_status(p, None, None, datetime(2024, 2, 29, 11, tzinfo=timezone.utc))['overdue']
    assert plan_status(p, None, None, datetime(2024, 2, 29, 12, tzinfo=timezone.utc))['overdue']
    assert format_hours(9001) == '150 ч 1 мин'
    assert format_hours(0) == '0 ч 0 мин'
