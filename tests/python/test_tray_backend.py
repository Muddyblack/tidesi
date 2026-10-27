"""Worker results must reach QML and its property bindings on the GUI thread."""

import ast
import importlib.util
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

from _support import REPO

HAS_PYSIDE = importlib.util.find_spec("PySide6") is not None
WINDOWS_APP = Path(REPO) / "hosts" / "desktop" / "app.py"


def load_production_finish_sessions():
    tree = ast.parse(WINDOWS_APP.read_text(encoding="utf-8"), filename=str(WINDOWS_APP))
    backend_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Backend")
    method = next(node for node in backend_class.body if isinstance(node, ast.FunctionDef) and node.name == "_finish_sessions")
    extracted = ast.Module(
        body=[ast.ClassDef(name="ExtractedBackend", bases=[], keywords=[], body=[method], decorator_list=[])],
        type_ignores=[],
    )
    ast.fix_missing_locations(extracted)
    namespace = {"Slot": lambda *args: lambda function: function, "json": json}
    exec(compile(extracted, str(WINDOWS_APP), "exec"), namespace)
    return namespace["ExtractedBackend"]


class RequestGenerationSignal:
    def __init__(self):
        self.events = []
        self.listeners = []

    def connect(self, listener):
        self.listeners.append(listener)

    def emit(self, *args):
        self.events.append(args)
        for listener in self.listeners:
            listener(*args)


class SessionStateModel:
    def __init__(self, request_id, sessions):
        self.request_id = request_id
        self.sessions = sessions
        self.loading = True

    def apply_completion(self, result, query, request_id):
        del query
        if request_id != self.request_id:
            return
        self.sessions = json.loads(result)["sessions"]
        self.loading = False


class RequestGenerationBehaviorTest(unittest.TestCase):
    def test_late_completion_does_not_replace_data_or_clear_newer_loading_state(self):
        backend_type = load_production_finish_sessions()
        backend = object.__new__(backend_type)
        backend._sessions_request_id = 2
        signal = RequestGenerationSignal()
        backend.sessionsReady = signal
        state = SessionStateModel(2, [{"id": "loading"}])
        signal.connect(state.apply_completion)

        backend._finish_sessions('{"sessions":[{"id":"late"}]}', "", "old", 1)
        self.assertEqual(signal.events, [])
        self.assertEqual(state.sessions, [{"id": "loading"}])
        self.assertTrue(state.loading)

        backend._finish_sessions('{"sessions":[{"id":"current"}]}', "", "current", 2)
        self.assertEqual(signal.events, [('{"sessions":[{"id":"current"}]}', "current", 2)])
        self.assertEqual(state.sessions, [{"id": "current"}])
        self.assertFalse(state.loading)


def load_backend_method(name, extra=None):
    tree = ast.parse(WINDOWS_APP.read_text(encoding="utf-8"), filename=str(WINDOWS_APP))
    backend_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Backend")
    method = next(node for node in backend_class.body if isinstance(node, ast.FunctionDef) and node.name == name)
    extracted = ast.Module(
        body=[ast.ClassDef(name="ExtractedBackend", bases=[], keywords=[], body=[method], decorator_list=[])],
        type_ignores=[],
    )
    ast.fix_missing_locations(extracted)
    namespace = {"Slot": lambda *args: lambda function: function, "json": json, **(extra or {})}
    exec(compile(extracted, str(WINDOWS_APP), "exec"), namespace)
    return namespace["ExtractedBackend"]


class RateQueryOffThreadTest(unittest.TestCase):
    """The pricing catalog parse must not run on the GUI thread, and a
    superseded query's result must not reach the UI."""

    def _source(self, name):
        tree = ast.parse(WINDOWS_APP.read_text(encoding="utf-8"), filename=str(WINDOWS_APP))
        backend_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Backend")
        method = next(node for node in backend_class.body if isinstance(node, ast.FunctionDef) and node.name == name)
        return ast.unparse(method)

    def test_request_rates_submits_to_the_pool_not_inline(self):
        source = self._source("requestRates")
        self.assertIn("_pool.submit", source)
        self.assertNotIn("catalog_rows", source)

    def test_catalog_parse_happens_in_the_worker(self):
        source = self._source("_query_rates")
        self.assertIn("catalog_rows", source)
        self.assertIn("_ratesCompleted.emit", source)

    def test_a_superseded_rate_result_is_dropped(self):
        backend_type = load_backend_method("_finish_rates")
        backend = object.__new__(backend_type)
        backend._rates_request_id = 5
        signal = RequestGenerationSignal()
        backend.ratesReady = signal
        backend._finish_rates("old", 4)
        self.assertEqual(signal.events, [])
        backend._finish_rates("current", 5)
        self.assertEqual(signal.events, [("current",)])

    def test_request_rates_ignores_a_non_newer_request_id(self):
        backend_type = load_backend_method("requestRates")
        backend = object.__new__(backend_type)
        backend._rates_request_id = 3
        submitted = []
        backend._query_rates = lambda *a: None
        backend._pool = mock.Mock()
        backend._pool.submit = lambda *a, **k: submitted.append(a)
        backend.requestRates("q", 40, 0, 2)
        self.assertEqual(submitted, [])
        backend.requestRates("q", 40, 0, 4)
        self.assertEqual(len(submitted), 1)

    def test_pricing_refresh_does_not_take_the_environment_lock(self):
        source = Path(REPO).joinpath("hosts", "desktop", "app.py").read_text(encoding="utf-8")
        start = source.index("def refresh_pricing_json():")
        end = source.index("def _restore_environ", start)
        self.assertNotIn("_env_lock", source[start:end])


class SessionRefreshResponseTest(unittest.TestCase):
    def test_refresh_runs_on_its_own_worker_without_waiting_on_the_pool(self):
        # A worker that submits to its own bounded pool and waits deadlocks once
        # every worker does the same.
        responses = RequestGenerationSignal()
        calls = []
        backend_type = load_backend_method(
            "_refresh_sessions",
            {
                "refresh_sessions_json": lambda *args, **kwargs: calls.append("refresh") or '{"sessions":[{"title":"refreshed"}]}',
                "collect_sessions_json": lambda *args, **kwargs: calls.append("cache") or '{"sessions":[{"title":"cached"}]}',
            },
        )
        backend = object.__new__(backend_type)
        backend._pool = mock.Mock()
        backend._pool.submit.side_effect = AssertionError("the refresh waited on its own pool")
        backend._sessionsCompleted = responses

        backend._refresh_sessions("", 4, 0, True, None)

        self.assertEqual(calls, ["refresh"])
        self.assertEqual([json.loads(event[0])["sessions"][0]["title"] for event in responses.events], ["refreshed"])


if HAS_PYSIDE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, os.path.join(REPO, "hosts", "desktop"))
    import app
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication


@unittest.skipUnless(HAS_PYSIDE, "PySide6 not installed")
class BackendThreadTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QGuiApplication.instance() or QGuiApplication([])

    def setUp(self):
        self.backend = app.Backend()
        self.addCleanup(self.backend._pool.shutdown, wait=True)
        self.events = []
        self.gui_thread = threading.get_ident()
        for name in (
            "busyChanged",
            "snapshotReady",
            "providerReady",
            "refreshFailed",
            "historyFinished",
            "pricingBusyChanged",
            "pricingRefreshFinished",
            "pricingRefreshFailed",
        ):
            # A direct observer records the emission thread, without Qt
            # concealing an unsafe emission by queueing the test callback.
            getattr(self.backend, name).connect(
                lambda *args, name=name: self.events.append((name, args, threading.get_ident())),
                Qt.DirectConnection,
            )

    def check_refresh(self, error=None):
        with mock.patch("app.collect_snapshot", return_value='{"providers":[]}', side_effect=error):
            self.backend.refresh()
            # Wait for the worker without pumping GUI events. The UI must
            # remain busy until the queued completion has been handled.
            self.backend._pool.shutdown(wait=True)
            self.assertTrue(self.backend.busy)
            self.assertEqual(self.events, [("busyChanged", (), self.gui_thread)])
            self.qt_app.processEvents()
            self.assertFalse(self.backend.busy)
            expected = ("refreshFailed", ("usage backend failed: offline",)) if error else ("snapshotReady", ('{"providers":[]}',))
            self.assertEqual(self.events[1:], [(*expected, self.gui_thread), ("busyChanged", (), self.gui_thread)])

    def test_refresh_success_is_published_on_the_gui_thread(self):
        self.check_refresh()

    def test_refresh_failure_is_published_on_the_gui_thread(self):
        self.check_refresh(RuntimeError("offline"))

    def test_provider_update_reaches_the_gui_before_refresh_completion(self):
        ready = threading.Event()
        release = threading.Event()

        def collect(*, on_provider):
            on_provider({"id": "cursor", "ok": True})
            ready.set()
            if not release.wait(5):
                raise TimeoutError("test did not release the final snapshot")
            return '{"providers":[{"id":"cursor","ok":true}]}'

        with mock.patch("app.collect_snapshot", side_effect=collect):
            self.backend.refresh()
            try:
                self.assertTrue(ready.wait(2))
                self.qt_app.processEvents()
                self.assertTrue(self.backend.busy)
                updates = [event for event in self.events if event[0] == "providerReady"]
                self.assertEqual(len(updates), 1)
                self.assertEqual(updates[0][2], self.gui_thread)
                self.assertEqual(json.loads(updates[0][1][0])["providers"], [{"id": "cursor", "ok": True}])
                self.assertFalse(any(event[0] == "snapshotReady" for event in self.events))
            finally:
                release.set()
            self.backend._pool.shutdown(wait=True)
            self.qt_app.processEvents()
        self.assertFalse(self.backend.busy)
        self.assertEqual(sum(event[0] == "snapshotReady" for event in self.events), 1)

    def check_pricing_refresh(self, result=None, error=None):
        with mock.patch("app.refresh_pricing_json", return_value=result, side_effect=error) as refresh:
            self.backend.refreshPricing()
            self.backend.refreshPricing()
            self.backend._pool.shutdown(wait=True)
            self.assertTrue(self.backend.pricingBusy)
            self.assertEqual(self.events, [("pricingBusyChanged", (), self.gui_thread)])
            self.qt_app.processEvents()
            self.assertFalse(self.backend.pricingBusy)
            if error:
                expected = ("pricingRefreshFailed", ("pricing backend failed: offline",), self.gui_thread)
            else:
                expected = ("pricingRefreshFinished", (result,), self.gui_thread)
            self.assertEqual(
                self.events[1:],
                [expected, ("pricingBusyChanged", (), self.gui_thread)],
            )
            refresh.assert_called_once_with()

    def test_pricing_refresh_success_rejects_duplicate_calls(self):
        self.check_pricing_refresh('{"ok":true,"status":"refreshed","fetchedAt":1800000000,"error":""}')

    def test_pricing_refresh_preserves_stale_and_hard_failure_statuses(self):
        for result in (
            '{"ok":true,"status":"stale-good","fetchedAt":1799000000,"error":"download failed"}',
            '{"ok":false,"status":"no-cache","fetchedAt":0,"error":"No usable pricing rates"}',
        ):
            with self.subTest(result=result):
                backend = app.Backend()
                self.addCleanup(backend._pool.shutdown, wait=True)
                events = []
                backend.pricingRefreshFinished.connect(
                    lambda text, events=events: events.append(("finished", text)),
                    Qt.DirectConnection,
                )
                with mock.patch("app.refresh_pricing_json", return_value=result):
                    backend.refreshPricing()
                    backend._pool.shutdown(wait=True)
                    self.qt_app.processEvents()
                self.assertEqual(events, [("finished", result)])

    def test_history_is_published_on_the_gui_thread(self):
        with mock.patch("app.historyio.run", return_value='{"data":[]}'):
            self.backend.history("autoload", "")
            self.backend._pool.shutdown(wait=True)
        self.assertEqual(self.events, [])
        self.qt_app.processEvents()
        self.assertEqual(self.events, [("historyFinished", ("autoload", '{"data":[]}'), self.gui_thread)])

    def test_session_refresh_uses_query_only_collection(self):
        with mock.patch("app.collect_sessions", return_value={}) as collect:
            self.backend.refreshSessions("query", 1, 3)
            self.backend._sessions_future.result(timeout=5)

        self.assertEqual(collect.call_args_list, [mock.call("query", limit=60, offset=3)])

    def test_session_query_forwards_source_ids_through_windows_bridge(self):
        with mock.patch("app.collect_sessions_json", return_value='{"sessions":[]}') as collect:
            self.backend.refreshSessions("query", 1, 3, ["openai", "antigravity"])
            self.backend._sessions_future.result(timeout=5)

        self.assertEqual(
            collect.call_args_list,
            [
                mock.call(
                    "query",
                    source_ids=["openai", "antigravity"],
                    limit=60,
                    offset=3,
                )
            ],
        )

    def test_explicit_session_refresh_uses_global_collection(self):
        with mock.patch("app.refresh_sessions", return_value={}) as refresh:
            self.backend.refreshSessionsAndQuery("query", 1, 3)
            self.backend._sessions_future.result(timeout=5)

        self.assertEqual(refresh.call_args_list, [mock.call("query", limit=60, offset=3)])

    def test_explicit_session_refresh_forwards_source_ids_through_windows_bridge(self):
        with mock.patch("app.refresh_sessions_json", return_value='{"sessions":[]}') as refresh:
            self.backend.refreshSessionsAndQuery("query", 1, 3, ["openai", "opencode"])
            self.backend._sessions_future.result(timeout=5)

        self.assertEqual(
            refresh.call_args_list,
            [
                mock.call(
                    "query",
                    source_ids=["openai", "opencode"],
                    limit=60,
                    offset=3,
                )
            ],
        )

    def test_session_refresh_cancels_queued_superseded_future(self):
        release = threading.Event()
        barrier = threading.Barrier(4)
        self.addCleanup(release.set)

        def block_worker():
            barrier.wait()
            release.wait()

        blockers = [self.backend._pool.submit(block_worker) for _ in range(3)]
        barrier.wait(timeout=5)
        with mock.patch("app.collect_sessions_json", return_value='{"sessions":[]}') as collect:
            self.backend.refreshSessions("first", 1, 0)
            first = self.backend._sessions_future
            self.backend.refreshSessions("second", 2, 0)
            second = self.backend._sessions_future

            self.assertTrue(first.cancelled())
            self.assertFalse(second.cancelled())

            release.set()
            for blocker in blockers:
                blocker.result(timeout=5)
            second.result(timeout=5)

        self.assertEqual(collect.call_args_list, [mock.call("second", limit=60, offset=0)])

    def test_late_session_completion_cannot_replace_newer_generation(self):
        events = []
        self.backend.sessionsReady.connect(lambda result, query, request_id: events.append((result, query, request_id)))
        self.backend._sessions_request_id = 2

        self.backend._finish_sessions('{"sessions":["late"]}', "", "first", 1)
        self.backend._finish_sessions('{"sessions":["current"]}', "", "second", 2)

        self.assertEqual(events, [('{"sessions":["current"]}', "second", 2)])


if __name__ == "__main__":
    unittest.main()
