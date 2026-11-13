"""Explicit owned-pool CLI; never loads production settings or enables Kopia."""
import json
import sys
from pathlib import Path

from .backup_pool import BackupPool, PoolCapacityError, PoolCleanupError
from .storage_foundation import StorageError


def register(sub):
    p=sub.add_parser('backup-pool',help='统一本地备份池：实际占用、恢复保护及5GiB自动容量管理')
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--protected-path',type=Path,action='append',default=[])
    actions=p.add_subparsers(dest='pool_action',required=True)
    for name in ('init','status','usage','history','cleanup-preview','cleanup'):
        actions.add_parser(name)
    backup=actions.add_parser('capture')
    backup.add_argument('--source',type=Path,required=True);backup.add_argument('--task-id',required=True)
    backup.add_argument('--code-version',required=True)
    verify=actions.add_parser('verify');verify.add_argument('--asset-id',required=True)
    finish=actions.add_parser('finish-task');finish.add_argument('--task-id',required=True)
    finish.add_argument('--state',choices=('COMPLETED','FAILED','UNKNOWN'),default='COMPLETED')


def run(args):
    try:
        pool=BackupPool(args.root,protected_paths=args.protected_path,initialize=args.pool_action=='init')
        action=args.pool_action
        if action in ('init','status','usage','history'):
            result=pool.status()
            if action=='usage':result={'root':result['root'],'policy':result['policy'],'usage':result['usage'],
                                      'outstanding_reserved_bytes':result['outstanding_reserved_bytes']}
        elif action in ('cleanup','cleanup-preview'):
            result=pool.checkpoint(force_temporaries=True,preview=action=='cleanup-preview')
        elif action=='capture':result=pool.backup(args.source,task_id=args.task_id,code_version=args.code_version)
        elif action=='verify':result=pool.verify(args.asset_id)
        elif action=='finish-task':result=pool.finish_task(args.task_id,state=args.state)
        else:raise StorageError('POOL_ACTION_UNKNOWN')
        print(json.dumps(result,ensure_ascii=False,default=str));return 0
    except (PoolCapacityError,PoolCleanupError) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc),'report':exc.report},ensure_ascii=False),file=sys.stderr);return 2
    except (StorageError,OSError,ValueError,KeyError) as exc:
        print(json.dumps({'status':'BLOCKED','reason':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
