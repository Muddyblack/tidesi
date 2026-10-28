"""Days after stats-cache.json's lastComputedDate come from Claude's transcripts."""

import datetime
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _support  # noqa: F401  (sys.path)
from tidesi.providers import claude_transcripts


def _line(**fields):
    return json.dumps(fields, separators=(",", ":")) + "\n"


class ClaudeTranscriptsTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.cache = tempfile.mkdtemp()
        patcher = mock.patch.object(claude_transcripts.paths, "cache_home", return_value=self.cache)
        patcher.start()
        self.addCleanup(patcher.stop)
        data = mock.patch.object(claude_transcripts.paths, "data_home", return_value=self.cache)
        data.start()
        self.addCleanup(data.stop)
        self.addCleanup(shutil.rmtree, self.home, True)
        self.addCleanup(shutil.rmtree, self.cache, True)
        project = os.path.join(self.home, "projects", "p")
        os.makedirs(project)
        with open(os.path.join(project, "s.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(_line(type="user", timestamp="2026-10-12T12:00:00.000Z", message={"role": "user", "content": "hi"}))
            usage = {"input_tokens": 2, "output_tokens": 10, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 8}
            for _ in range(2):  # a streamed message repeats; it counts once
                fh.write(
                    _line(
                        type="assistant",
                        timestamp="2026-10-12T12:00:05.000Z",
                        message={"id": "m1", "model": "claude-sonnet-5-5", "usage": usage, "content": [{"type": "tool_use"}]},
                    )
                )

    def test_days_after_the_cache_are_added(self):
        stats = {"lastComputedDate": "2026-09-20", "totalSessions": 5, "totalMessages": 50, "dailyActivity": [{"date": "2026-09-20"}]}
        out = claude_transcripts.supplement(stats, home=self.home)
        day = out["dailyActivity"][-1]
        self.assertEqual((day["messageCount"], day["sessionCount"], day["toolCallCount"]), (2, 1, 1))
        self.assertEqual(out["dailyModelTokens"][-1]["tokensByModel"], {"claude-sonnet-5-5": 120})
        self.assertEqual(out["totalSessions"], 6)
        self.assertEqual(out["modelUsage"]["claude-sonnet-5-5"]["outputTokens"], 10)

    def test_days_already_in_the_cache_are_not_counted_twice(self):
        # Days are bucketed locally, so the transcript's day depends on the host timezone.
        stamp = datetime.datetime(2026, 10, 12, 12, 0, tzinfo=datetime.timezone.utc).timestamp()
        local_day = datetime.datetime.fromtimestamp(stamp).strftime("%Y-%m-%d")
        stats = {"lastComputedDate": local_day, "dailyActivity": []}
        self.assertEqual(claude_transcripts.supplement(stats, home=self.home)["dailyActivity"], [])

    def test_no_transcripts_leaves_stats_alone(self):
        stats = {"lastComputedDate": "2026-09-20"}
        self.assertEqual(claude_transcripts.supplement(stats, home=os.path.join(self.home, "none")), stats)

    def test_load_keeps_finished_days_in_the_widgets_own_file(self):
        legacy = {"lastComputedDate": "2026-09-20", "totalSessions": 5}
        now = 1792000000 + 3 * 86400  # a few days after the transcript's day
        out = claude_transcripts.load(legacy, now, home=self.home)
        self.assertEqual(out["dailyActivity"][-1]["messageCount"], 2)
        with open(claude_transcripts.archive_path(), encoding="utf-8") as fh:
            archived = json.load(fh)
        self.assertEqual(archived["totalSessions"], 6)
        # Claude's file may now be gone, and the transcript pruned: the days stay.
        shutil.rmtree(os.path.join(self.home, "projects"))
        again = claude_transcripts.load(None, now, home=self.home)
        self.assertEqual(again["totalSessions"], 6)
        self.assertEqual(again["dailyActivity"][-1]["messageCount"], 2)


if __name__ == "__main__":
    unittest.main()
