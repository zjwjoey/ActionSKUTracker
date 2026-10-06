from pathlib import Path

import pytest

from action_tracker.exporting import service


def _seed_pair(tmp_path: Path):
    output = tmp_path / "out.xlsx"
    manifest = tmp_path / "out.manifest.json"
    output.write_bytes(b"old workbook")
    manifest.write_text("old manifest", encoding="utf-8")
    preview = tmp_path / "preview.xlsx"
    preview.write_bytes(b"new workbook")
    return preview, output, manifest


def test_backup_output_failure_preserves_old_pair(tmp_path, monkeypatch):
    preview, output, manifest = _seed_pair(tmp_path)
    old_output, old_manifest = output.read_bytes(), manifest.read_bytes()
    real_copy = service.shutil.copy2

    def fail_output(src, dst, *args, **kwargs):
        if Path(src) == output:
            raise OSError("output backup failed")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(service.shutil, "copy2", fail_output)
    with pytest.raises(OSError):
        service._publish_export_pair(preview, output, manifest, {"x": 1})
    assert output.read_bytes() == old_output
    assert manifest.read_bytes() == old_manifest


def test_backup_manifest_failure_preserves_old_pair(tmp_path, monkeypatch):
    preview, output, manifest = _seed_pair(tmp_path)
    old_output, old_manifest = output.read_bytes(), manifest.read_bytes()
    real_copy = service.shutil.copy2

    def fail_manifest(src, dst, *args, **kwargs):
        if Path(src) == manifest:
            raise OSError("manifest backup failed")
        return real_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(service.shutil, "copy2", fail_manifest)
    with pytest.raises(OSError):
        service._publish_export_pair(preview, output, manifest, {"x": 1})
    assert output.read_bytes() == old_output
    assert manifest.read_bytes() == old_manifest


def test_manifest_replace_failure_rolls_back_pair(tmp_path, monkeypatch):
    preview, output, manifest = _seed_pair(tmp_path)
    old_output, old_manifest = output.read_bytes(), manifest.read_bytes()
    real_replace = Path.replace

    def fail_manifest_replace(self, target):
        if Path(target) == manifest:
            raise OSError("manifest replace failed")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    with pytest.raises(OSError):
        service._publish_export_pair(preview, output, manifest, {"x": 1})
    assert output.read_bytes() == old_output
    assert manifest.read_bytes() == old_manifest


def test_new_pair_failure_leaves_no_half_pair(tmp_path, monkeypatch):
    preview = tmp_path / "preview.xlsx"
    output = tmp_path / "out.xlsx"
    manifest = tmp_path / "out.manifest.json"
    preview.write_bytes(b"new workbook")
    real_replace = Path.replace

    def fail_manifest_replace(self, target):
        if Path(target) == manifest:
            raise OSError("manifest replace failed")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_manifest_replace)
    with pytest.raises(OSError):
        service._publish_export_pair(preview, output, manifest, {"x": 1})
    assert not output.exists()
    assert not manifest.exists()


def test_repair_report_replace_failure_rolls_back_complete_chinese_bundle(tmp_path, monkeypatch):
    preview, output, manifest = _seed_pair(tmp_path)
    repair = tmp_path / "out.repair-report.json"
    repair.write_text("old repair", encoding="utf-8")
    old = (output.read_bytes(), manifest.read_bytes(), repair.read_bytes())
    real_replace = Path.replace

    failed = False

    def fail_repair_replace(self, target):
        nonlocal failed
        if Path(target) == repair and not failed:
            failed = True
            raise OSError("repair replace failed")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_repair_replace)
    with pytest.raises(OSError):
        service._publish_export_bundle(
            preview, output, manifest, {"x": 1},
            repair_report_path=repair, repair_report={"repairs": []},
        )
    assert (output.read_bytes(), manifest.read_bytes(), repair.read_bytes()) == old
