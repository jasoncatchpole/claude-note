"""Tests for note.py. Run: python3 -m unittest -v test_note"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import note  # noqa: E402


class TempDBMixin:
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="note-test-")
        self.db = Path(self.tmpdir) / "notes.db"
        self._saved_env = os.environ.get("NOTE_DB")
        os.environ["NOTE_DB"] = str(self.db)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("NOTE_DB", None)
        else:
            os.environ["NOTE_DB"] = self._saved_env
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestCRUD(TempDBMixin, unittest.TestCase):
    def test_add_returns_id_and_persists(self):
        id1 = note.add_note("audiobook", "idea one")
        id2 = note.add_note("audiobook", "idea two")
        self.assertEqual(id1, 1)
        self.assertEqual(id2, 2)
        rows, total = note.list_notes()
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)

    def test_add_rejects_empty_topic(self):
        with self.assertRaises(ValueError):
            note.add_note("", "idea")
        with self.assertRaises(ValueError):
            note.add_note("   ", "idea")

    def test_add_rejects_empty_idea(self):
        with self.assertRaises(ValueError):
            note.add_note("topic", "")

    def test_list_empty_returns_no_notes(self):
        rows, total = note.list_notes()
        self.assertEqual(rows, [])
        self.assertEqual(total, 0)
        self.assertEqual(note.format_list(rows, total, 20), "no notes")

    def test_list_filters_by_topic_case_insensitive(self):
        note.add_note("AudioBook", "one")
        note.add_note("work", "two")
        note.add_note("audiobook", "three")
        rows, total = note.list_notes(topic="audiobook")
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 2)

    def test_list_respects_limit_and_all(self):
        for i in range(30):
            note.add_note("t", f"idea{i}")
        rows, total = note.list_notes(limit=10)
        self.assertEqual(len(rows), 10)
        self.assertEqual(total, 30)
        rows_all, total_all = note.list_notes(limit=None)
        self.assertEqual(len(rows_all), 30)
        self.assertEqual(total_all, 30)

    def test_list_orders_newest_first(self):
        note.add_note("t", "first")
        note.add_note("t", "second")
        note.add_note("t", "third")
        rows, _ = note.list_notes()
        self.assertEqual(rows[0][3], "third")
        self.assertEqual(rows[-1][3], "first")

    def test_view_returns_full_body(self):
        body = "line one\nline two\nline three"
        nid = note.add_note("multi", body)
        row = note.view_note(nid)
        self.assertEqual(row[1], "multi")
        self.assertEqual(row[4], body)

    def test_view_missing_id_returns_none(self):
        self.assertIsNone(note.view_note(999))

    def test_rm_deletes_row(self):
        nid = note.add_note("t", "i")
        self.assertTrue(note.rm_note(nid))
        self.assertIsNone(note.view_note(nid))

    def test_rm_missing_id_returns_false(self):
        self.assertFalse(note.rm_note(999))

    def test_unicode_topic_and_idea_roundtrip(self):
        nid = note.add_note("日本語", "idea with emoji 🎉 and accents é")
        row = note.view_note(nid)
        self.assertEqual(row[1], "日本語")
        self.assertIn("🎉", row[4])

    def test_project_dir_stored(self):
        nid = note.add_note("t", "i", project_dir="/some/path")
        row = note.view_note(nid)
        self.assertEqual(row[3], "/some/path")


class TestParsing(unittest.TestCase):
    def test_add_basic(self):
        self.assertEqual(
            note.parse_note_command("/note foo bar baz"),
            ("add", {"topic": "foo", "idea": "bar baz"}),
        )

    def test_add_single_token_is_error(self):
        action, kw = note.parse_note_command("/note foo")
        self.assertEqual(action, "error")

    def test_list_bare(self):
        self.assertEqual(
            note.parse_note_command("/note list"),
            ("list", {"topic": None}),
        )

    def test_list_with_topic(self):
        self.assertEqual(
            note.parse_note_command("/note list audiobook"),
            ("list", {"topic": "audiobook"}),
        )

    def test_list_three_tokens_is_add(self):
        self.assertEqual(
            note.parse_note_command("/note list buy milk tomorrow"),
            ("add", {"topic": "list", "idea": "buy milk tomorrow"}),
        )

    def test_rm_numeric_is_subcommand(self):
        self.assertEqual(
            note.parse_note_command("/note rm 42"),
            ("rm", {"id": 42}),
        )

    def test_rm_nonnumeric_is_add(self):
        self.assertEqual(
            note.parse_note_command("/note rm foo bar"),
            ("add", {"topic": "rm", "idea": "foo bar"}),
        )

    def test_view_numeric(self):
        self.assertEqual(
            note.parse_note_command("/note view 7"),
            ("view", {"id": 7}),
        )

    def test_explicit_add(self):
        self.assertEqual(
            note.parse_note_command("/note add list stuff to do"),
            ("add", {"topic": "list", "idea": "stuff to do"}),
        )

    def test_leading_whitespace(self):
        self.assertEqual(
            note.parse_note_command("   /note foo bar"),
            ("add", {"topic": "foo", "idea": "bar"}),
        )

    def test_multiline_idea_preserved(self):
        action, kw = note.parse_note_command("/note foo line1\nline2\nline3")
        self.assertEqual(action, "add")
        self.assertEqual(kw["topic"], "foo")
        self.assertIn("line1", kw["idea"])
        self.assertIn("line3", kw["idea"])

    def test_special_chars_survive(self):
        action, kw = note.parse_note_command("/note sql DROP TABLE users; --")
        self.assertEqual(action, "add")
        self.assertEqual(kw["topic"], "sql")
        self.assertIn("DROP TABLE", kw["idea"])

    def test_non_note_prompt_returns_none(self):
        self.assertIsNone(note.parse_note_command("hello world"))
        self.assertIsNone(note.parse_note_command(""))
        self.assertIsNone(note.parse_note_command(None))

    def test_note_not_on_first_line_returns_none(self):
        self.assertIsNone(note.parse_note_command("fix bug\n/note foo bar"))

    def test_bare_note_returns_help(self):
        self.assertEqual(note.parse_note_command("/note"), ("help", {}))
        self.assertEqual(note.parse_note_command("/note "), ("help", {}))

    def test_notebook_prefix_does_not_match(self):
        # /notebook shouldn't trigger /note
        self.assertIsNone(note.parse_note_command("/notebook foo"))

    def test_marker_form_basic(self):
        # When the stub slash command expands, the prompt arrives as
        # "__NOTE_HOOK__ <args>". Hook must accept this form too.
        self.assertEqual(
            note.parse_note_command("__NOTE_HOOK__ audiobook test idea"),
            ("add", {"topic": "audiobook", "idea": "test idea"}),
        )

    def test_marker_form_list(self):
        self.assertEqual(
            note.parse_note_command("__NOTE_HOOK__ list audiobook"),
            ("list", {"topic": "audiobook"}),
        )

    def test_marker_form_bare_is_help(self):
        self.assertEqual(note.parse_note_command("__NOTE_HOOK__"), ("help", {}))

    def test_marker_prefix_alone_does_not_match(self):
        # __NOTE_HOOK__SOMETHING (no \b) should not match.
        self.assertIsNone(note.parse_note_command("__NOTE_HOOK__SUFFIX foo"))


class TestHookMode(TempDBMixin, unittest.TestCase):
    def test_passthrough_on_non_note(self):
        out, code = note.hook_main(json.dumps({"prompt": "hello"}))
        self.assertEqual(out, "{}")
        self.assertEqual(code, 0)

    def test_block_on_add_emits_correct_json(self):
        out, code = note.hook_main(json.dumps({"prompt": "/note audio test gemini"}))
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("saved #1", data["reason"])
        self.assertIn("audio", data["reason"])

    def test_block_captures_cwd_as_project_dir(self):
        note.hook_main(json.dumps({"prompt": "/note t i", "cwd": "/home/user/src"}))
        row = note.view_note(1)
        self.assertEqual(row[3], "/home/user/src")

    def test_block_on_list_formats_reason_multiline(self):
        note.add_note("a", "one")
        note.add_note("b", "two")
        out, _ = note.hook_main(json.dumps({"prompt": "/note list"}))
        data = json.loads(out)
        self.assertIn("\n", data["reason"])

    def test_malformed_stdin_is_passthrough(self):
        out, code = note.hook_main("not json at all")
        self.assertEqual(out, "{}")
        self.assertEqual(code, 0)

    def test_empty_stdin_is_passthrough(self):
        out, code = note.hook_main("")
        self.assertEqual(out, "{}")
        self.assertEqual(code, 0)

    def test_db_exception_is_passthrough(self):
        with mock.patch.object(note, "connect", side_effect=RuntimeError("boom")):
            out, code = note.hook_main(json.dumps({"prompt": "/note foo bar"}))
            self.assertEqual(out, "{}")
            self.assertEqual(code, 0)

    def test_view_missing_id_reports_error_in_reason(self):
        out, _ = note.hook_main(json.dumps({"prompt": "/note view 999"}))
        data = json.loads(out)
        self.assertEqual(data["decision"], "block")
        self.assertIn("no note", data["reason"])

    def test_bare_note_emits_help(self):
        out, _ = note.hook_main(json.dumps({"prompt": "/note"}))
        data = json.loads(out)
        self.assertIn("usage:", data["reason"])


class TestEndToEnd(TempDBMixin, unittest.TestCase):
    def _run_hook(self, prompt: str):
        env = os.environ.copy()
        env["NOTE_DB"] = str(self.db)
        return subprocess.run(
            [sys.executable, str(HERE / "note.py"), "hook"],
            input=json.dumps({"prompt": prompt}),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def test_subprocess_add_writes_row(self):
        result = self._run_hook("/note work ship it")
        self.assertEqual(result.returncode, 0)
        data = json.loads(result.stdout.strip())
        self.assertEqual(data["decision"], "block")
        row = note.view_note(1)
        self.assertEqual(row[1], "work")
        self.assertEqual(row[4], "ship it")

    def test_subprocess_passthrough(self):
        result = self._run_hook("hello world")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "{}")
        rows, total = note.list_notes()
        self.assertEqual(total, 0)

    def test_cli_add_and_list(self):
        env = os.environ.copy()
        env["NOTE_DB"] = str(self.db)
        r1 = subprocess.run(
            [sys.executable, str(HERE / "note.py"), "add", "x", "first", "idea"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(r1.returncode, 0)
        self.assertIn("saved #1", r1.stdout)
        r2 = subprocess.run(
            [sys.executable, str(HERE / "note.py"), "list"],
            capture_output=True, text=True, env=env, timeout=10,
        )
        self.assertEqual(r2.returncode, 0)
        self.assertIn("first idea", r2.stdout)


class TestPathResolution(unittest.TestCase):
    def test_NOTE_DB_env_override(self):
        with mock.patch.dict(os.environ, {"NOTE_DB": "/custom/path/notes.db"}):
            self.assertEqual(str(note.db_path()), "/custom/path/notes.db")

    def test_default_is_sibling_of_script_parent(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTE_DB", None)
            p = note.db_path()
            expected = Path(note.__file__).resolve().parent.parent / "notes.db"
            self.assertEqual(p, expected)

    def test_parent_dir_autocreated(self):
        tmpdir = tempfile.mkdtemp(prefix="note-path-")
        try:
            nested = Path(tmpdir) / "a" / "b" / "c" / "notes.db"
            with mock.patch.dict(os.environ, {"NOTE_DB": str(nested)}):
                note.add_note("t", "i")
                self.assertTrue(nested.exists())
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
