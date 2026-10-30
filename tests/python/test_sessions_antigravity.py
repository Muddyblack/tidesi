import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support import REPO  # noqa: F401
from tidesi import sessions
from tidesi.providers import antigravity_sessions

_CLI_ID = "11111111-1111-4111-8111-111111111111"
_IDE_ID = "22222222-2222-4222-8222-222222222222"


def _write_cli(root: str, session_id: str, content: str, name: str = "transcript.jsonl") -> str:
    path = Path(root) / "antigravity-cli" / "brain" / session_id / ".system_generated" / "logs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.utime(path, (1_767_261_900, 1_767_261_900))
    return str(path)


def _write_ide(root: str, session_id: str, content: str, name: str = "task.md") -> str:
    path = Path(root) / "antigravity" / "brain" / session_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


class AntigravityDiscoveryTest(unittest.TestCase):
    def test_transcript_reads_stop_at_line_or_total_byte_budget(self):
        first = json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "Safe title"}).encode() + b"\n"
        for oversized_line in (False, True):
            with self.subTest(oversized_line=oversized_line):
                tail = b"x" * 10_000 if oversized_line else first * 100
                stream = io.BytesIO(first + tail)
                reader = mock.MagicMock(wraps=stream)
                reader.__enter__.return_value = reader
                budget = 2 * len(first)
                with (
                    mock.patch.object(Path, "open", return_value=reader),
                    mock.patch.object(antigravity_sessions, "_mtime", return_value=100),
                    mock.patch.object(antigravity_sessions, "_MAX_LINE_BYTES", len(first)),
                    mock.patch.object(antigravity_sessions, "_MAX_TRANSCRIPT_BYTES", budget),
                ):
                    record = antigravity_sessions._read_cli(_CLI_ID, Path("unused.jsonl"))
                self.assertEqual(record.title, "Safe title")
                self.assertLessEqual(stream.tell(), budget + 1)
                self.assertEqual(reader.readline.call_count, 2)

    def test_editor_reads_only_title_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            artifact = Path(_write_ide(root, _IDE_ID, "# Safe heading\n"))
            stream = io.StringIO("# Safe heading\n" + "x" * 100_000)
            reader = mock.MagicMock(wraps=stream)
            reader.__enter__.return_value = reader
            with mock.patch.object(Path, "open", return_value=reader):
                record = antigravity_sessions._read_editor(_IDE_ID, artifact.parent)
            self.assertEqual(record.title, "Safe heading")
            self.assertLessEqual(stream.tell(), antigravity_sessions._MAX_TITLE_CHARS)

    def test_reads_cli_transcript_and_ide_artifact_without_claiming_foreign_jsonl(self):
        transcript = "\n".join(
            [
                json.dumps(
                    {
                        "step_index": 0,
                        "type": "USER_INPUT",
                        "created_at": "2026-01-01T10:00:00Z",
                        "content": "<USER_REQUEST>Build the widget</USER_REQUEST>",
                    }
                ),
                json.dumps(
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "created_at": "2026-01-01T10:05:00Z",
                        "content": "plan",
                    }
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as root:
            cli_path = _write_cli(root, _CLI_ID, transcript)
            _write_cli(root, "33333333-3333-4333-8333-333333333333", '{"type":"USER_INPUT"}')
            _write_ide(root, _IDE_ID, "# Fix the installer\n\nDetails")
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_HOME": root}, clear=True):
                records = antigravity_sessions.read_recent_sessions()

        self.assertEqual({record.session_id for record in records}, {_CLI_ID, _IDE_ID})
        cli = next(record for record in records if record.session_id == _CLI_ID)
        self.assertEqual(cli.title, "Build the widget")
        self.assertEqual(cli.last_activity, 1_767_261_900)
        self.assertEqual(cli.source_path, cli_path)
        ide = next(record for record in records if record.session_id == _IDE_ID)
        self.assertEqual(ide.title, "Fix the installer")

    def test_reads_the_antigravity_ide_store_like_the_cli(self):
        transcript = json.dumps({"step_index": 0, "type": "USER_INPUT", "content": "From the IDE store"})
        with tempfile.TemporaryDirectory() as root:
            path = Path(_write_cli(root, _IDE_ID, transcript))
            ide_path = Path(root) / "antigravity-ide" / path.relative_to(Path(root) / "antigravity-cli")
            ide_path.parent.mkdir(parents=True)
            path.rename(ide_path)
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_HOME": root}, clear=True):
                records = antigravity_sessions.read_recent_sessions()

        self.assertEqual([(record.session_id, record.title) for record in records], [(_IDE_ID, "From the IDE store")])

    def test_deduplicates_cli_and_ide_copies_by_newest_activity(self):
        transcript = json.dumps(
            {
                "step_index": 0,
                "type": "USER_INPUT",
                "created_at": "2026-01-01T10:00:00Z",
                "content": "<USER_REQUEST>CLI title</USER_REQUEST>",
            }
        )
        with tempfile.TemporaryDirectory() as root:
            _write_cli(root, _CLI_ID, transcript)
            ide_path = _write_ide(root, _CLI_ID, "# IDE title")
            os.utime(ide_path, (2_000_000_000, 2_000_000_000))
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_HOME": root}, clear=True):
                records = antigravity_sessions.read_recent_sessions()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].title, "IDE title")

    def test_caps_recent_records_and_rejects_invalid_ids(self):
        with tempfile.TemporaryDirectory() as root:
            for index in range(61):
                session_id = f"{index:08d}-0000-4000-8000-000000000000"
                _write_ide(root, session_id, f"# Session {index}")
            _write_ide(root, "not-a-uuid", "# Invalid")
            with mock.patch.dict(os.environ, {"ANTIGRAVITY_HOME": root}, clear=True):
                records = antigravity_sessions.read_recent_sessions()

        self.assertEqual(len(records), 60)
        self.assertNotIn("not-a-uuid", {record.session_id for record in records})


class AntigravitySessionBoundaryTest(unittest.TestCase):
    def test_unverified_usage_fields_remain_outside_metadata_contract(self):
        transcript = json.dumps(
            {
                "step_index": 0,
                "type": "MODEL_RESPONSE",
                "model": "gemini-test",
                "usage": {"input_tokens": 1000, "output_tokens": 200},
            }
        )
        with tempfile.TemporaryDirectory() as root:
            path = Path(_write_cli(root, _CLI_ID, transcript))
            record = antigravity_sessions._read_cli(_CLI_ID, path)
        with mock.patch.object(antigravity_sessions, "read_recent_sessions", return_value=[record]):
            entry = sessions._antigravity_entries()[0]

        self.assertEqual(entry["costStatus"], "unavailable")
        self.assertNotIn("costUSD", entry)

    def test_public_entry_redacts_source_path_and_session_id(self):
        record = antigravity_sessions.AntigravitySession(
            session_id=_CLI_ID,
            title="A useful title",
            last_activity=2_000,
            source_path="/private/.gemini/antigravity-cli/brain/" + _CLI_ID,
        )
        with mock.patch.object(antigravity_sessions, "read_recent_sessions", return_value=[record]):
            entry = sessions._antigravity_entries()[0]

        encoded = json.dumps(entry)
        self.assertEqual(entry["provider"], "antigravity")
        self.assertEqual(entry["sessionName"], "Antigravity")
        self.assertEqual(entry["costStatus"], "unavailable")
        self.assertNotIn(record.source_path, encoded)
        self.assertNotIn(_CLI_ID, encoded)
        self.assertRegex(entry["openKey"], r"^[0-9a-f]{64}$")

    def test_target_keeps_real_uuid_for_resume_but_uses_opaque_key(self):
        record = antigravity_sessions.AntigravitySession(
            session_id=_CLI_ID,
            title="Title",
            last_activity=2_000,
            source_path="/private/transcript.jsonl",
        )
        with (
            mock.patch.object(antigravity_sessions, "read_session_targets", return_value=[record]),
            mock.patch.object(sessions, "_codex_targets", return_value=[]),
            mock.patch.object(sessions, "_grok_targets", return_value=[]),
            mock.patch.object(sessions, "_claude_targets", return_value=[]),
            mock.patch.object(sessions, "_cline_targets", return_value=[]),
            mock.patch.object(sessions, "_opencode_targets", return_value=[]),
        ):
            targets = sessions.collect_open_targets()

        key = sessions._open_key("antigravity", _CLI_ID)
        self.assertEqual(targets[key]["id"], _CLI_ID)
        self.assertEqual(targets[key]["keyId"], _CLI_ID)


if __name__ == "__main__":
    unittest.main()
