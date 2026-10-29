"""JetBrains AI, Windsurf, Pi/OMP, Kilo, CodeRabbit and Zed, from synthetic files.

Nothing here needs an account, a key or the network: every collector reads a
file planted under a throwaway HOME, or replays a recorded response.
"""

import json
import os
import sqlite3
import unittest
from unittest import mock

from _support import IsolatedHomeTest
from tidesi import detect
from tidesi.http import HttpResult
from tidesi.normalize import normalize
from tidesi.providers import coderabbit, jetbrains, kilo, openrouter, pi, windsurf, zed

NOW = 1790841600
OPTIONS = ".config/JetBrains/IntelliJIdea2025.3/options/AIAssistantQuotaManager2.xml"


def quota_xml(info, refill=None):
    def attribute(name, value):
        return f'<option name="{name}" value="{json.dumps(value).replace(chr(34), "&quot;")}" />'

    body = attribute("quotaInfo", info) + (attribute("nextRefill", refill) if refill else "")
    return f'<application><component name="AIAssistantQuotaManager2">{body}</component></application>'


def normalized(id_, usage):
    return normalize({"id": id_, "now": NOW, "inputs": {"usage": usage}})


class JetBrainsTest(IsolatedHomeTest):
    def test_reads_monthly_credits_and_the_refill_date(self):
        info = {"type": "Available", "tariffQuota": {"current": 7.5, "maximum": 10, "available": 2.5}, "topUpQuota": {"available": 5, "maximum": 10}}
        self.write(OPTIONS, quota_xml(info, {"type": "Known", "next": "2026-11-01T00:00:00Z"}))
        result = normalized("jetbrains", jetbrains.get_jetbrains_usage())
        self.assertTrue(result["ok"])
        self.assertEqual(result["account"]["name"], "IntelliJ IDEA 2025.3")
        window = result["quotaWindows"][0]
        self.assertEqual((window["pct"], window["resetAt"], window["detail"]), (75, 1793491200, "7.5 / 10 credits"))
        self.assertEqual(result["details"]["topUpAvailable"], 5)

    def test_newest_ide_wins(self):
        old = self.write(
            ".config/JetBrains/PyCharm2024.3/options/AIAssistantQuotaManager2.xml", quota_xml({"tariffQuota": {"current": 1, "maximum": 10}})
        )
        os.utime(old, (1, 1))
        self.write(OPTIONS, quota_xml({"tariffQuota": {"current": 2, "maximum": 10}}))
        self.assertEqual(jetbrains.get_jetbrains_usage()["ide"], "IntelliJ IDEA 2025.3")

    def test_missing_malformed_and_empty_files(self):
        self.assertEqual(jetbrains.get_jetbrains_usage(), {})
        self.assertFalse(normalized("jetbrains", {})["ok"])
        self.write(OPTIONS, "<application><not-closed>")
        self.assertIn("error", jetbrains.get_jetbrains_usage())
        self.write(OPTIONS, quota_xml({"tariffQuota": {"current": "many"}}))
        self.assertIn("error", jetbrains.get_jetbrains_usage())
        self.assertFalse(normalized("jetbrains", jetbrains.get_jetbrains_usage())["ok"])


class WindsurfTest(IsolatedHomeTest):
    def seed(self, plan, as_blob=False):
        path = self.home / ".config/Windsurf/User/globalStorage/state.vscdb"
        path.parent.mkdir(parents=True)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE ItemTable (key TEXT UNIQUE, value BLOB)")
        value = json.dumps(plan)
        conn.execute("INSERT INTO ItemTable VALUES (?, ?)", ("windsurf.settings.cachedPlanInfo", value.encode("utf-16-le") if as_blob else value))
        conn.commit()
        conn.close()

    def test_remaining_percentages_become_used_percentages(self):
        self.seed(
            {
                "planName": "Pro",
                "endTimestamp": 1793433600000,
                "quotaUsage": {
                    "dailyRemainingPercent": 60,
                    "weeklyRemainingPercent": 35,
                    "dailyResetAtUnix": 1790928000,
                    "weeklyResetAtUnix": 1791446400,
                },
            }
        )
        result = normalized("windsurf", windsurf.get_windsurf_usage())
        self.assertTrue(result["ok"])
        self.assertEqual(
            [(w["label"], w["pct"], w["resetAt"]) for w in result["quotaWindows"]],
            [("Daily quota", 40, 1790928000), ("Weekly quota", 65, 1791446400)],
        )
        self.assertEqual(result["summary"]["pct"], 65)
        self.assertEqual(result["details"]["expiresAt"], 1793433600)

    def test_older_caches_fall_back_to_counters(self):
        self.seed({"planName": "Free", "usage": {"messages": 100, "remainingMessages": 70, "flowActions": 200, "usedFlowActions": 10}}, as_blob=True)
        result = normalized("windsurf", windsurf.get_windsurf_usage())
        self.assertEqual([(w["label"], w["pct"]) for w in result["quotaWindows"]], [("Messages", 30), ("Flow actions", 5)])

    def test_missing_database_and_plan_without_figures(self):
        self.assertEqual(windsurf.get_windsurf_usage(), {})
        self.seed({"planName": "Pro"})
        self.assertFalse(normalized("windsurf", windsurf.get_windsurf_usage())["ok"])

    def test_database_is_opened_read_only(self):
        self.seed({"planName": "Pro", "quotaUsage": {"dailyRemainingPercent": 50}})
        real = sqlite3.connect
        with mock.patch.object(sqlite3, "connect", side_effect=lambda target, **kw: real(target, **kw)) as connect:
            windsurf.get_windsurf_usage()
        self.assertIn("mode=ro", connect.call_args.args[0])


def pi_line(**entry):
    return json.dumps(entry)


class PiTest(IsolatedHomeTest):
    def seed(self, *lines, root=".pi/agent/sessions/--work-demo--/2026-10-01T08-00-00_abc.jsonl"):
        return self.write(root, "\n".join(lines))

    def assistant(self, id_, **usage):
        counts = {"input": 100, "output": 20, "cacheRead": 40, "cacheWrite": 10, "totalTokens": 170, "cost": {"total": 9.99}}
        counts.update(usage)
        return pi_line(
            type="message",
            id=id_,
            timestamp="2026-10-01T08:00:00.000Z",
            message={"role": "assistant", "provider": "anthropic", "model": "claude-test", "usage": counts, "timestamp": NOW * 1000},
        )

    def header(self):
        return pi_line(type="session", version=3, id="abc", timestamp="2026-10-01T08:00:00.000Z", cwd="/home/me/work/demo")

    def test_counts_assistant_usage_once_and_never_the_cost(self):
        self.seed(self.header(), self.assistant("a1"), self.assistant("a1"), self.assistant("a2"))
        result = normalized("pi", pi.usage_snapshot())
        stats = result["details"]["stats"]
        self.assertEqual((stats["totalTokens"], stats["totalCachedTokens"], stats["totalSessions"]), (340, 100, 1))
        self.assertEqual(stats["totalCostUSD"], 0)
        self.assertEqual(stats["topWorkspaces"][0]["name"], "demo")
        self.assertNotIn("/home/me", json.dumps(result))

    def test_user_turns_malformed_lines_and_bad_counts_are_ignored(self):
        self.seed(
            self.header(),
            pi_line(type="message", id="u1", message={"role": "user", "usage": {"input": 5000}}),
            "not json",
            "[]",
            self.assistant("a1", input=-5, output=True, cacheRead="many"),
            self.assistant("a2", input=7, output=0, cacheRead=0, cacheWrite=0),
        )
        self.assertEqual(normalized("pi", pi.usage_snapshot())["details"]["stats"]["totalTokens"], 17)

    def test_standalone_usage_entries_and_model_changes_count(self):
        self.seed(
            self.header(),
            pi_line(type="model_change", id="m1", provider="openai", modelId="gpt-test"),
            pi_line(type="usage", id="u1", timestamp="2026-10-01T08:00:00.000Z", usage={"input": 10, "output": 5}),
        )
        models = normalized("pi", pi.usage_snapshot())["details"]["stats"]["models"]
        self.assertEqual(list(models), ["openai/gpt-test"])

    def test_omp_and_explicit_session_directories(self):
        self.seed(self.header(), self.assistant("a1"), root=".omp/agent/sessions/--w--/s.jsonl")
        self.assertEqual(pi.usage_snapshot()["agents"], ["omp"])
        self.write("custom/s.jsonl", self.assistant("a1"))
        with mock.patch.dict(os.environ, {"PI_CODING_AGENT_SESSION_DIR": str(self.home / "custom")}):
            self.assertEqual(len(pi.read_sessions()), 1)

    def test_no_sessions_is_unavailable(self):
        result = normalized("pi", pi.usage_snapshot())
        self.assertFalse(result["ok"])


def trpc(credits=None, sub=None):
    def wrap(payload):
        return {"result": {"data": {"json": payload}}}

    return json.dumps([wrap(credits), wrap(sub)])


class KiloTest(IsolatedHomeTest):
    def collect(self, body, key="kilo-test-key"):
        path = self.write("response.json", body)
        env = {"KILO_USAGE_RESPONSE_FILE": str(path)}
        if key:
            env["KILO_API_KEY"] = key
        with mock.patch.dict(os.environ, env):
            return kilo.get_kilo_usage()

    def test_credits_and_pass(self):
        usage = self.collect(
            trpc(
                {"creditBlocks": [{"amount_mUsd": 6_000_000, "balance_mUsd": 2_000_000}, {"amount_mUsd": 4_000_000, "balance_mUsd": 4_000_000}]},
                {
                    "subscription": {
                        "tier": "tier_49",
                        "currentPeriodUsageUsd": 12.5,
                        "currentPeriodBaseCreditsUsd": 45,
                        "currentPeriodBonusCreditsUsd": 5,
                        "nextBillingAt": "2026-11-01T00:00:00Z",
                    }
                },
            )
        )
        result = normalized("kilo", usage)
        self.assertTrue(result["ok"])
        self.assertEqual([(w["key"], w["pct"]) for w in result["quotaWindows"]], [("kilo_credits", 40), ("kilo_pass", 25)])
        self.assertEqual(result["quotaWindows"][1]["resetAt"], 1793491200)
        self.assertEqual(result["account"]["chips"][0]["text"], "PRO")

    def test_account_without_a_pass_and_an_empty_balance(self):
        result = normalized("kilo", self.collect(trpc({"totalBalance_mUsd": 0}, {"subscription": None})))
        self.assertTrue(result["ok"])
        self.assertEqual(result["quotaWindows"][0]["pct"], 100)

    def test_cli_login_is_used_without_a_key(self):
        self.write(".local/share/kilo/auth.json", {"kilo": {"access": "cli-token"}})
        seen = {}

        def fake(url, headers=None, **kw):
            seen.update(headers)
            return HttpResult(200, trpc({"totalBalance_mUsd": 5_000_000}, None))

        with mock.patch.object(kilo, "fetch_json", side_effect=fake):
            usage = kilo.get_kilo_usage()
        self.assertEqual(seen["Authorization"], "Bearer cli-token")
        self.assertEqual(usage["source"], "cli")

    def test_refused_key_falls_back_to_the_cli_login(self):
        self.write(".local/share/kilo/auth.json", {"kilo": {"access": "cli-token"}})
        responses = [HttpResult(401, ""), HttpResult(200, trpc({"totalBalance_mUsd": 1_000_000}, None))]
        with mock.patch.dict(os.environ, {"KILO_API_KEY": "stale"}), mock.patch.object(kilo, "fetch_json", side_effect=responses):
            self.assertEqual(kilo.get_kilo_usage()["source"], "cli")

    def test_no_credential_http_errors_and_garbage(self):
        self.assertEqual(kilo.get_kilo_usage(), {})
        self.assertFalse(normalized("kilo", {})["ok"])
        self.assertEqual(self.collect("not json").get("error"), "unexpected response")
        self.assertEqual(self.collect("{}").get("error"), "unexpected response")
        with mock.patch.dict(os.environ, {"KILO_API_KEY": "k"}), mock.patch.object(kilo, "fetch_json", return_value=HttpResult(403, "")):
            self.assertEqual(kilo.get_kilo_usage()["error"], "access denied")

    def test_key_never_reaches_output(self):
        usage = self.collect(trpc({"totalBalance_mUsd": 1_000_000}, None), key="kilo-secret-value")
        self.assertNotIn("kilo-secret-value", json.dumps(normalized("kilo", usage)))


REPORT = """CodeRabbit Usage — current billing period

Organization: Example Org
Usage billing: active
User: example
Your reviews: 12
Period resets: 2026-11-01
"""


class CodeRabbitTest(IsolatedHomeTest):
    def test_parses_the_usage_report(self):
        report = coderabbit.parse_report(REPORT)
        self.assertEqual((report["organization"], report["reviews"], report["resetAt"]), ("Example Org", 12, 1793491200))
        result = normalized("coderabbit", report)
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["text"], "12")
        self.assertFalse(result["quotaWindows"][0]["showMeter"])

    def test_other_text_is_not_a_report(self):
        self.assertIsNone(coderabbit.parse_report("Error: not logged in"))
        self.assertIsNone(coderabbit.parse_report(""))

    def test_missing_cli_signed_out_and_timeout(self):
        self.assertEqual(coderabbit.get_coderabbit_usage(), {})
        self.assertFalse(normalized("coderabbit", {})["ok"])
        done = mock.Mock(returncode=1, stdout="")
        with (
            mock.patch.object(coderabbit, "_binary", return_value="/bin/coderabbit"),
            mock.patch.object(coderabbit.subprocess, "run", return_value=done),
        ):
            self.assertIn("not signed in", coderabbit.get_coderabbit_usage()["error"])
        with (
            mock.patch.object(coderabbit, "_binary", return_value="/bin/coderabbit"),
            mock.patch.object(coderabbit.subprocess, "run", side_effect=coderabbit.subprocess.TimeoutExpired("coderabbit", 20)),
        ):
            self.assertEqual(coderabbit.get_coderabbit_usage(), {"error": "timed out"})

    def test_runs_exactly_the_usage_report(self):
        done = mock.Mock(returncode=0, stdout=REPORT)
        with (
            mock.patch.object(coderabbit, "_binary", return_value="/bin/coderabbit"),
            mock.patch.object(coderabbit.subprocess, "run", return_value=done) as run,
        ):
            coderabbit.get_coderabbit_usage()
        self.assertEqual(run.call_args.args[0], ["/bin/coderabbit", "usage"])


class ZedTest(IsolatedHomeTest):
    def collect(self, body):
        path = self.write("response.json", body)
        with mock.patch.dict(os.environ, {"ZED_USAGE_RESPONSE_FILE": str(path)}):
            return zed.get_zed_usage()

    def test_limited_and_unlimited_edit_predictions(self):
        limited = self.collect(
            {
                "plan": {
                    "plan_v3": "zed_pro",
                    "usage": {"edit_predictions": {"used": 120, "limit": 2000}},
                    "subscription_period": {"ended_at": "2026-11-01T00:00:00Z"},
                }
            }
        )
        result = normalized("zed", limited)
        self.assertEqual(
            (result["quotaWindows"][0]["pct"], result["quotaWindows"][0]["resetAt"], result["account"]["chips"][0]["text"]), (6, 1793491200, "PRO")
        )
        self.assertTrue(result["details"]["untested"])
        unlimited = self.collect({"plan": {"plan_v3": "zed_pro", "usage": {"edit_predictions": {"used": 5, "limit": "unlimited"}}}})
        result = normalized("zed", unlimited)
        self.assertTrue(result["ok"])
        self.assertFalse(result["quotaWindows"][0]["showMeter"])

    def test_garbage_and_unsupported_platforms(self):
        self.assertEqual(self.collect({"nope": 1}), {"error": "unexpected response"})
        self.assertFalse(normalized("zed", {"error": "x"})["ok"])
        with mock.patch.object(zed.paths, "IS_MACOS", False):
            self.assertEqual(zed.get_zed_usage(), {"unsupported": True})
        self.assertFalse(normalized("zed", {"unsupported": True})["ok"])

    def test_custom_server_must_be_https(self):
        self.write(".config/zed/settings.json", '{"server_url": "http://zed.internal"}')
        with mock.patch.dict(os.environ, {"ZED_USAGE_RESPONSE_FILE": ""}):
            self.assertEqual(zed.get_zed_usage(), {"error": "custom server must use https"})

    def test_settings_with_comments_are_read(self):
        self.write(".config/zed/settings.json", '// my settings\n{"credentials_url": "https://zed.example.com/"}')
        self.assertEqual(zed._server_url(zed._settings()), "https://zed.example.com")


class DetectionTest(IsolatedHomeTest):
    @unittest.skipIf(os.name == "nt", "POSIX executable bits")
    def test_cli_providers_are_detected_and_ide_providers_stay_manual(self):
        bin_dir = self.home / "path-bin"
        bin_dir.mkdir()
        for name in ("windsurf", "omp", "kilo", "coderabbit", "zed", "idea"):
            (bin_dir / name).write_text("", encoding="utf-8")
            (bin_dir / name).chmod(0o755)
        with mock.patch.dict(os.environ, {"PATH": str(bin_dir)}):
            self.assertEqual(detect.detect_providers(), ["windsurf", "pi", "kilo", "coderabbit"])
        self.assertTrue({"jetbrains", "zed"}.isdisjoint(detect.AUTO_DETECT_PROVIDERS))


class OpenRouterTest(IsolatedHomeTest):
    KEY = {
        "data": {
            "label": "personal",
            "usage": 3.25,
            "limit": 10,
            "limit_remaining": 6.75,
            "usage_daily": 0.5,
            "usage_weekly": 1.5,
            "usage_monthly": 3.25,
        }
    }
    CREDITS = {"data": {"total_credits": 20, "total_usage": 12.5}}

    def collect(self, key, credits=None):
        env = {"OPENROUTER_API_KEY": "or-test", "OPENROUTER_RESPONSE_FILE": str(self.write("key.json", key))}
        if credits is not None:
            env["OPENROUTER_CREDITS_RESPONSE_FILE"] = str(self.write("credits.json", credits))
        with mock.patch.dict(os.environ, env):
            return openrouter.get_openrouter_usage()

    def test_balance_and_key_spend_sit_next_to_the_key_cap(self):
        result = normalized("openrouter", self.collect(self.KEY, self.CREDITS))
        self.assertEqual(
            [(w["key"], w["detail"], w["showMeter"]) for w in result["quotaWindows"]],
            [("openrouter", "$3.25 / $10", True), ("openrouter_balance", "$7.5", False)],
        )
        self.assertEqual(result["details"]["balanceUSD"], 7.5)
        self.assertEqual([row["value"] for row in result["sections"][0]["rows"]], ["$0.5", "$1.5", "$3.25"])

    def test_recorded_key_response_never_reaches_the_network(self):
        result = normalized("openrouter", self.collect(self.KEY))
        self.assertTrue(result["ok"])
        self.assertIsNone(result["details"]["balanceUSD"])

    def test_refused_credits_keep_the_key_figures(self):
        with mock.patch.dict(os.environ, {"OPENROUTER_CREDITS_RESPONSE_FILE": str(self.write("credits.json", "not json"))}):
            self.assertTrue(normalized("openrouter", self.collect(self.KEY, "not json"))["ok"])

    def test_a_rejected_key_still_shows_the_balance(self):
        responses = [HttpResult(401, ""), HttpResult(200, json.dumps(self.CREDITS))]
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test"}), mock.patch.object(openrouter, "fetch_json", side_effect=responses):
            usage = openrouter.get_openrouter_usage()
        result = normalized("openrouter", usage)
        self.assertTrue(result["ok"])
        self.assertEqual((result["summary"]["text"], [w["key"] for w in result["quotaWindows"]]), ("$7.5", ["openrouter_balance"]))

    def test_both_requests_failing_is_still_an_error(self):
        with (
            mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-test"}),
            mock.patch.object(openrouter, "fetch_json", return_value=HttpResult(401, "")),
        ):
            self.assertFalse(normalized("openrouter", openrouter.get_openrouter_usage())["ok"])


if __name__ == "__main__":
    unittest.main()
