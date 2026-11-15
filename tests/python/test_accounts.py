"""Extra logins of one provider: ``claude@work`` is the Claude provider read
from another config directory."""

import os
import threading
import unittest
from unittest import mock

import _support  # noqa: F401 — puts the backend package on sys.path
from tidesi import accounts, envelope, widget_state

CFG = {
    "providers": {"claude": True, "openai": True},
    "providerDefaultsApplied": True,
    "accounts": {
        "claude": [{"id": "Work", "label": "Work", "home": "~/.claude-work"}],
        "openai": [{"id": "team", "home": "/srv/codex-team"}],
    },
}


class ConfiguredTest(unittest.TestCase):
    def test_entries_become_prefixed_provider_ids(self):
        found = accounts.configured(CFG)
        self.assertEqual(found["claude"][0]["id"], "claude@work")
        self.assertEqual(found["openai"][0]["label"], "team")  # no label: the key

    def test_unusable_entries_are_dropped_not_fatal(self):
        cfg = {
            "accounts": {
                "claude": [
                    {"id": "ok", "home": "/a"},
                    {"id": "ok", "home": "/b"},  # repeated key
                    {"id": "Bad Key!", "home": "/c"},
                    {"id": "nohome"},
                    "not a dict",
                ],
                "zed": [{"id": "x", "home": "/z"}],  # no home variable
                "openai": "nope",
            }
        }
        self.assertEqual([a["id"] for a in accounts.configured(cfg)["claude"]], ["claude@ok"])
        self.assertNotIn("zed", accounts.configured(cfg))
        self.assertNotIn("openai", accounts.configured(cfg))
        self.assertEqual(accounts.configured({"accounts": []}), {})

    def test_accounts_follow_their_provider_and_its_toggle(self):
        self.assertEqual(envelope.enabled(CFG), ["claude", "claude@work", "openai", "openai@team"])
        off = {**CFG, "providers": {"claude": False, "openai": True}}
        self.assertEqual(envelope.enabled(off), ["openai", "openai@team"])


class HomeOverrideTest(unittest.TestCase):
    def test_home_is_set_for_the_fetch_and_restored(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/main"}):
            with accounts.home_for("claude", "/work"):
                self.assertEqual(os.environ["CLAUDE_CONFIG_DIR"], "/work")
            self.assertEqual(os.environ["CLAUDE_CONFIG_DIR"], "/main")

    def test_a_tilde_is_expanded_the_way_a_shell_would(self):
        with accounts.home_for("claude", "~/.claude-work"):
            self.assertEqual(os.environ["CLAUDE_CONFIG_DIR"], os.path.expanduser("~/.claude-work"))
        self.assertEqual(accounts.launch_env({"base": "claude", "home": "~/x"}), {"CLAUDE_CONFIG_DIR": os.path.expanduser("~/x")})

    def test_unset_home_stays_unset_afterwards(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CODEX_HOME", None)
            with accounts.home_for("openai", "/x"):
                pass
            self.assertNotIn("CODEX_HOME", os.environ)

    def test_parallel_fetches_never_see_each_others_home(self):
        seen = []

        def fetch(home):
            with accounts.home_for("claude", home):
                seen.append((home, os.environ["CLAUDE_CONFIG_DIR"]))

        threads = [threading.Thread(target=fetch, args=(h,)) for h in ("/a", "/b", "/c", "/d")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(home == seen_home for home, seen_home in seen))


class BuildTest(unittest.TestCase):
    def build(self, selected):
        homes = []

        def collect(id_, now, **_):
            homes.append(os.environ.get("CLAUDE_CONFIG_DIR"))
            return {"id": id_, "now": now, "inputs": {}}

        def normalize(raw):
            return {"id": raw["id"], "label": "Claude", "ok": True, "icon": "claude-color.svg"}

        with (
            mock.patch.object(envelope, "collect", collect),
            mock.patch.object(envelope, "normalize", normalize),
            mock.patch.object(envelope.config, "load_settings", return_value=CFG),
            mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/main"}),
        ):
            return envelope.build(selected, now=1_700_000_000), homes

    def test_extra_account_is_the_base_provider_read_from_its_home(self):
        env, homes = self.build(["claude", "claude@work"])
        main, work = env["providers"]
        self.assertEqual((main["id"], main["label"]), ("claude", "Claude"))
        self.assertEqual((work["id"], work["label"]), ("claude@work", "Claude · Work"))
        self.assertEqual(work["profile"]["base"], "claude")
        self.assertEqual(work["icon"], "claude-color.svg")
        # One fetch per row, each reading the home that row names.
        self.assertCountEqual(homes, ["/main", os.path.expanduser("~/.claude-work")])

    def test_an_account_records_its_own_history_series(self):
        # One series fed by two logins is a chart and an ETA of neither.
        account = accounts.find(CFG, "claude@work")
        base = {
            "id": "claude",
            "label": "Claude",
            "historyValues": {"s": 23, "w": 61},
            "chartWindows": [{"id": "session", "key": "s"}, {"id": "weekly", "key": "w"}],
            "quotaWindows": [{"key": "session", "historyKey": "s"}, {"key": "extra"}],
        }
        work = accounts.relabel(base, account)
        self.assertEqual(work["historyValues"], {"s@work": 23, "w@work": 61})
        self.assertEqual([w["key"] for w in work["chartWindows"]], ["s@work", "w@work"])
        self.assertEqual(work["quotaWindows"], [{"key": "session", "historyKey": "s@work"}, {"key": "extra"}])
        # The base provider's own result is left as it was.
        self.assertEqual(base["historyValues"], {"s": 23, "w": 61})
        self.assertEqual(base["chartWindows"][0]["key"], "s")

    def test_crash_in_an_account_is_its_own_error_row(self):
        row = envelope.crashed("claude@work", 1, ValueError("boom"))
        self.assertEqual(row["id"], "claude@work")
        self.assertIn("Claude", row["label"])
        self.assertIs(row["ok"], False)


class SnapshotTest(unittest.TestCase):
    def test_account_rows_survive_the_snapshot_filter(self):
        kept = widget_state._current_providers([{"id": "claude@work"}, {"id": "gone"}, {"id": "claude"}])
        self.assertEqual([p["id"] for p in kept], ["claude@work", "claude"])


if __name__ == "__main__":
    unittest.main()
