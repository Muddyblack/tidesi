import hashlib
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _support import REPO

sys.path.insert(0, str(Path(REPO) / "backend"))
session_manifest = importlib.import_module("tidesi.session_manifest")


class SessionManifestTraversalTest(unittest.TestCase):
    def _tree(self, root: Path) -> None:
        (root / "z" / "nested").mkdir(parents=True)
        (root / "a").mkdir()
        for relative in ("root.jsonl", "z/one.jsonl", "z/nested/two.jsonl", "a/skip.txt"):
            (root / relative).write_text("metadata fixture", encoding="utf-8")

    def _expected_record(self, path: Path, kind: str) -> tuple[str, str, int, int, int]:
        normalized = os.path.abspath(os.path.normpath(path))
        stat = path.stat()
        return kind, normalized, 1, stat.st_mtime_ns, stat.st_size

    def _fingerprint(self, records: list[tuple[str, str, int, int, int]]) -> tuple[int, int]:
        encoded = json.dumps(records, separators=(",", ":"), sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        return int(digest[:16], 16) & ((1 << 63) - 1), len(encoded)

    def test_matching_files_preserve_empty_small_nested_excluded_records(self):
        # Given empty, shallow and nested trees with a pruned exclusion
        # When matching JSONL metadata is collected
        # Then legacy directory-first ordering, predicates and fingerprints remain exact
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            empty = base / "empty"
            empty.mkdir()
            self.assertEqual(
                session_manifest._matching_files(str(empty), lambda name: name.endswith(".jsonl")), [self._expected_record(empty, "directory")]
            )

            root = base / "store"
            self._tree(root)
            excluded = root / ".msp-view-v1"
            excluded.mkdir()
            (excluded / "hidden.jsonl").write_text("excluded", encoding="utf-8")
            records = session_manifest._matching_files(str(root), lambda name: name.endswith(".jsonl"), excluded={".msp-view-v1"})
            expected = [self._expected_record(path, "directory") for path in (root, root / "a", root / "z", root / "z" / "nested")]
            expected.extend(
                self._expected_record(path, "file") for path in (root / "root.jsonl", root / "z" / "one.jsonl", root / "z" / "nested" / "two.jsonl")
            )
            self.assertEqual(records, expected)
            manifest = session_manifest._fingerprint("fixture", records)
            self.assertEqual((manifest.mtime_ns, manifest.size), self._fingerprint(expected))

    def test_antigravity_preserves_tree_specific_predicates_and_record_order(self):
        # Given every Antigravity layout and files that do/do not match
        # When the metadata trees are collected
        # Then each group retains its directory-first records and fingerprints
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cli = root / "antigravity-cli" / "brain"
            ide = root / "antigravity-ide" / "brain"
            markdown = root / "antigravity" / "brain"
            (cli / "session" / ".system_generated" / "logs").mkdir(parents=True)
            (ide / "session" / ".system_generated" / "logs").mkdir(parents=True)
            (ide / "session" / ".system_generated" / "logs" / "transcript.jsonl").touch()
            (markdown / "workspace").mkdir(parents=True)
            (cli / "session" / ".system_generated" / "logs" / "transcript.jsonl").touch()
            (cli / "session" / "other.txt").touch()
            (markdown / "workspace" / "note.md").touch()
            (markdown / "workspace" / "ignored.jsonl").touch()
            records = session_manifest._antigravity_records(str(root))
            dirs = [cli, cli / "session", cli / "session" / ".system_generated", cli / "session" / ".system_generated" / "logs"]
            dirs += [ide, ide / "session", ide / "session" / ".system_generated", ide / "session" / ".system_generated" / "logs"]
            dirs += [markdown, markdown / "workspace"]
            files = [
                cli / "session" / ".system_generated" / "logs" / "transcript.jsonl",
                ide / "session" / ".system_generated" / "logs" / "transcript.jsonl",
                markdown / "workspace" / "note.md",
            ]
            expected = [self._expected_record(path, "directory") for path in dirs[:4]]
            expected.append(self._expected_record(files[0], "file"))
            expected.extend(self._expected_record(path, "directory") for path in dirs[4:8])
            expected.append(self._expected_record(files[1], "file"))
            expected.extend(self._expected_record(path, "directory") for path in dirs[8:])
            expected.append(self._expected_record(files[2], "file"))
            self.assertEqual(records, expected)
            manifest = session_manifest._fingerprint("antigravity", records)
            self.assertEqual((manifest.mtime_ns, manifest.size), self._fingerprint(expected))

    def test_matching_files_walks_each_root_once(self):
        # Given a multi-directory matching-file tree
        # When metadata is collected
        # Then the filesystem traversal count is one and records are returned
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tree(root)
            walk = os.walk
            with mock.patch.object(session_manifest.os, "walk", wraps=walk) as counted_walk:
                actual = session_manifest._matching_files(str(root), lambda name: name.endswith(".jsonl"))
            self.assertEqual(counted_walk.call_count, 1)
            self.assertTrue(actual)

    def test_antigravity_walks_each_tree_once(self):
        # Given every Antigravity directory tree
        # When metadata is collected
        # Then each filesystem root is traversed exactly once
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for client in ("antigravity-cli", "antigravity-ide", "antigravity"):
                (root / client / "brain").mkdir(parents=True)
            walk = os.walk
            with mock.patch.object(session_manifest.os, "walk", wraps=walk) as counted_walk:
                session_manifest._antigravity_records(str(root))
            self.assertEqual(counted_walk.call_count, 3)

    def test_matching_files_keeps_symlink_and_stat_error_metadata(self):
        # Given a symlinked directory and a matching file whose stat fails
        # When metadata is collected
        # Then symlink traversal policy and zeroed stat-error tuple remain unchanged
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "real").mkdir()
            target = root / "real" / "linked.jsonl"
            target.touch()
            link = root / "linked-dir"
            try:
                link.symlink_to(root / "real", target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("directory symlinks are unavailable")
            denied = root / "denied.jsonl"
            denied.touch()
            original_stat = os.stat

            def stat_with_denial(path, *args, **kwargs):
                if os.fspath(path) == str(denied):
                    raise PermissionError("fixture stat denial")
                return original_stat(path, *args, **kwargs)

            with mock.patch.object(session_manifest.os, "stat", side_effect=stat_with_denial):
                records = session_manifest._matching_files(str(root), lambda name: name.endswith(".jsonl"))
            self.assertIn(self._expected_record(link, "directory"), records)
            self.assertEqual(records.count(self._expected_record(target, "file")), 1)
            self.assertIn(("file", str(denied), 0, 0, 0), records)


if __name__ == "__main__":
    unittest.main()
