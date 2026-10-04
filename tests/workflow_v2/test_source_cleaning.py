from action_tracker.workflow_v2.source_audit import clean_source_record


def test_cleaning_keeps_numbers(source_row):
    source_row["desc_es"] = "<b>Mesa</b> 10 cm"
    cleaned, audit = clean_source_record(source_row)
    assert cleaned["desc_es"] == "Mesa 10 cm"
    assert audit[-1]["blocked"] is False
