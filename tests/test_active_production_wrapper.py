"""CI_SAFE: temporary Git/data directories; no production or network calls."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / 'scripts/run_active_production.ps1'
PWSH = shutil.which('pwsh') or shutil.which('powershell')
if not PWSH:
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
    PWSH = str(bundled) if bundled.is_file() else None
pytestmark = pytest.mark.skipif(not PWSH, reason='PowerShell is unavailable')
HOSTS = list(dict.fromkeys(host for host in (PWSH, shutil.which('powershell')) if host))


@pytest.fixture
def environment(tmp_path):
    source = tmp_path / '源代码'; source.mkdir()
    subprocess.run(['git', 'init', '-b', 'release/fixture', str(source)], check=True, capture_output=True)
    (source / 'scripts').mkdir()
    (source / 'scripts/run_production_daily.ps1').write_text(
        'param($ProjectRoot,$Date,[switch]$Resume,[switch]$DryRun,$RunId,[switch]$NoNetwork)\n'
        '@{ root=$ProjectRoot; date=$Date; resume=[bool]$Resume; dry=[bool]$DryRun; '
        'run=$RunId; offline=[bool]$NoNetwork } | ConvertTo-Json -Compress\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(source), 'add', '--', 'scripts/run_production_daily.ps1'], check=True)
    subprocess.run(['git', '-C', str(source), '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                    '-c', 'commit.gpgSign=false', 'commit', '-m', 'fixture'], check=True, capture_output=True)
    head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    data = tmp_path / '生产数据'; (data / 'scripts').mkdir(parents=True)
    shutil.copy2(WRAPPER, data / 'scripts/run_production_daily.ps1')
    (data / 'runtime/deployment').mkdir(parents=True)
    deployment = {'source_checkout': str(source), 'source_branch': 'release/fixture',
                  'code_sha': head, 'data_root': str(data)}
    return data, deployment


def invoke(data, deployment, *args, host=PWSH, ensure_ascii=False):
    path = data / 'runtime/deployment/active.json'
    path.write_text(json.dumps(deployment, ensure_ascii=ensure_ascii), encoding='utf-8')
    return subprocess.run([host, '-NoProfile', '-File', str(data / 'scripts/run_production_daily.ps1'),
                           *args], capture_output=True, text=True, encoding='utf-8')


def test_active_wrapper_validates_release_without_invoking_daily(environment):
    data, deployment = environment
    result = invoke(data, deployment, '-ValidateOnly')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['validation'] == 'PASS'
    assert not (data / 'runtime/db').exists()


def test_active_wrapper_forwards_parameters_to_existing_entry(environment):
    data, deployment = environment
    result = invoke(data, deployment, '-Date', '2026-10-23', '-Resume', '-DryRun',
                    '-RunId', 'fixture-run', '-NoNetwork')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'root': str(data), 'date': '2026-10-23', 'resume': True,
                                       'dry': True, 'run': 'fixture-run', 'offline': True}


def test_active_wrapper_rejects_uncommitted_release_code(environment):
    data, deployment = environment
    source = Path(deployment['source_checkout'])
    with (source / 'scripts/run_production_daily.ps1').open('a') as handle:
        handle.write('\n# modified after validation\n')
    result = invoke(data, deployment, '-ValidateOnly')
    assert result.returncode != 0 and 'PRODUCTION_RELEASE_WORKTREE_DIRTY' in result.stderr


@pytest.mark.parametrize('field,value', [('code_sha', '0'*40), ('source_branch', 'release/wrong'),
    ('source_branch', 'feature/unreviewed'), ('source_branch', ''), ('data_root', 'invalid-root')])
def test_active_wrapper_rejects_identity_mismatch(environment, field, value):
    data, deployment = environment
    deployment[field] = value
    assert invoke(data, deployment, '-ValidateOnly').returncode != 0


@pytest.mark.parametrize('host', HOSTS, ids=lambda host: Path(host).stem)
@pytest.mark.parametrize('ensure_ascii', [True, False], ids=['escaped-json', 'utf8-json'])
def test_wrapper_stdout_and_stderr_are_utf8_on_each_available_host(environment, host, ensure_ascii):
    data, deployment = environment
    result = invoke(data, deployment, '-ValidateOnly', host=host, ensure_ascii=ensure_ascii)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload['source_checkout'] == deployment['source_checkout']
    assert payload['data_root'] == str(data)
    assert '源代码' in payload['source_checkout'] and '生产数据' in payload['data_root']
    source = Path(deployment['source_checkout']) / 'scripts/run_production_daily.ps1'
    source.write_text(source.read_text(encoding='utf-8') + '\n# dirty\n', encoding='utf-8')
    rejected = invoke(data, deployment, '-ValidateOnly', host=host, ensure_ascii=ensure_ascii)
    assert rejected.returncode != 0
    assert 'PRODUCTION_RELEASE_WORKTREE_DIRTY' in rejected.stderr
