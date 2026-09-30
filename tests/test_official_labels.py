import csv
import io

from action_tracker.exporting.official_labels import build_official_label_rows, render_official_label_sidecar


def test_official_labels_keep_raw_identity_and_unknown_tokens():
    rows = build_official_label_rows([{"sku": "1", "raw_tags": "Nuevo|Una opción más sostenible|MysteryLabel"}])
    assert [row["raw_label"] for row in rows] == ["Nuevo", "Una opción más sostenible", "MysteryLabel"]
    assert rows[0]["label_type"] == "new"
    assert rows[1]["label_type"] == "sustainable"
    assert rows[2]["normalized_label"] == "MysteryLabel"
    payload = render_official_label_sidecar(rows).decode("utf-8-sig")
    parsed = list(csv.DictReader(io.StringIO(payload)))
    assert [row["raw_label"] for row in parsed] == ["Nuevo", "Una opción más sostenible", "MysteryLabel"]
