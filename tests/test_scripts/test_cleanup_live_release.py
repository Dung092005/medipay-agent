from database.corpus.cleanup_live_release_for_cutover import clear_runtime_release_pointers


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((statement, params))


def test_cleanup_clears_ops_active_release_foreign_key_pointer():
    cursor = RecordingCursor()

    clear_runtime_release_pointers(cursor, "snapshot-old")

    assert len(cursor.calls) == 1
    statement, params = cursor.calls[0]
    assert "DELETE FROM ops.active_release" in statement
    assert params == ("snapshot-old", "snapshot-old")
