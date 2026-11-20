"""One folder, several logins: history follows the account, not the folder.

Log out of Claude Code and back in as someone else and ``~/.claude`` reads
another quota. These pin that each login records into series of its own, that
a login coming back gets its series back, and that what came before keeps its
place.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import _support  # noqa: F401 — puts the backend package on sys.path
from tidesi import accounts, envelope, identity
from tidesi.normalize import normalize
from tidesi.normalize.claude import normalize_claude
from tidesi.providers import claude_credentials, grok, mistral, openai_credentials

NOW = 1_800_000_000


def row(pct=40, reset=NOW + 3600, ok=True, login=None):
    """A normalized Claude-like row with one session window."""
    provider = {
        "id": "claude",
        "label": "Claude",
        "ok": ok,
        "historyValues": {"s": pct},
        "chartWindows": [{"id": "session", "key": "s"}],
        "quotaWindows": [{"key": "session", "label": "5-hour session", "pct": pct, "available": True, "resetAt": reset, "historyKey": "s"}],
        "sections": [],
    }
    if login:
        identity.mark(provider, login, login + "@example.com")
    return provider


class RegistryCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.file = os.path.join(self.dir, "accounts-seen.json")
        patcher = mock.patch.dict(os.environ, {"TIDESI_ACCOUNTS_FILE": self.file})
        patcher.start()
        self.addCleanup(patcher.stop)

    def resolve(self, provider, tab="claude", legacy="", now=NOW):
        return identity.resolve(provider, "claude", tab, legacy, now)

    def stored(self):
        with open(self.file, encoding="utf-8") as fh:
            return json.load(fh)


class SeriesTest(RegistryCase):
    def test_first_login_keeps_the_folders_series_and_the_next_gets_its_own(self):
        a, _, _ = self.resolve(row(login="a"))
        b, _, _ = self.resolve(row(login="b"), now=NOW + 60)
        self.assertEqual(a, "")  # history recorded before carries on
        self.assertEqual(b, "#" + identity.digest("claude", "b"))
        # Back to the first login: its series, not the newcomer's.
        again, _, _ = self.resolve(row(login="a"), now=NOW + 120)
        self.assertEqual(again, "")

    def test_an_extra_accounts_first_login_keeps_its_at_series(self):
        suffix, _, _ = self.resolve(row(login="w"), tab="claude@work", legacy="@work")
        self.assertEqual(suffix, "@work")

    def test_a_login_keeps_its_series_in_another_folder(self):
        self.resolve(row(login="a"))
        self.resolve(row(login="b"), now=NOW + 60)
        # b moves to the work folder: still b's series, whoever owns @work.
        suffix, _, _ = self.resolve(row(login="b"), tab="claude@work", legacy="@work", now=NOW + 120)
        self.assertEqual(suffix, "#" + identity.digest("claude", "b"))

    def test_a_row_that_cannot_say_who_it_is_records_for_the_last_login(self):
        self.resolve(row(login="a"))
        self.resolve(row(login="b"), now=NOW + 60)
        suffix, public, _ = self.resolve(row(), now=NOW + 120)
        self.assertEqual(suffix, "#" + identity.digest("claude", "b"))
        self.assertIsNone(public)

    def test_no_login_ever_named_changes_nothing_and_writes_nothing(self):
        suffix, public, others = self.resolve(row())
        self.assertEqual((suffix, public, others), ("", None, []))
        self.assertFalse(os.path.exists(self.file))

    def test_the_raw_id_never_reaches_the_row_or_the_file(self):
        provider = row()
        identity.mark(provider, "secret-account-uuid", "me@example.com")
        _, public, _ = self.resolve(provider)
        self.assertNotIn("identity", provider)
        self.assertNotIn("secret-account-uuid", json.dumps(public))
        self.assertNotIn("secret-account-uuid", open(self.file, encoding="utf-8").read())

    def test_a_damaged_registry_is_started_over(self):
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write('{"version":1,"providers":{"claude":{"tabs":[],"logins":{"x":"junk"}}}}')
        suffix, public, _ = self.resolve(row(login="a"))
        self.assertEqual(suffix, "")
        self.assertEqual(public["label"], "a@example.com")
        with open(self.file, "w", encoding="utf-8") as fh:
            fh.write("not json")
        self.resolve(row(login="a"), now=NOW + 60)
        self.assertIn("claude", self.stored()["providers"])

    def test_old_logins_are_forgotten_and_the_list_is_capped(self):
        self.resolve(row(login="owner"))
        for i in range(identity.MAX_LOGINS + 5):
            self.resolve(row(login=f"x{i}"), now=NOW + i)
        logins = self.stored()["providers"]["claude"]["logins"]
        self.assertLessEqual(len(logins), identity.MAX_LOGINS)
        self.assertIn(identity.digest("claude", "owner"), logins)
        later = NOW + identity.FORGET_AFTER_S + 10_000
        self.resolve(row(login="new"), now=later)
        self.assertEqual(list(self.stored()["providers"]["claude"]["logins"]), [identity.digest("claude", "new")])
        # The tab's first login, forgotten and back: still the tab's own series.
        suffix, _, _ = self.resolve(row(login="owner"), now=later + 60)
        self.assertEqual(suffix, "")


class OtherLoginsTest(RegistryCase):
    def test_the_other_logins_of_a_folder_are_listed_with_their_last_reading(self):
        self.resolve(row(pct=80, reset=NOW + 3600, login="a"))
        _, public, others = self.resolve(row(pct=2, login="b"), now=NOW + 60)
        self.assertEqual(public["label"], "b@example.com")
        self.assertEqual([o["label"] for o in others], ["a@example.com"])
        section = identity.others_section(others, now=NOW + 120)
        group = section["groups"][0]
        self.assertEqual((group["label"], group["pct"], group["resetAt"]), ("a@example.com", 80, NOW + 3600))
        # Once its window has reset, its quota is back.
        after = identity.others_section(others, now=NOW + 7200)["groups"][0]
        self.assertEqual((after["pct"], after["resetAt"], after["rows"][0]["pct"]), (0, 0, 0))

    def test_one_login_in_two_folders_is_marked_and_not_listed_as_another(self):
        self.resolve(row(login="a"))
        _, public, others = self.resolve(row(login="a"), tab="claude@work", legacy="@work", now=NOW + 5)
        self.assertEqual(public["sameAs"], "claude")
        self.assertEqual(others, [])


class EnvelopeTest(RegistryCase):
    def build(self, rows):
        """Build ["claude"] once per row, as successive polls."""
        out = []
        for i, provider in enumerate(rows):
            with (
                mock.patch.object(envelope, "collect", lambda id_, now, **_: {}),
                mock.patch.object(envelope, "normalize", lambda raw, p=provider: p),
                mock.patch.object(envelope.config, "load_settings", return_value={}),
                mock.patch.object(envelope, "_local_spend", return_value={}),
            ):
                out.append(envelope.build(["claude"], now=NOW + i * 60)["providers"][0])
        return out

    def test_switching_logins_in_one_folder_records_two_series(self):
        a, b, a2 = self.build([row(pct=80, login="a"), row(pct=2, login="b"), row(pct=81, login="a")])
        key_b = "s#" + identity.digest("claude", "b")
        self.assertEqual(a["historyValues"], {"s": 80})
        self.assertEqual(b["historyValues"], {key_b: 2})
        self.assertEqual(b["quotaWindows"][0]["historyKey"], key_b)
        self.assertEqual(b["chartWindows"][0]["key"], key_b)
        self.assertEqual(a2["historyValues"], {"s": 81})
        self.assertEqual(b["login"]["label"], "b@example.com")
        # b's tab lists a, with the 80 % it was last seen at.
        bars = [s for s in b["sections"] if s.get("kind") == "bars"]
        self.assertEqual(bars[0]["groups"][0]["pct"], 80)


class ClaudeIdentityTest(unittest.TestCase):
    def test_the_account_in_claude_json_names_the_login(self):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, ".claude.json"), "w", encoding="utf-8") as fh:
            json.dump({"oauthAccount": {"accountUuid": "u1", "emailAddress": "me@x.io", "organizationUuid": "o1", "x": 1}, "projects": {}}, fh)
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": home}):
            self.assertEqual(claude_credentials.get_claude_account(), {"accountUuid": "u1", "emailAddress": "me@x.io", "organizationUuid": "o1"})
        with mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual(claude_credentials.get_claude_account()["accountUuid"], "u1")

    def test_the_normalizer_marks_the_account_and_its_organisation(self):
        raw = {
            "id": "claude",
            "now": NOW,
            "inputs": {
                "credentials": {"claudeAiOauth": {"accessToken": "t"}},
                "oauthAccount": {"accountUuid": "u1", "organizationUuid": "o1", "emailAddress": "me@x.io"},
                "usage": None,
                "usageError": "offline",
            },
        }
        result = normalize_claude(raw)
        self.assertEqual(result["identity"], {"id": "u1:o1", "label": "me@x.io"})
        self.assertEqual(result["details"]["organizationUuid"], "o1")


class OtherProvidersTest(unittest.TestCase):
    def test_codex_is_its_chatgpt_account_and_not_a_token(self):
        raw = {
            "id": "openai",
            "now": NOW,
            "inputs": {"credentials": {"codexAccessToken": "tok", "codexLoggedIn": True, "accountId": "acc-1", "email": "me@x.io"}},
        }
        self.assertEqual(normalize(raw)["identity"], {"id": "acc-1", "label": "me@x.io"})

    def test_a_provider_with_only_a_key_names_no_login(self):
        raw = {"id": "openai", "now": NOW, "inputs": {"credentials": {"openaiApiKey": "sk-x"}}}
        self.assertNotIn("identity", normalize(raw))


class ExtraAccountKeysTest(unittest.TestCase):
    """An extra account reads only its folder: the environment's keys are the
    main login's, and read twice they put one bill in the total twice."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        env = {
            "WIDGET_CLAUDE_ADMIN_KEY": "sk-ant-admin-main",
            "OPENAI_API_KEY": "sk-main",
            "XAI_API_KEY": "xai-main",
            "MISTRAL_API_KEY": "mistral-main",
        }
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_main_login_reads_the_environment(self):
        self.assertEqual(claude_credentials.get_claude_credentials().get("claudeAdminApiKey"), "sk-ant-admin-main")
        self.assertEqual(openai_credentials.get_openai_credentials().get("openaiApiKey"), "sk-main")
        self.assertEqual(grok._resolve_api_key(), "xai-main")

    def test_an_extra_account_does_not(self):
        with accounts.home_for("claude", self.home):
            self.assertNotIn("claudeAdminApiKey", claude_credentials.get_claude_credentials())
            with open(os.path.join(self.home, "admin-api-key"), "w", encoding="utf-8") as fh:
                fh.write("sk-ant-admin-work")
            self.assertEqual(claude_credentials.get_claude_credentials()["claudeAdminApiKey"], "sk-ant-admin-work")
        with accounts.home_for("openai", self.home):
            self.assertEqual(openai_credentials.get_openai_credentials(), {})
        with accounts.home_for("grok", self.home):
            self.assertEqual(grok._resolve_api_key(), "")
        # Outside the fetch, the main login again.
        self.assertIsNone(accounts.extra_account())


class MistralFolderTest(unittest.TestCase):
    def test_vibe_home_is_the_vibe_folder_itself(self):
        with mock.patch.dict(os.environ, {"VIBE_HOME": "/x/.vibe-work"}):
            self.assertEqual(mistral._vibe_session_dir(), os.path.join("/x/.vibe-work", "logs", "session"))
        with mock.patch.dict(os.environ, {"HOME": "/h"}):
            os.environ.pop("VIBE_HOME", None)
            self.assertEqual(mistral._vibe_session_dir(), os.path.join("/h", ".vibe", "logs", "session"))

    def test_the_key_comes_from_the_accounts_own_env_file(self):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, ".env"), "w", encoding="utf-8") as fh:
            fh.write("MISTRAL_API_KEY=work-key\n")
        with mock.patch.dict(os.environ, {"VIBE_HOME": home}):
            self.assertEqual(mistral._vibe_key(), "work-key")


class FoldersTest(unittest.TestCase):
    def test_a_folder_already_read_is_not_read_again(self):
        cfg = {
            "accounts": {
                "claude": [
                    {"id": "same", "home": "~/.claude"},  # the main login's
                    {"id": "work", "home": "~/.claude-work"},
                    {"id": "again", "home": "~/.claude-work/"},  # work's, spelt otherwise
                    {"id": "home2", "home": "~/.claude-home"},
                ]
            }
        }
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
            self.assertEqual([a["key"] for a in accounts.configured(cfg)["claude"]], ["work", "home2"])

    def test_kiro_and_antigravity_cannot_have_extra_accounts(self):
        # Their quota is read from the IDE, never from a login folder.
        cfg = {"accounts": {"kiro": [{"id": "w", "home": "/k"}], "antigravity": [{"id": "w", "home": "/g"}]}}
        self.assertEqual(accounts.configured(cfg), {})


class LockTest(unittest.TestCase):
    def test_providers_on_different_variables_do_not_wait_for_each_other(self):
        inside = threading.Event()
        release = threading.Event()

        def hold_claude():
            with accounts.home_for("claude", "/work"):
                inside.set()
                release.wait(5)

        holder = threading.Thread(target=hold_claude)
        holder.start()
        inside.wait(5)
        started = time.monotonic()
        with accounts.home_for("openai", "/codex-work"):
            pass
        # And a main login with no extra account takes no lock at all.
        with accounts.home_for("claude", None, exclusive=False):
            pass
        self.assertLess(time.monotonic() - started, 1)
        release.set()
        holder.join()


if __name__ == "__main__":
    unittest.main()
