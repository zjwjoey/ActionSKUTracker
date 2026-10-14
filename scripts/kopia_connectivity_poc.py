"""Explicit non-production preflight. Never creates or writes a cloud repository."""
from __future__ import annotations

import argparse
import json
import platform
from pathlib import Path

from action_tracker.config import load_settings
from action_tracker.operations.kopia_connectivity import ConnectionConfig, cli_environment, connection_test


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli-path", default="kopia")
    parser.add_argument("--output", type=Path, required=True, help="New isolated report directory")
    parser.add_argument("--enable-probe", action="store_true", help="Explicit preflight only; no repository connection")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.is_relative_to(project) or (output / ".git").exists():
        parser.error("Reports must be outside the source checkout")
    settings = load_settings(project_root_override=project)
    output.mkdir(parents=False, exist_ok=False)
    environment = cli_environment(args.cli_path)
    for provider in ("oss", "cos"):
        # Explicit invocation changes only this in-memory PoC config.
        settings["storage_foundation"]["kopia"].update(
            provider=provider, cli_path=args.cli_path, enabled=args.enable_probe)
        config = ConnectionConfig.from_settings(settings)
        result = connection_test(config)
        result.update(windows=platform.platform(), environment=environment,
                      selected_region=config.region, production_files_modified=False,
                      paid_resources_created=False, cloud_bucket_requests_performed=False)
        # Endpoint-only TLS traffic is reported separately from bucket operations.
        result["endpoint_tls_probe_performed"] = bool(result.get("network"))
        (output / f"KOPIA_{provider.upper()}_TEST.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "environment": environment,
                      "cloud_repository_test": "NOT_PERFORMED"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
