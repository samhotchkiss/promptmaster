from promptmaster.messaging import close_message, create_message, ensure_inbox, list_open_messages


def test_file_backed_inbox_round_trip(tmp_path) -> None:
    root = ensure_inbox(tmp_path)

    message_path = create_message(tmp_path, sender="pa", subject="Review needed", body="Issue 012 is ready.")
    assert message_path.exists()
    assert (root.parent / ".gitignore").read_text().strip().endswith("inbox/")

    messages = list_open_messages(tmp_path)
    assert len(messages) == 1
    assert messages[0].subject == "Review needed"
    assert messages[0].sender == "pa"

    archived = close_message(tmp_path, message_path.name)
    assert archived.exists()
    assert list_open_messages(tmp_path) == []
