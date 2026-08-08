"""
Regression tests for:

  1. browser_control.close_tab — tabs opened in the user's REAL browser
     (native open, no Playwright session) never closed, because Jarvis spun
     up a fresh automation window and closed a tab there. Now pure-shortcut
     actions (close_tab / back / forward / reload) fall back to OS keyboard
     shortcuts when no automation session is active.

  2. file_processor._process_text_doc — a free-form analysis action (e.g.
     "translate to Turkish") was silently replaced with the literal word
     "custom", so the LLM never learned what the user actually asked.

  3. file_processor._process_data sort — wrote CSV content into a file that
     kept the input extension (broken .xlsx output for Excel files).
"""

from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# browser_control — tab closing on the user's real browser
# ═══════════════════════════════════════════════════════════════════════════════


class TestCloseTabNativeFallback:
    def test_close_tab_sends_os_shortcut_when_no_session(self):
        """No automation session → must NOT spawn one; send Ctrl+W/Cmd+W instead."""
        import actions.browser_control as bc

        expected = ("command", "w") if bc._OS == "Darwin" else ("ctrl", "w")
        with (
            patch.object(bc, "_native_hotkey", return_value=True) as hotkey,
            patch.object(bc._registry, "_sessions", {}),
            patch.object(bc._registry, "_active_browser", ""),
        ):
            result = bc.browser_control(parameters={"action": "close_tab"})

        assert "native" in result.lower()
        assert "CTRL+W" in result or "COMMAND+W" in result
        hotkey.assert_called_once_with(*expected)

    def test_close_tab_reports_failure_when_hotkey_fails(self):
        import actions.browser_control as bc

        with (
            patch.object(bc, "_native_hotkey", return_value=False),
            patch.object(bc._registry, "_sessions", {}),
            patch.object(bc._registry, "_active_browser", ""),
        ):
            result = bc.browser_control(parameters={"action": "close_tab"})

        assert "could not close_tab" in result.lower()

    def test_back_and_reload_also_use_native_shortcut_without_session(self):
        import actions.browser_control as bc

        expected = {
            "back":   ("command", "left") if bc._OS == "Darwin" else ("alt", "left"),
            "reload": ("command", "r") if bc._OS == "Darwin" else ("f5",),
        }
        for action, keys in expected.items():
            with (
                patch.object(bc, "_native_hotkey", return_value=True) as hotkey,
                patch.object(bc._registry, "_sessions", {}),
                patch.object(bc._registry, "_active_browser", ""),
            ):
                result = bc.browser_control(parameters={"action": action})
            assert "native" in result.lower()
            hotkey.assert_called_once_with(*keys)

    def test_close_tab_uses_session_when_one_exists(self):
        """An active automation session keeps using the Playwright path."""
        import actions.browser_control as bc

        fake_session = MagicMock()
        fake_session.run.return_value = "Tab closed."
        with (
            patch.object(bc._registry, "_sessions", {"chrome": fake_session}),
            patch.object(bc._registry, "_active_browser", "chrome"),
            patch.object(bc, "_native_hotkey") as hotkey,
        ):
            result = bc.browser_control(parameters={"action": "close_tab"})

        assert result == "Tab closed."
        hotkey.assert_not_called()


class TestNewTabNativeFallback:
    def test_new_tab_without_url_sends_os_shortcut_when_no_session(self):
        """'New tab' with no URL must open a tab in the real browser, not an
        automation window."""
        import actions.browser_control as bc

        expected = ("command", "t") if bc._OS == "Darwin" else ("ctrl", "t")
        with (
            patch.object(bc, "_native_hotkey", return_value=True) as hotkey,
            patch.object(bc._registry, "_sessions", {}),
            patch.object(bc._registry, "_active_browser", ""),
        ):
            result = bc.browser_control(parameters={"action": "new_tab"})

        assert "native" in result.lower()
        assert "CTRL+T" in result or "COMMAND+T" in result
        hotkey.assert_called_once_with(*expected)

    def test_new_tab_with_url_opens_natively_when_no_session(self):
        """'New tab' WITH a URL keeps the native-open behavior — no shortcut."""
        import actions.browser_control as bc

        with (
            patch.object(bc, "_open_native", return_value="Opened: https://example.com") as opener,
            patch.object(bc, "_native_hotkey") as hotkey,
            patch.object(bc._registry, "_sessions", {}),
            patch.object(bc._registry, "_active_browser", ""),
        ):
            result = bc.browser_control(
                parameters={"action": "new_tab", "url": "https://example.com"}
            )

        assert result == "Opened: https://example.com"
        hotkey.assert_not_called()
        opener.assert_called_once_with("https://example.com", None)

    def test_new_tab_falls_back_to_native_launch_when_hotkey_fails(self):
        """If pyautogui is unavailable, still open the browser natively."""
        import actions.browser_control as bc

        with (
            patch.object(bc, "_native_hotkey", return_value=False),
            patch.object(bc, "_open_native", return_value="Opened chrome.") as opener,
            patch.object(bc._registry, "_sessions", {}),
            patch.object(bc._registry, "_active_browser", ""),
        ):
            result = bc.browser_control(parameters={"action": "new_tab"})

        assert result == "Opened chrome."
        opener.assert_called_once_with("", None)


class TestTabSwitchingNativeFallback:
    @pytest.mark.parametrize(
        "action, darwin, other",
        [
            ("next_tab", ("command", "shift", "bracketright"), ("ctrl", "tab")),
            ("prev_tab", ("command", "shift", "bracketleft"), ("ctrl", "shift", "tab")),
        ],
    )
    def test_tab_switch_sends_os_shortcut_when_no_session(self, action, darwin, other):
        import actions.browser_control as bc

        expected = darwin if bc._OS == "Darwin" else other
        with (
            patch.object(bc, "_native_hotkey", return_value=True) as hotkey,
            patch.object(bc._registry, "_sessions", {}),
            patch.object(bc._registry, "_active_browser", ""),
        ):
            result = bc.browser_control(parameters={"action": action})

        assert "native" in result.lower()
        hotkey.assert_called_once_with(*expected)

    def test_next_tab_uses_session_when_one_exists(self):
        """An active automation session cycles Playwright pages instead of hotkeys."""
        import actions.browser_control as bc

        fake_session = MagicMock()
        fake_session.run.return_value = "Switched to tab: https://a.com"
        with (
            patch.object(bc._registry, "_sessions", {"chrome": fake_session}),
            patch.object(bc._registry, "_active_browser", "chrome"),
            patch.object(bc, "_native_hotkey") as hotkey,
        ):
            result = bc.browser_control(parameters={"action": "next_tab"})

        assert result == "Switched to tab: https://a.com"
        hotkey.assert_not_called()
        fake_session.next_tab.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# file_processor — text/docx free-form analysis
# ═══════════════════════════════════════════════════════════════════════════════


class TestTextDocCustomAction:
    def test_custom_action_is_kept_as_instruction(self):
        from actions import file_processor as fp

        fake_model = MagicMock()
        fake_model.generate_content.return_value.text = "done"
        with (
            patch.object(fp, "_gemini_client", return_value=fake_model),
            patch.object(fp.Path, "exists", return_value=True),
            patch.object(fp.Path, "read_text", return_value="some document text"),
        ):
            result = fp._process_text_doc(
                fp.Path("/tmp/fake.txt"), "text",
                "translate this to turkish", {},
            )

        assert result == "done"
        prompt = fake_model.generate_content.call_args.args[0]
        assert "translate this to turkish" in prompt
        assert "custom" not in prompt

    def test_explicit_instruction_wins_over_action_name(self):
        from actions import file_processor as fp

        fake_model = MagicMock()
        fake_model.generate_content.return_value.text = "ok"
        with (
            patch.object(fp, "_gemini_client", return_value=fake_model),
            patch.object(fp.Path, "exists", return_value=True),
            patch.object(fp.Path, "read_text", return_value="more text"),
        ):
            result = fp._process_text_doc(
                fp.Path("/tmp/fake.txt"), "text",
                "whatever", {"instruction": "find all email addresses"},
            )

        assert result == "ok"
        prompt = fake_model.generate_content.call_args.args[0]
        assert "find all email addresses" in prompt
        assert "whatever" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# file_processor — data sort output extension
# ═══════════════════════════════════════════════════════════════════════════════


class TestDataSort:
    def test_sort_writes_csv_output(self, tmp_path):
        pd = pytest.importorskip("pandas")
        from actions import file_processor as fp

        csv_file = tmp_path / "data.csv"
        pd.DataFrame({"name": ["b", "a"], "age": [2, 1]}).to_csv(csv_file, index=False)

        result = fp._process_data(csv_file, "csv", "sort", {"column": "age"})

        assert "sorted" in result
        out = tmp_path / "data_sorted.csv"
        assert out.exists()
        df = pd.read_csv(out)
        assert list(df["age"]) == [1, 2]

    def test_sort_on_excel_saves_csv_not_xlsx(self, tmp_path):
        pd = pytest.importorskip("pandas")
        openpyxl = pytest.importorskip("openpyxl")
        from actions import file_processor as fp

        xlsx_file = tmp_path / "data.xlsx"
        pd.DataFrame({"score": [9, 3]}).to_excel(xlsx_file, index=False)

        result = fp._process_data(xlsx_file, "excel", "sort", {"column": "score"})

        assert "sorted" in result
        out = tmp_path / "data_sorted.csv"
        assert out.exists()
        df = pd.read_csv(out)
        assert list(df["score"]) == [3, 9]


# ═══════════════════════════════════════════════════════════════════════════════
# file_processor — CSV read/analyze/stats with pandas 3.x
# ═══════════════════════════════════════════════════════════════════════════════
# pandas >= 3.0 removed the `errors` kwarg from read_csv, which crashed every
# CSV operation. The shim retries without it — these tests guard that path.


class TestCsvPandasCompat:
    def test_csv_read_falls_back_when_errors_kwarg_rejected(self, tmp_path):
        """The compatibility shim: if read_csv rejects `errors` (pandas >= 3.0),
        the call is retried without it and the file still reads."""
        pd = pytest.importorskip("pandas")
        from actions import file_processor as fp

        csv_file = tmp_path / "data.csv"
        pd.DataFrame({"age": [1, 2]}).to_csv(csv_file, index=False)

        real_read_csv = pd.read_csv

        def _flaky(*args, **kwargs):
            if "errors" in kwargs:
                raise TypeError("read_csv() got an unexpected keyword argument 'errors'")
            return real_read_csv(*args, **kwargs)

        with patch("pandas.read_csv", side_effect=_flaky):
            result = fp._process_data(csv_file, "csv", "info", {})

        assert "Rows: 2" in result

    def test_csv_analyze_works_with_fixed_read(self, tmp_path):
        """analyze reads the CSV then asks the LLM — must not crash on the read."""
        pd = pytest.importorskip("pandas")
        from actions import file_processor as fp

        csv_file = tmp_path / "data.csv"
        pd.DataFrame({"name": ["alice", "bob"], "age": [30, 25]}).to_csv(
            csv_file, index=False
        )

        fake_model = MagicMock()
        fake_model.generate_content.return_value.text = "insights"
        with patch.object(fp, "_gemini_client", return_value=fake_model):
            result = fp._process_data(csv_file, "csv", "analyze", {})

        assert result == "insights"
        prompt = fake_model.generate_content.call_args.args[0]
        assert "name" in prompt and "age" in prompt
        assert "alice" in prompt

    def test_csv_stats_works_with_fixed_read(self, tmp_path):
        """stats is pure pandas (no LLM) — must still work after the read fix."""
        pd = pytest.importorskip("pandas")
        from actions import file_processor as fp

        csv_file = tmp_path / "data.csv"
        pd.DataFrame({"age": [30, 25, 35]}).to_csv(csv_file, index=False)

        result = fp._process_data(csv_file, "csv", "stats", {})

        assert "Statistics:" in result
        assert "age" in result
