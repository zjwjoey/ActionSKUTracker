"""CI_SAFE: temporary Git release/data roots, no real production or network."""
import hashlib,json,shutil,subprocess,sys
from pathlib import Path
import pytest

WRAPPER=Path(__file__).resolve().parents[1]/'scripts/run_bound_production.ps1'
PWSH=shutil.which('pwsh') or shutil.which('powershell')
if not PWSH:
    bundled=Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe'
    PWSH=str(bundled) if bundled.is_file() else None
pytestmark=pytest.mark.skipif(not PWSH,reason='PowerShell unavailable')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

@pytest.fixture
def bound(tmp_path):
    source=tmp_path/'source';source.mkdir()
    subprocess.run(['git','init','-b','release/fixture',str(source)],check=True,capture_output=True)
    pkg=source/'src/action_tracker';pkg.mkdir(parents=True)
    (pkg/'__init__.py').write_text('',encoding='utf8')
    (pkg/'__main__.py').write_text('import json,os,sys; print(json.dumps({"args":sys.argv[1:],"root":os.environ["ACTION_TRACKER_PROJECT_ROOT"],"output":os.environ.get("ACTION_TRACKER_EXPORT_ROOT")}))',encoding='utf8')
    subprocess.run(['git','-C',str(source),'add','--','src'],check=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@example.test','-c','commit.gpgSign=false','commit','-m','fixture'],check=True,capture_output=True)
    head=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    data=tmp_path/'data'
    for d in ('scripts','config','runtime/deployment'):(data/d).mkdir(parents=True,exist_ok=True)
    script=data/'scripts/run_production.ps1';shutil.copy2(WRAPPER,script)
    config=data/'config/settings.yaml';config.write_text('paths: {}\n',encoding='utf8')
    environment=data/'config/production_environment.ps1';environment.write_text('# fixture process environment\n',encoding='utf8')
    active=dict(data_root=str(data),source_checkout=str(source),source_branch='release/fixture',code_sha=head,
                configuration_files={'config/settings.yaml':sha(config)},entrypoint=str(script),entrypoint_sha256=sha(script),
                launch_environment=dict(path=str(environment),sha256=sha(environment),python_executable=sys.executable))
    (data/'runtime/deployment/active.json').write_text(json.dumps(active),encoding='utf8')
    return data,source,script

def invoke(bound,*args):
    return subprocess.run([PWSH,'-NoProfile','-File',str(bound[2]),*args],capture_output=True,text=True,encoding='utf8')

def test_bound_entry_validates_exact_source_and_config(bound):
    r=invoke(bound,'-Command','validate')
    assert r.returncode==0,r.stderr
    assert json.loads(r.stdout)['configuration_binding']=='PASS'

def test_bound_entry_rejects_config_drift(bound):
    (bound[0]/'config/settings.yaml').write_text('paths: {changed: true}',encoding='utf8')
    r=invoke(bound,'-Command','validate')
    assert r.returncode!=0 and 'PRODUCTION_CONFIG_HASH_MISMATCH' in r.stderr

def test_bound_entry_rejects_dirty_release(bound):
    (bound[1]/'extra.py').write_text('print(1)',encoding='utf8')
    r=invoke(bound,'-Command','validate')
    assert r.returncode!=0 and 'PRODUCTION_RELEASE_WORKTREE_DIRTY' in r.stderr

def test_export_uses_same_bound_entry_and_isolated_output(bound,tmp_path):
    out=tmp_path/'isolated-exports'
    r=invoke(bound,'-Command','export-history','-Date','2026-11-09','-OutputDirectory',str(out))
    assert r.returncode==0,r.stderr
    payload=json.loads(r.stdout)
    assert payload['args']==['export-history','--date','2026-11-09']
    assert Path(payload['root'])==bound[0]
    assert Path(payload['output'])==out
