import re
import shutil
import subprocess
import pytest


@pytest.mark.asyncio
async def test_dashboard_script_executes_without_snapshots(owner):
    if not shutil.which('node'):
        pytest.skip('Node.js is needed for template JavaScript checks')
    html = (await owner.get('/dashboard')).text
    script = next(text for text in re.findall(r'<script>(.*?)</script>', html, re.S) if 'function dashboard()' in text)
    harness = "const document = {addEventListener() {}};\n" + script + "\nconst d = dashboard(); if (d.motohoursDisplay !== '—' || d.latest !== null) throw Error('Invalid empty state');"
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_service_form_preserves_zero(owner):
    if not shutil.which('node'):
        pytest.skip('Node.js is needed for template JavaScript checks')
    html = (await owner.get('/service-plan/page')).text
    script = next(text for text in re.findall(r'<script>(.*?)</script>', html, re.S) if 'function servicePlan()' in text)
    harness = script + "\nconst form = servicePlan(); if (form.optionalNumber(0) !== 0 || form.optionalNumber('0') !== 0 || form.optionalNumber('') !== null) throw Error('Invalid number conversion');"
    result = subprocess.run(['node', '-e', harness], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
