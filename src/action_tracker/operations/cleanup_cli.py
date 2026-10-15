"""Independent operator CLI; never initializes production runtime or daily-run."""
from __future__ import annotations
import argparse
import json
import uuid
from pathlib import Path

from . import cleanup_executor as engine
from .storage_foundation import StorageError


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit evidence/approval storage cleanup')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--context', required=True)
    review = commands.add_parser('review')
    review.add_argument('--candidates', required=True)
    scope = commands.add_parser('capture-references')
    scope.add_argument('--scope', required=True)
    scope.add_argument('--out', required=True)
    decision = commands.add_parser('record-disposition')
    decision.add_argument('--decision', required=True)
    decision.add_argument('--out', required=True)
    plan = commands.add_parser('plan')
    for name in ('candidates', 'references', 'context', 'out'):
        plan.add_argument('--' + name, required=True)
    plan.add_argument('--observation-seconds', type=int, default=86400)
    request = commands.add_parser('approval-request')
    request.add_argument('--plan', required=True)
    request.add_argument('--out', required=True)
    for name in ('rollback-preview', 'purge-preview'):
        transition = commands.add_parser(name)
        transition.add_argument('--previous-plan', required=True)
        transition.add_argument('--out', required=True)
        transition.add_argument('--references')
        transition.add_argument('--observation')
    execute = commands.add_parser('execute')
    for name in ('plan', 'approval', 'approval-sha256', 'out'):
        execute.add_argument('--' + name, required=True)
    execute.add_argument('--enable-execution', action='store_true', default=False)
    args = parser.parse_args(argv)
    try:
        if getattr(args, 'out', None):
            output = engine.safe_path(args.out, must_exist=False)
            if output.exists():
                raise StorageError('OUTPUT_ALREADY_EXISTS')
            if not output.parent.is_dir():
                raise StorageError('OUTPUT_DIRECTORY_REQUIRED')
            probe = output.parent / ('.report-probe-' + uuid.uuid4().hex)
            try:
                with probe.open('xb') as stream:
                    stream.write(b'output permission probe')
            finally:
                if probe.exists():
                    probe.unlink()
        if args.command == 'init':
            context = engine.read_json(args.context)
            result = engine.initialize_root(Path(context['state_root']), context['protected_roots'])
        elif args.command == 'review':
            result = engine.review_candidates(engine.read_json(args.candidates))
        elif args.command == 'capture-references':
            scope = engine.read_json(args.scope)
            result = engine.capture_reference_scope(scope['domains'], reviewer=scope['reviewer'], scope_reason=scope['scope_reason'])
        elif args.command == 'record-disposition':
            result = engine.record_disposition(engine.read_json(args.decision))
        elif args.command == 'plan':
            result = engine.prepare_plan(engine.read_json(args.candidates), engine.read_json(args.references),
                engine.read_json(args.context), observation_seconds=args.observation_seconds)
        elif args.command == 'approval-request':
            result = engine.approval_request(engine.read_json(args.plan))
        elif args.command in {'rollback-preview', 'purge-preview'}:
            result = engine.transition_plan(engine.read_json(args.previous_plan),
                action='ROLLBACK' if args.command == 'rollback-preview' else 'PURGE',
                observation=engine.read_json(args.observation) if args.observation else None,
                references=engine.read_json(args.references) if args.references else None)
        else:
            result = engine.execute(engine.read_json(args.plan), engine.read_json(args.approval),
                approval_sha256=args.approval_sha256, enabled=args.enable_execution)
        if getattr(args, 'out', None):
            try:
                engine.atomic_new(Path(args.out), result)
            except OSError as exc:
                print(json.dumps({'status': 'RESULT_WITH_REPORT_WRITE_FAILURE',
                    'report_error': type(exc).__name__, 'result': result}, ensure_ascii=False, indent=2))
                return 3
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (StorageError, OSError, ValueError, KeyError, TypeError) as exc:
        # File paths can be operator-visible; arbitrary exception content is not a report contract.
        reason = str(exc) if isinstance(exc, StorageError) else type(exc).__name__
        print(json.dumps(getattr(exc, 'operation_state', {'status': 'BLOCKED', 'reason': reason,
                                                        'production_modified': False}), ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
