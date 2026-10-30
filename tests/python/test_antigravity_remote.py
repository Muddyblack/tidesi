import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from tidesi.http import HttpResult
from tidesi.normalize.antigravity import normalize_antigravity
from tidesi.providers import antigravity, antigravity_remote

ENABLED = "[RemoteControl] RemoteControlEnabled value: true\n"
CONNECTED = "[remote-control-x-v2] Connection status: Connected\n"
CLOSED = "[remote-control-x-v2] Connection loop exited: WebChannel connection closed\n"


class RemoteControlTest(unittest.TestCase):
    def test_windows_remote_credentials_use_agy_generic_credential(self):
        stored = {"token": {"access_token": "test-access"}}
        with (
            mock.patch.object(antigravity_remote.paths, "IS_WINDOWS", True),
            mock.patch.object(antigravity_remote.windows_credentials, "password", return_value=json.dumps(stored)) as read,
        ):
            self.assertEqual(antigravity_remote._stored_credentials(), stored)
        read.assert_called_once_with("gemini:antigravity")

    def read(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "language_server.log")
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            return antigravity.remote_control([path])

    def test_connected(self):
        self.assertEqual(self.read("noise\n" + ENABLED + CONNECTED), {"enabled": True, "connected": True})

    def test_closed_connection_is_offline(self):
        self.assertEqual(self.read(ENABLED + CONNECTED + CLOSED), {"enabled": True, "connected": False})

    def test_disabled_is_never_connected(self):
        off = "RemoteControlEnabled value: false\n"
        self.assertEqual(self.read(ENABLED + CONNECTED + off), {"enabled": False, "connected": False})

    def test_no_events_or_no_log_is_unknown(self):
        self.assertIsNone(self.read("nothing relevant\n"))
        self.assertIsNone(antigravity.remote_control([os.path.join(tempfile.gettempdir(), "no-such-agy.log")]))

    def test_reported_in_details_for_the_tab(self):
        usage = {"models": [], "planType": "Starter", "remote": {"enabled": True, "connected": True}}
        result = normalize_antigravity({"now": 1, "inputs": {"usage": usage}})
        remote = result["details"]["remote"]
        self.assertEqual((remote["state"], remote["url"]), ("online", "https://antigravity.google.com/"))
        self.assertEqual([c["text"] for c in result["account"]["chips"]], ["STARTER"])

    def test_no_log_means_no_remote_tab(self):
        result = normalize_antigravity({"now": 1, "inputs": {"usage": {"models": [], "planType": "Starter"}}})
        self.assertIsNone(result["details"]["remote"])


class RemoteDevicesTest(unittest.TestCase):
    def setUp(self):
        self.stored = {"auth_method": "consumer", "token": {"access_token": "test-access", "refresh_token": "test-refresh"}}
        patcher = mock.patch.object(antigravity_remote, "_stored_credentials", return_value=self.stored)
        self.credentials = patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(antigravity_remote, "fetch_json")
        self.http = patcher.start()
        self.addCleanup(patcher.stop)
        # Never spawn the real agy or touch the real cache from a test.
        patcher = mock.patch.object(antigravity_remote, "_cooled_down", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def respond(self, data, status=200):
        self.http.return_value = HttpResult(status, json.dumps(data))

    def test_list_devices_get_request_and_redaction(self):
        self.respond(
            {
                "instances": [
                    {"uuid": "private-id", "metadata": {"name": "cloudshell", "secret": "not-for-output"}, "status": "INSTANCE_STATUS_DISCONNECTED"},
                    {"metadata": {"name": "desktop"}, "status": "INSTANCE_STATUS_CONNECTED", "statusLastUpdatedAt": "2026-10-10T12:00:00Z"},
                ]
            }
        )
        rows = antigravity_remote.get_instances()
        self.assertEqual([r["name"] for r in rows], ["desktop", "cloudshell"])
        self.assertEqual([r["state"] for r in rows], ["online", "offline"])
        self.assertEqual(rows[0]["updatedAt"], "2026-10-10T12:00:00Z")
        self.assertEqual(rows[1]["url"], "https://antigravity.google.com/r/private-id")
        self.assertNotIn("uuid", rows[1])
        self.assertNotIn("not-for-output", json.dumps(rows))
        args, kwargs = self.http.call_args
        self.assertEqual(args, (antigravity_remote.INSTANCE_URL,))
        self.assertNotIn("data", kwargs)  # ListInstances' HTTP binding is GET.
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-access")

    def test_project_query_is_encoded(self):
        self.stored["project_id"] = "projects/test project"
        self.respond({})
        self.assertEqual(antigravity_remote.get_instances(), [])
        self.assertTrue(self.http.call_args.args[0].endswith("?project=projects%2Ftest+project"))

    def test_device_links_escape_the_id_and_fall_back_when_missing(self):
        self.respond(
            {
                "instances": [
                    {"uuid": "device/id?name=a#b", "metadata": {"name": "desktop"}, "status": 1},
                    {"metadata": {"name": "no-id"}, "status": 2},
                ]
            }
        )
        rows = antigravity_remote.get_instances()
        self.assertEqual(rows[0]["url"], "https://antigravity.google.com/r/device%2Fid%3Fname%3Da%23b")
        self.assertEqual(rows[1]["url"], "https://antigravity.google.com/")

    def agy_refreshes(self):
        fresh = {"auth_method": "consumer", "token": {"access_token": "new-access"}}
        self.credentials.side_effect = [self.stored, fresh]
        which = mock.patch.object(antigravity_remote.shutil, "which", return_value="/bin/agy")
        run = mock.patch.object(antigravity_remote.subprocess, "run")
        which.start(), self.addCleanup(which.stop)
        self.run_agy = run.start()
        self.addCleanup(run.stop)
        cool = mock.patch.object(antigravity_remote, "_cooled_down", return_value=True)
        cool.start()
        self.addCleanup(cool.stop)

    def test_expired_access_is_refreshed_by_agy(self):
        self.stored["token"]["expiry"] = "2020-01-01T00:00:00Z"
        self.agy_refreshes()
        self.http.return_value = HttpResult(200, "{}")
        self.assertEqual(antigravity_remote.get_instances(), [])
        self.assertEqual(self.run_agy.call_args.args[0], ["/bin/agy", "models"])
        self.assertEqual(self.http.call_args.kwargs["headers"]["Authorization"], "Bearer new-access")

    def test_unauthorized_retries_only_once(self):
        self.agy_refreshes()
        self.http.side_effect = [HttpResult(401, ""), HttpResult(401, "")]
        self.assertIsNone(antigravity_remote.get_instances())
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(self.run_agy.call_count, 1)

    def test_network_forbidden_and_malformed_responses_are_unavailable(self):
        for status, body in [(0, ""), (403, ""), (429, ""), (200, "<html>login</html>"), (200, "[]"), (200, '{"instances":null}')]:
            with self.subTest(status=status, body=body):
                self.http.reset_mock()
                self.http.return_value = HttpResult(status, body)
                self.assertIsNone(antigravity_remote.get_instances())
                self.assertEqual(self.http.call_count, 1)

    def test_missing_credentials_and_account_mismatch_do_not_request(self):
        self.credentials.return_value = None
        self.assertIsNone(antigravity_remote.get_instances())
        self.http.assert_not_called()
        self.credentials.return_value = self.stored
        payload = base64.urlsafe_b64encode(b'{"email":"other@example.com"}').decode().rstrip("=")
        self.stored["id_token"] = "header." + payload + ".signature"
        # The list is fetched alongside the usage lookup, so a mismatch is only
        # caught afterwards; the rows are still discarded.
        self.respond({"instances": [{"metadata": {"name": "a"}}]})
        self.assertIsNone(antigravity_remote.get_instances("active@example.com"))

    def test_bad_tokens_do_not_reach_headers(self):
        self.stored["token"] = {"access_token": "secret\r\ninjected"}
        self.assertIsNone(antigravity_remote.get_instances())
        self.http.assert_not_called()

    def test_malformed_rows_and_unknown_states(self):
        self.respond(
            {
                "instances": [
                    None,
                    {},
                    {"metadata": []},
                    {"metadata": {"name": ""}},
                    {"metadata": {"name": "idle"}, "status": 3},
                    {"metadata": {"name": "unknown"}, "status": {}},
                ]
            }
        )
        rows = antigravity_remote.get_instances()
        self.assertEqual([r["state"] for r in rows], ["idle", "unknown"])

    def test_provider_attaches_devices_without_a_local_log(self):
        self.respond({"instances": [{"uuid": "desktop-id", "metadata": {"name": "other desktop"}, "status": 1}]})
        with (
            mock.patch.object(antigravity, "_get_antigravity_usage", return_value={"models": []}),
            mock.patch.object(antigravity, "remote_control", return_value=None),
        ):
            usage = antigravity.get_antigravity_usage()
        normalized = normalize_antigravity({"now": 1, "inputs": {"usage": usage}})
        remote = normalized["details"]["remote"]
        self.assertEqual(remote["instances"][0]["name"], "other desktop")
        self.assertEqual(remote["title"], "Remote devices")
        self.assertEqual(remote["state"], "online")
        self.assertEqual(remote["instances"][0]["url"], "https://antigravity.google.com/r/desktop-id")

    def test_failure_preserves_local_status_and_quota(self):
        self.respond({}, 503)
        with (
            mock.patch.object(antigravity, "_get_antigravity_usage", return_value={"models": [], "planType": "Starter"}),
            mock.patch.object(antigravity, "remote_control", return_value={"enabled": True, "connected": True}),
        ):
            usage = antigravity.get_antigravity_usage()
        remote = normalize_antigravity({"now": 1, "inputs": {"usage": usage}})["details"]["remote"]
        self.assertEqual(remote["state"], "online")
        self.assertEqual(remote["instances"], [])
        self.assertIn("unavailable", remote["note"])
        self.assertEqual(usage["planType"], "Starter")

    def test_empty_account_is_distinct_from_unavailable(self):
        remote = normalize_antigravity({"now": 1, "inputs": {"usage": {"models": [], "remote": {"instances": []}}}})["details"]["remote"]
        self.assertIn("No remote devices", remote["detail"])
        self.assertNotIn("unavailable", remote["note"])


class RemoteCredentialsTest(unittest.TestCase):
    def test_keyring_lookup(self):
        stored = {"token": {"access_token": "test"}}
        with (
            mock.patch.object(antigravity_remote.paths, "IS_MACOS", False),
            mock.patch.object(antigravity_remote.paths, "IS_WINDOWS", False),
            mock.patch.object(antigravity_remote.shutil, "which", return_value="/bin/secret-tool"),
            mock.patch.object(antigravity_remote.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(stored))) as run,
        ):
            self.assertEqual(antigravity_remote._stored_credentials(), stored)
        self.assertEqual(run.call_args.args[0], ["secret-tool", "lookup", "service", "gemini", "username", "antigravity"])
        self.assertEqual(run.call_args.kwargs["timeout"], 2)

    def test_keyring_timeout_falls_back_to_file(self):
        with tempfile.TemporaryDirectory() as home:
            directory = os.path.join(home, ".gemini", "antigravity")
            os.makedirs(directory)
            stored = {"token": {"access_token": "test"}}
            with open(os.path.join(directory, "antigravity-oauth-token"), "w", encoding="utf-8") as f:
                json.dump(stored, f)
            with (
                mock.patch.dict(os.environ, {"HOME": home, "USERPROFILE": home}),
                mock.patch.object(antigravity_remote.paths, "IS_MACOS", False),
                mock.patch.object(antigravity_remote.paths, "IS_WINDOWS", False),
                mock.patch.object(antigravity_remote.shutil, "which", return_value="/bin/secret-tool"),
                mock.patch.object(antigravity_remote.subprocess, "run", side_effect=subprocess.TimeoutExpired("secret-tool", 2)),
            ):
                self.assertEqual(antigravity_remote._stored_credentials(), stored)


class RefreshCooldownTest(unittest.TestCase):
    def test_agy_is_tried_once_per_cooldown(self):
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}):
            self.assertTrue(antigravity_remote._cooled_down())
            self.assertFalse(antigravity_remote._cooled_down())


if __name__ == "__main__":
    unittest.main()
