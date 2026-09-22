from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAINTAINED = (
    ROOT / "scripts" / "audit_tm_v1_staging.py",
    ROOT / "scripts" / "audit_translation_memory_phase_a.py",
    ROOT / "scripts" / "build_grouped_source_fixture.py",
)


def test_maintained_tools_are_repo_relative_and_parameterized():
    for path in MAINTAINED:
        text = path.read_text(encoding="utf-8")
        assert "F:/" not in text and "F:\\" not in text
        assert "D:\\Users" not in text and "C:\\Users" not in text
        assert "argparse.ArgumentParser" in text
        assert "production" in text.lower()


def test_maintained_tools_do_not_contain_production_apply_operations():
    for path in MAINTAINED:
        text = path.read_text(encoding="utf-8")
        assert "os.replace(" not in text
        assert "shutil.copy2" not in text
        assert "--commit" not in text


def test_grouped_fixture_requires_explicit_input_and_output():
    text = (ROOT / "scripts" / "build_grouped_source_fixture.py").read_text(encoding="utf-8")
    assert 'parser.add_argument("--input", required=True)' in text
    assert 'parser.add_argument("--output", required=True)' in text
