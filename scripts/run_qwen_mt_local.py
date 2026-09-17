from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_ENDPOINT = "https://ws-5luepildpfsg40gc.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a read-only Qwen-MT local smoke test")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument(
        "--data-root",
        default="",
        help="可选的运行数据根目录；适用于代码 worktree 与 SQLite 生产数据目录分离的情况",
    )
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--mode", choices=("smoke", "canary"), default="smoke")
    parser.add_argument("--field", default="", help="可选字段；留空则按全部可翻译字段执行")
    parser.add_argument("--keep-proxy", action="store_true")
    args = parser.parse_args()

    repo = Path(args.repo_root).resolve()
    if not (repo / "src" / "action_tracker" / "__main__.py").exists():
        raise SystemExit(f"不是有效的 ActionSKUTracker 项目目录：{repo}")

    key = getpass.getpass("请输入 DASHSCOPE_API_KEY（输入时隐藏）：").strip()
    if not key:
        raise SystemExit("DASHSCOPE_API_KEY 不能为空")

    env = os.environ.copy()
    env["DASHSCOPE_API_KEY"] = key
    env["QWEN_MT_BASE_URL"] = args.endpoint.rstrip("/")
    env["DASHSCOPE_BASE_URL"] = env["QWEN_MT_BASE_URL"]
    if args.mode == "canary":
        # Explicit, process-scoped opt-in.  It does not alter settings.yaml
        # and cannot enable production writes.
        env["ACTION_TRACKER_ALLOW_QWEN_PROVIDER"] = "1"
    if args.data_root:
        data_root = Path(args.data_root).resolve()
        if not data_root.exists():
            raise SystemExit(f"数据根目录不存在：{data_root}")
        # The source code stays in --repo-root, while settings/runtime/DB are
        # resolved from this explicit data root.  This avoids accidentally
        # creating or reading an empty SQLite database in a worktree.
        env["ACTION_TRACKER_PROJECT_ROOT"] = str(data_root)
    # The compatible endpoint is already workspace-bound; do not inherit an
    # old workspace header from the user's shell.
    env.pop("DASHSCOPE_WORKSPACE", None)
    env["PYTHONPATH"] = str(repo / "src")
    if not args.keep_proxy:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
            env.pop(name, None)
        env["NO_PROXY"] = "*"

    output_name = "qwen_live_smoke.json" if args.mode == "smoke" else "qwen_canary.json"
    output = repo / "runtime" / "temp" / output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_dir():
        # The CLI's canary contract takes an output *directory*; clean only
        # this generated artifact directory before rerunning.
        shutil.rmtree(output)
    elif output.exists():
        output.unlink()
    python = sys.executable
    print(f"Endpoint: {env['QWEN_MT_BASE_URL']}")
    print("Model: qwen-mt-flash")
    print("Production writes: false")

    if args.mode == "smoke":
        command = [python, "-m", "action_tracker", "localization-live-smoke", "--limit", str(args.limit), "--output", str(output)]
    else:
        command = [python, "-m", "action_tracker", "localization-canary", "--limit", str(args.limit), "--provider", "--output", str(output)]
        if args.field:
            command.extend(["--field", args.field])
    smoke = subprocess.run(command, cwd=repo, env=env, check=False)
    report = output / "translation_run_summary.json" if output.is_dir() else output
    if report.exists():
        print("\nCanary report:" if args.mode == "canary" else "\nSmoke report:")
        try:
            print(json.dumps(json.loads(report.read_text(encoding="utf-8")), ensure_ascii=False, indent=2))
        except Exception:
            print(report.read_text(encoding="utf-8", errors="replace"))
    return smoke.returncode


if __name__ == "__main__":
    raise SystemExit(main())
