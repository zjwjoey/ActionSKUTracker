from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_action_localization_lite.py"


def _write_source(path: Path, count: int = 160) -> None:
    fields = ["name", "cat1", "cat2", "spec", "description", "details"]
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["batch_id", "sku", "field_name", "source_es", "source_hash", "qa_rule_id", "candidate_status", "old_zh", "candidate_zh"])
        writer.writeheader()
        for i in range(count):
            sku = str(2500000 + i)
            for field in fields:
                writer.writerow({"batch_id": "fixture", "sku": sku, "field_name": field, "source_es": f"Producto {i}" if field == "name" else ("Hogar" if field == "cat1" else "Accesorios" if field == "cat2" else f"Valor {i}"), "source_hash": f"hash-{i}", "qa_rule_id": "" if i % 4 else "NUMERIC_DROPPED", "candidate_status": "REVIEW_REQUIRED" if i % 4 == 0 else "", "old_zh": "", "candidate_zh": ""})


def test_prepare_selects_fixed_sample_without_master_write(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "lite"
    _write_source(source)
    result = subprocess.run([sys.executable, str(RUNNER), "--input", str(source), "--output-dir", str(output), "--mode", "prepare"], cwd=ROOT, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["status"] == "PREPARED"
    assert payload["sku_count"] == 150
    with (output / "lite_150_sample_manifest.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 150
    assert len({row["sku"] for row in rows}) == 150
    assert payload["master_writes"] == 0


def test_translate_without_provider_is_explicitly_not_run(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "lite"
    _write_source(source)
    subprocess.run([sys.executable, str(RUNNER), "--input", str(source), "--output-dir", str(output), "--mode", "translate"], cwd=ROOT, capture_output=True, text=True, check=True)
    with (output / "lite_qwen_results.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 900
    assert {row["status"] for row in rows} == {"NOT_RUN"}
    assert all(row["qwen_zh"] == "" for row in rows)


def test_review_routes_missing_provider_to_owner_queue(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "lite"
    _write_source(source)
    subprocess.run([sys.executable, str(RUNNER), "--input", str(source), "--output-dir", str(output), "--mode", "all"], cwd=ROOT, capture_output=True, text=True, check=True)
    summary = json.loads((output / "lite_run_summary.json").read_text(encoding="utf-8"))
    assert summary["master_writes"] == 0
    assert summary["production_apply"] is False
    assert summary["review_required"] == 900


def test_translate_resume_and_review_with_fixture_provider(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "lite"
    _write_source(source)
    # Import the side-channel functions without invoking network code.
    sys.path.insert(0, str(ROOT / "src"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("lite_runner", RUNNER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    rows = module.read_csv(source)
    grouped = module.group_source_rows(rows)
    sample = module.choose_sample(grouped, rows, output)
    class Fixture:
        provider = "fixture"
        model = "fixture"
        calls = 0
        def translate(self, request):
            from action_tracker.localization.providers.base import TranslationResponse
            self.calls += 1
            text = request.source_text
            return TranslationResponse({request.field_name: f"中文{text}"}, self.provider, self.model, request.source_hash, "req", "resp", request.request_id)
    provider = Fixture()
    translated = module.translate_sample(sample, grouped, output, provider, 0)
    assert provider.calls == 900
    reviewed = module.review_rows(translated, grouped, output)
    assert len(reviewed) == 900
    assert {row["review_decision"] for row in reviewed} == {"KEEP"}
    provider2 = Fixture()
    module.translate_sample(sample, grouped, output, provider2, 0)
    assert provider2.calls == 0


def test_source_fields_remain_immutable_during_lite_review(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    output = tmp_path / "lite"
    _write_source(source)
    before = source.read_bytes()
    subprocess.run(
        [sys.executable, str(RUNNER), "--input", str(source), "--output-dir", str(output), "--mode", "all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert source.read_bytes() == before
    with (output / "lite_review_rows.csv").open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 900
    assert all(row["source_es"] for row in rows)
