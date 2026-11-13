"""Manual opt-in local repository roundtrip; no production settings loaded."""
import json,sys,uuid
from pathlib import Path

from .backup_pool import BackupPool
from .backup_operations import OperationPolicy
from .kopia_backend import BackendConfig,BackendCredentials,KopiaBackend
from .pool_kopia import roundtrip,own_root
from .storage_foundation import StorageError


def register(sub):
    p=sub.add_parser('pool-kopia',help='手动复用任务恢复基准进行本地 Kopia 往返验证；默认不执行')
    p.add_argument('--root',required=True,type=Path)
    p.add_argument('--base-asset-id',required=True)
    p.add_argument('--repository-id',required=True)
    p.add_argument('--cli-path',default='kopia')
    p.add_argument('--execute',action='store_true')
    for name in ('master','known','offline'):p.add_argument('--'+name,type=Path)


def run(args):
    if not args.execute:
        print(json.dumps({'status':'DISABLED','side_effects':False}));return 0
    backend=None
    try:
        projections={k:getattr(args,k) for k in ('master','known','offline') if getattr(args,k)}
        if projections and len(projections)!=3:raise StorageError('CORE_PROJECTIONS_INCOMPLETE')
        pool=BackupPool(args.root)
        root=own_root(pool)
        backend=KopiaBackend(BackendConfig(enabled=True,repository_path=str(root/'repository'),
            expected_repository_id=args.repository_id,cli_path=args.cli_path),
            BackendCredentials.from_environment('filesystem'),root/'sessions'/uuid.uuid4().hex)
        backend.connect()
        result=roundtrip(pool,args.base_asset_id,backend=backend,policy=OperationPolicy(enabled=True),
                         projections=projections)
        print(json.dumps(result,ensure_ascii=False,default=str));return 0
    except (StorageError,OSError,ValueError,KeyError) as exc:
        # Backend errors are closed codes. Do not emit private CLI diagnostics.
        print(json.dumps({'status':'BLOCKED','reason':str(exc)},ensure_ascii=False),file=sys.stderr);return 2
    finally:
        if backend is not None and backend._connected:backend.disconnect()
