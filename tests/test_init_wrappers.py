"""
Tests for the thin re-export wrappers in yt_dont_recommend/__init__.py.

These four functions (do_login, fetch_subscriptions, process_channels,
check_selectors) exist only to expose browser/diagnostics entry points at
the package root so external callers and tests can patch them with
`patch("yt_dont_recommend.X")`. The wrappers forward to their module-level
implementations; the tests here confirm that forwarding works.
"""

from unittest.mock import MagicMock, patch

import yt_dont_recommend as ydr


class TestInitWrappers:
    def test_do_login_forwards_to_browser_module(self):
        with patch("yt_dont_recommend.browser.do_login") as mock_do_login:
            mock_do_login.return_value = None
            result = ydr.do_login()
            mock_do_login.assert_called_once_with()
            assert result is None

    def test_fetch_subscriptions_forwards_and_returns_set(self):
        page = MagicMock()
        with patch("yt_dont_recommend.browser.fetch_subscriptions") as mock_fetch:
            mock_fetch.return_value = {"@alpha", "@beta"}
            result = ydr.fetch_subscriptions(page)
            mock_fetch.assert_called_once_with(page)
            assert result == {"@alpha", "@beta"}

    def test_process_channels_forwards_all_kwargs(self):
        channel_sources = {"@a": "deslop"}
        to_unblock = ["@b"]
        state = {"blocked_by": {}}
        clickbait_cfg = {"video": {"title": {}}}
        exclude_set = {"@c"}
        keyword_compiled = [("foo", "substring", 1)]
        keyword_excludes = {"@d"}
        browser_handle = (None, None, None)

        with patch("yt_dont_recommend.browser.process_channels") as mock_pc:
            mock_pc.return_value = None
            ydr.process_channels(
                channel_sources,
                to_unblock=to_unblock,
                state=state,
                dry_run=True,
                limit=5,
                headless=True,
                clickbait_cfg=clickbait_cfg,
                exclude_set=exclude_set,
                keyword_compiled=keyword_compiled,
                keyword_excludes=keyword_excludes,
                _browser=browser_handle,
            )

        mock_pc.assert_called_once_with(
            channel_sources,
            to_unblock=to_unblock,
            state=state,
            dry_run=True,
            limit=5,
            headless=True,
            clickbait_cfg=clickbait_cfg,
            exclude_set=exclude_set,
            keyword_compiled=keyword_compiled,
            keyword_excludes=keyword_excludes,
            _browser=browser_handle,
        )

    def test_check_selectors_forwards_to_diagnostics(self):
        with patch("yt_dont_recommend.diagnostics.check_selectors") as mock_cs:
            mock_cs.return_value = True
            result = ydr.check_selectors("@custom")
            mock_cs.assert_called_once_with("@custom")
            assert result is True

    def test_check_selectors_default_target(self):
        with patch("yt_dont_recommend.diagnostics.check_selectors") as mock_cs:
            mock_cs.return_value = False
            result = ydr.check_selectors()
            mock_cs.assert_called_once_with("@YouTube")
            assert result is False
