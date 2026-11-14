"""Sessions and spend of an extra account: the same collectors, read from the
account's own folder, with every row carrying the account as its provider."""

import json
import os
import time
import unittest
from unittest import mock

from _support import IsolatedHomeTest
from tidesi import accounts, config, envelope, session_index, session_manifest, sessions


def _transcript(root, name, session_id, prompt):
    """One Claude Code session below ``root`` that used some tokens."""
    project = root / "projects" / "-tmp-demo"
    project.mkdir(parents=True, exist_ok=True)
    line = {
        "type": "assistant",
        "message": {"id": f"m-{session_id}", "model": "claude-sonnet-4", "usage": {"input_tokens": 1200, "output_tokens": 300}},
    }
    (project / f"{session_id}.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")
    (root / "history.jsonl").write_text(json.dumps({"sessionId": session_id, "display": prompt}) + "\n", encoding="utf-8")


class SessionAccountsTest(IsolatedHomeTest):
    def setUp(self):
        super().setUp()
        self.main = self.home / ".claude"
        self.work = self.home / ".claude-work"
        _transcript(self.main, "main", "aaaa-main", "main account prompt")
        _transcript(self.work, "work", "bbbb-work", "work account prompt")
        settings = {"accounts": {"claude": [{"id": "work", "label": "Work", "home": "~/.claude-work"}]}}
        (self.home / "config.json").write_text(json.dumps(settings), encoding="utf-8")
        config.reset_settings_cache()
        self.addCleanup(config.reset_settings_cache)

    def test_each_login_lists_only_its_own_sessions(self):
        main = sessions.collect_source("claude")
        work = sessions.collect_source("claude@work")
        self.assertEqual([(r["provider"], r["title"]) for r in main], [("claude", "main account prompt")])
        self.assertEqual([(r["provider"], r["title"]) for r in work], [("claude@work", "work account prompt")])
        self.assertEqual(work[0]["sessionName"], "Claude Code · Work")
        # The account's folder is only in the environment while its rows are read.
        self.assertNotIn("CLAUDE_CONFIG_DIR", os.environ)

    def test_an_account_row_is_priced_like_its_provider(self):
        row = sessions.collect_source("claude@work")[0]
        self.assertEqual(row["tokens"], 1500)
        self.assertEqual(row["costBilling"], "api")  # no plan in this folder's login

    def test_open_keys_differ_between_accounts(self):
        main = sessions.collect_source("claude")[0]["openKey"]
        work = sessions.collect_source("claude@work")[0]["openKey"]
        self.assertEqual(work, sessions._open_key("claude@work", "bbbb-work"))
        self.assertNotEqual(main, work)

    def test_the_manifest_has_a_source_for_the_account(self):
        manifests = {m.source_id: m for m in session_manifest.build_manifests()}
        self.assertIn("claude@work", manifests)
        before = manifests["claude@work"]
        # A write in the work folder changes only the work source.
        time.sleep(0.01)
        (self.work / "projects" / "-tmp-demo" / "cccc-new.jsonl").write_text("{}\n", encoding="utf-8")
        after = {m.source_id: m for m in session_manifest.build_manifests()}
        self.assertNotEqual(after["claude@work"], before)
        self.assertEqual(after["claude"], manifests["claude"])

    def test_the_index_keeps_both_logins_apart_and_filters_by_account(self):
        with mock.patch.object(sessions.pricing, "cached_catalog", return_value={}):
            result = sessions.refresh_sessions()
        self.assertEqual({r["provider"] for r in result["sessions"]}, {"claude", "claude@work"})
        sources = {s["id"]: s["label"] for s in result["sources"]}
        self.assertEqual(sources["claude@work"], "Claude Code · Work")
        self.assertIn("claude", sources)
        only_work = sessions.collect_sessions(source_ids=["claude@work"])
        self.assertEqual([r["title"] for r in only_work["sessions"]], ["work account prompt"])
        self.assertEqual([r["title"] for r in sessions.collect_sessions(query="main")["sessions"]], ["main account prompt"])

    def test_spend_is_grouped_per_account(self):
        rows = [
            {
                "provider": "claude",
                "costUSD": 1.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
                "costBilling": "api",
                "tokens": 10,
                "lastActivityAt": 1_700_000_000,
            },
            {
                "provider": "claude@work",
                "costUSD": 2.0,
                "costStatus": "exact",
                "costProvenance": "estimated",
                "costBilling": "api",
                "tokens": 20,
                "lastActivityAt": 1_700_000_000,
            },
        ]
        group = envelope._local_spend_group(rows, "estimated")
        self.assertEqual(group["providers"]["claude"]["costUSD"], 1.0)
        self.assertEqual(group["providers"]["claude@work"]["costUSD"], 2.0)
        self.assertEqual(group["totalUSD"], 3.0)

    def test_resuming_an_account_session_signs_in_as_that_account(self):
        launched = []
        with (
            mock.patch.object(sessions.shutil, "which", return_value="/usr/bin/claude"),
            mock.patch.object(sessions, "_terminal_launch", lambda argv, cwd: launched.append(argv) or (True, "ok")),
        ):
            ok, _message = sessions.open_session(sessions._open_key("claude@work", "bbbb-work"))
            main_ok, _ = sessions.open_session(sessions._open_key("claude", "aaaa-main"))
        self.assertTrue(ok and main_ok)
        work_argv, main_argv = launched
        self.assertEqual(work_argv[:2], ["env", f"CLAUDE_CONFIG_DIR={self.work}"])
        self.assertEqual(work_argv[2:], ["/usr/bin/claude", "--resume", "bbbb-work"])
        self.assertEqual(main_argv, ["/usr/bin/claude", "--resume", "aaaa-main"])

    def test_providers_without_a_session_collector_are_left_out(self):
        cfg = {"accounts": {"claude": [{"id": "w", "home": "/x"}], "kimi": [{"id": "k", "home": "/y"}]}}
        self.assertEqual([a["id"] for a in accounts.session_accounts(cfg)], ["claude@w"])
        self.assertIn("claude@work", session_index.source_ids())
        self.assertNotIn("kimi@k", session_index.source_ids())


if __name__ == "__main__":
    unittest.main()
