from action_tracker.cli import build_parser


def test_saved_view_cli_exposes_update_and_delete():
    parser = build_parser()
    update = parser.parse_args(["saved-view", "update", "view_1", "--name", "当前低价"])
    delete = parser.parse_args(["saved-view", "delete", "view_1"])
    assert update.saved_view_command == "update" and update.view_id == "view_1"
    assert delete.saved_view_command == "delete" and delete.view_id == "view_1"
    run = parser.parse_args(["saved-view", "run", "view_1", "--json"])
    assert run.saved_view_command == "run" and run.json is True
