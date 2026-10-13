"""Repair browser-confirmed obsolete original prices via the audited API."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path
from action_tracker.config import load_settings
from action_tracker.database.integration import database_path, regenerate_compatibility_exports
from action_tracker.database.price_correction import apply_verified_original_price_absence
from action_tracker.localization.history_audit import verified_backup
from action_tracker.services.runtime import RunLock

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--commit',action='store_true')
    p.add_argument('--verified-backup-report',type=Path)
    a=p.parse_args();cfg=load_settings();e=json.loads(a.evidence.read_text(encoding='utf-8'))
    if not e.get('authorization_text'):raise ValueError('PRICE_CORRECTION_AUTHORIZATION_REQUIRED')
    a.output.mkdir(parents=True,exist_ok=True);lock=RunLock(Path(cfg['paths']['state']));lock.acquire('original-price-clear',command='verified-original-price-clear')
    try:
        kwargs=dict(items=e['items'],expected_base_commit_id=e['expected_base_commit_id'],business_date=e['business_date'])
        preview=apply_verified_original_price_absence(database_path(cfg),dry_run=True,**kwargs)
        (a.output/'preflight.json').write_text(json.dumps(preview,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'phase':'PREFLIGHT','status':preview['status'],'ready_fields':preview.get('ready_fields',0)}),flush=True)
        if not a.commit or preview['status']=='NOOP':return
        if a.verified_backup_report:
            backup=json.loads(a.verified_backup_report.read_text(encoding='utf-8'))
            if backup.get('integrity')!='ok' or backup.get('foreign_key_violations') or backup.get('logical_parity') is not True:
                raise ValueError('PRICE_BACKUP_NOT_VERIFIED')
            digest=hashlib.sha256()
            with Path(backup['backup']).open('rb') as f:
                for block in iter(lambda:f.read(1024*1024),b''):digest.update(block)
            if digest.hexdigest()!=backup['sha256']:raise ValueError('PRICE_BACKUP_DIGEST_CHANGED')
            with closing(sqlite3.connect('file:'+Path(backup['restore']).resolve().as_posix()+'?mode=ro',uri=True)) as db:
                old=db.execute("SELECT commit_id FROM commit_batches WHERE status='COMMITTED' ORDER BY committed_at DESC,commit_id DESC LIMIT 1").fetchone()
            if not old or old[0]!=e['expected_base_commit_id']:raise ValueError('PRICE_BACKUP_BASE_MISMATCH')
        else:
            dest=Path(cfg['paths']['backups'])/'original_price_clear'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'.sqlite3')
            backup=verified_backup(database_path(cfg),dest)
        (a.output/'backup.json').write_text(json.dumps(backup,ensure_ascii=False,indent=2),encoding='utf-8')
        print('{"phase":"BACKUP_RESTORE_VERIFIED"}',flush=True)
        result=apply_verified_original_price_absence(database_path(cfg),**kwargs)
        (a.output/'apply_report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result),flush=True)
        sync=regenerate_compatibility_exports(cfg,result['commit_id'])
        (a.output/'export_sync.json').write_text(json.dumps(sync,ensure_ascii=False,indent=2,default=str),encoding='utf-8');print(json.dumps(sync,default=str),flush=True)
    finally:lock.release()
if __name__=='__main__':main()
