from __future__ import annotations

import unittest
from unittest import mock

from bridge import bridge
from bridge.notebooklm.errors import AuthenticationError, ProtocolError
from bridge.notebooklm.gateway import NotebookLMGateway


class NotebookStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_cache = bridge.AUTH_CACHE
        self.original_cache_at = bridge.AUTH_CACHE_AT
        self.original_refresh = bridge.AUTH_REFRESH_IN_FLIGHT
        self.original_login_watch = bridge.AUTH_LOGIN_WATCH_IN_FLIGHT
        self.original_audit_sync = dict(bridge.AUDIT_SYNC)

    def tearDown(self) -> None:
        bridge.AUTH_CACHE = self.original_cache
        bridge.AUTH_CACHE_AT = self.original_cache_at
        bridge.AUTH_REFRESH_IN_FLIGHT = self.original_refresh
        bridge.AUTH_LOGIN_WATCH_IN_FLIGHT = self.original_login_watch
        bridge.AUDIT_SYNC = self.original_audit_sync

    def test_saved_credentials_are_not_reported_as_connected_before_live_validation(self) -> None:
        bridge.AUTH_CACHE = None
        bridge.AUTH_CACHE_AT = 0.0
        bridge.AUTH_REFRESH_IN_FLIGHT = False
        saved_only = {
            "state": "authenticated",
            "live": False,
            "account": "user@example.com",
            "message": "saved credentials",
        }

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "status", return_value=saved_only), mock.patch.object(
            bridge, "refresh_notebook_status_in_background"
        ) as refresh:
            status = bridge.local_notebook_status()

        self.assertEqual(status["state"], "checking")
        self.assertFalse(status["live"])
        self.assertTrue(status["checking"])
        refresh.assert_called_once_with()

    def test_failed_live_check_is_not_overwritten_by_saved_credentials(self) -> None:
        bridge.AUTH_CACHE = {
            "state": "unavailable",
            "live": False,
            "stage": "unavailable",
            "message": "network unavailable",
        }
        bridge.AUTH_CACHE_AT = bridge.time.monotonic()
        bridge.AUTH_REFRESH_IN_FLIGHT = False
        saved_only = {"state": "authenticated", "live": False, "account": "user@example.com"}

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "status", return_value=saved_only):
            status = bridge.local_notebook_status()

        self.assertEqual(status["state"], "unavailable")
        self.assertFalse(status["live"])

    def test_completed_browser_login_replaces_stale_waiting_cache_immediately(self) -> None:
        """A completed browser login must not wait for the cache TTL to be noticed."""
        bridge.AUTH_CACHE = {
            "state": "login_in_progress",
            "live": False,
            "stage": "browser_login",
            "message": "waiting for browser login",
        }
        bridge.AUTH_CACHE_AT = bridge.time.monotonic()
        bridge.AUTH_REFRESH_IN_FLIGHT = False
        completed_login = {
            "state": "authenticated",
            "live": False,
            "stage": "connected",
            "account": "user@example.com",
            "message": "browser session saved",
        }

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "status", return_value=completed_login), mock.patch.object(
            bridge, "refresh_notebook_status_in_background"
        ) as refresh:
            status = bridge.local_notebook_status()

        self.assertEqual(status["state"], "checking")
        self.assertFalse(status["live"])
        self.assertTrue(status["checking"])
        self.assertFalse(status["loginLaunching"])
        refresh.assert_called_once_with()

    def test_browser_login_completion_refreshes_live_cache_without_page_polling(self) -> None:
        """The bridge must notice login completion even when the dialog was closed."""
        bridge.AUTH_CACHE = {
            "state": "login_in_progress",
            "live": False,
            "stage": "browser_login",
        }
        bridge.AUTH_CACHE_AT = bridge.time.monotonic()
        local_states = iter([
            {"state": "login_in_progress", "stage": "browser_login"},
            {"state": "authenticated", "stage": "connected", "account": "user@example.com"},
        ])
        verified = {
            "state": "authenticated",
            "stage": "verified",
            "live": True,
            "account": "user@example.com",
        }

        def gateway_status(*, live: bool = False) -> dict[str, object]:
            return dict(verified if live else next(local_states))

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "status", side_effect=gateway_status), mock.patch.object(
            bridge.threading.Thread, "start", autospec=True
        ) as start, mock.patch.object(bridge.time, "sleep"):
            bridge.watch_notebook_login_completion_in_background()
            worker = start.call_args.args[0]
            worker.run()

        self.assertEqual(bridge.AUTH_CACHE["state"], "authenticated")
        self.assertTrue(bridge.AUTH_CACHE["live"])
        self.assertEqual(bridge.AUTH_CACHE["account"], "user@example.com")

    def test_login_watcher_retries_a_transient_live_verification_failure(self) -> None:
        bridge.AUTH_CACHE = {"state": "login_in_progress", "live": False}
        bridge.AUTH_CACHE_AT = bridge.time.monotonic()
        local = {"state": "authenticated", "stage": "connected", "account": "user@example.com"}
        live_states = iter([
            {"state": "unavailable", "stage": "unavailable", "live": False, "message": "timeout"},
            {"state": "authenticated", "stage": "verified", "live": True, "account": "user@example.com"},
        ])

        def gateway_status(*, live: bool = False) -> dict[str, object]:
            return dict(next(live_states) if live else local)

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "status", side_effect=gateway_status), mock.patch.object(
            bridge.threading.Thread, "start", autospec=True
        ) as start, mock.patch.object(bridge.time, "sleep") as sleep:
            bridge.watch_notebook_login_completion_in_background()
            start.call_args.args[0].run()

        self.assertEqual(bridge.AUTH_CACHE["state"], "authenticated")
        self.assertTrue(bridge.AUTH_CACHE["live"])
        sleep.assert_called()

    def test_background_revalidation_does_not_hide_a_verified_live_session(self) -> None:
        """A routine refresh must not make an already verified account look pending."""
        bridge.AUTH_CACHE = {
            "state": "authenticated",
            "live": True,
            "stage": "verified",
            "account": "user@example.com",
            "message": "NotebookLM live session verified",
        }
        bridge.AUTH_CACHE_AT = bridge.time.monotonic()
        bridge.AUTH_REFRESH_IN_FLIGHT = True
        saved_session = {
            "state": "authenticated",
            "live": False,
            "stage": "connected",
            "account": "user@example.com",
        }

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "status", return_value=saved_session):
            status = bridge.local_notebook_status()

        self.assertEqual(status["state"], "authenticated")
        self.assertTrue(status["live"])
        self.assertFalse(status["checking"])
        self.assertTrue(status["revalidating"])

    def test_live_authentication_rejection_is_marked_expired(self) -> None:
        gateway = object.__new__(NotebookLMGateway)
        gateway.auth = mock.MagicMock()
        gateway.auth.status.return_value = {"state": "authenticated", "account": "user@example.com"}
        gateway.client = mock.MagicMock()
        gateway.client.list_notebooks.side_effect = AuthenticationError("expired")

        status = gateway.status(live=True)

        self.assertEqual(status["state"], "not_authenticated")
        self.assertEqual(status["stage"], "expired")
        gateway.auth.invalidate_session.assert_called_once()

    def test_transient_live_check_failure_is_not_mislabeled_as_logged_out(self) -> None:
        gateway = object.__new__(NotebookLMGateway)
        gateway.auth = mock.MagicMock()
        gateway.auth.status.return_value = {"state": "authenticated", "account": "user@example.com"}
        gateway.client = mock.MagicMock()
        gateway.client.list_notebooks.side_effect = ProtocolError("network timeout")

        status = gateway.status(live=True)

        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["stage"], "unavailable")
        gateway.auth.invalidate_session.assert_not_called()

    def test_audit_notebook_sync_uses_an_interactive_timeout(self) -> None:
        bridge.AUDIT_SYNC = {"state": "idle", "notebookCount": None, "error": None, "syncedAt": None}

        with mock.patch.object(bridge.NOTEBOOK_GATEWAY, "list_notebooks", return_value=[{"id": "a"}]) as listing, mock.patch.object(
            bridge.threading.Thread, "start", autospec=True
        ) as start:
            bridge.refresh_notebook_audit_in_background()
            worker = start.call_args.args[0]
            worker.run()

        listing.assert_called_once_with(timeout=15.0, retry_network=False)
        self.assertEqual(bridge.AUDIT_SYNC["state"], "ready")
        self.assertEqual(bridge.AUDIT_SYNC["notebookCount"], 1)


if __name__ == "__main__":
    unittest.main()
