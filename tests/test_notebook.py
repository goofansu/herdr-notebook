from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "herdr_notebook.py")
PANE = "w1:p1"
CWD = "/work/tree with spaces"
WORKSPACE = "tree with spaces"
NOTEBOOK_FILE = "w1%3Ap1.md"
# The plugin stamps memos against the local wall clock; so does this.
TODAY = datetime.date.today().isoformat()  # noqa: DTZ011

HERDR = r"""#!{python}
import json
import os
import sys

with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(["herdr", *sys.argv[1:]]) + "\n")

if sys.argv[1:] == ["pane", "list"]:
    panes = os.environ.get("LIVE_PANES", "").split()
    print(json.dumps({{"result": {{"panes": [{{"pane_id": p}} for p in panes]}}}}))

if sys.argv[1:3] == ["pane", "report-metadata"]:
    sys.exit(int(os.environ.get("REPORT_EXIT", "0")))

sys.exit(int(os.environ.get("HERDR_EXIT", "0")))
"""

PAGER = r"""#!{python}
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    sys.stdout.write(handle.read())
"""

EDITOR = r"""#!{python}
import os
import sys

line = os.environ.get("EDITOR_APPENDS")
if line:
    with open(sys.argv[1], "a", encoding="utf-8") as handle:
        handle.write(line + "\n")
sys.exit(int(os.environ.get("EDITOR_EXIT", "0")))
"""


class NotebookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.bin)
        os.makedirs(self.state)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        for program, template in (
            ("herdr", HERDR),
            ("fake-editor", EDITOR),
            ("fake-pager", PAGER),
        ):
            path = os.path.join(self.bin, program)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(template.format(python=sys.executable))
            os.chmod(path, 0o755)

    # Invocation helpers.

    def invoke(self, command: str, stdin: str = "", **extra):
        env = {
            "PATH": self.bin,
            "CALL_LOG": self.log,
            "HERDR_PLUGIN_STATE_DIR": self.state,
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                {
                    "focused_pane_id": PANE,
                    "focused_pane_cwd": CWD,
                    "workspace_label": WORKSPACE,
                }
            ),
        }
        env.update({key: str(value) for key, value in extra.items()})
        return subprocess.run(
            [sys.executable, PLUGIN, command],
            input=stdin,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def popup(self, command: str, stdin: str = "", pane: str = PANE, **extra):
        return self.invoke(command, stdin=stdin, HERDR_NOTEBOOK_PANE=pane, **extra)

    def hook(self, command: str, event: dict, **extra):
        return self.invoke(command, HERDR_PLUGIN_EVENT_JSON=json.dumps(event), **extra)

    # Assertion helpers.

    def calls(self) -> list[list[str]]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def pane_open(self, entrypoint: str, pane: str = PANE) -> list[str]:
        return [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "herdr-notebook",
            "--entrypoint",
            entrypoint,
            "--cwd",
            CWD,
            "--env",
            f"HERDR_NOTEBOOK_PANE={pane}",
        ]

    def published(self, memo: str, pane: str = PANE) -> list[str]:
        return [
            "herdr",
            "pane",
            "report-metadata",
            pane,
            "--source",
            "plugin:herdr-notebook",
            "--title",
            memo,
            "--token",
            f"notebook={memo}",
        ]

    def cleared(self, pane: str = PANE) -> list[str]:
        return [
            "herdr",
            "pane",
            "report-metadata",
            pane,
            "--source",
            "plugin:herdr-notebook",
            "--clear-title",
            "--clear-token",
            "notebook",
        ]

    # Notebook file helpers.

    def notebook(self, name: str = NOTEBOOK_FILE) -> str:
        return os.path.join(self.state, "panes", name)

    def write_notebook(self, body: list[str], name: str = NOTEBOOK_FILE) -> str:
        path = self.notebook(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(body) + "\n")
        return path

    def lines(self, name: str = NOTEBOOK_FILE) -> list[str]:
        with open(self.notebook(name), encoding="utf-8") as handle:
            return handle.read().splitlines()

    def archived(self) -> list[str]:
        directory = os.path.join(self.state, "archive")
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    # Actions.

    def test_opens_the_memo_prompt_over_a_notebook_it_created(self) -> None:
        result = self.invoke("add-memo")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.pane_open("memo-prompt")])
        header = self.lines()
        self.assertEqual(header[0], f"# {PANE} — {WORKSPACE}")
        self.assertIn(CWD, header[2])
        self.assertEqual(header[-2], f"## {TODAY}")

    def test_keeps_an_existing_notebook_when_an_action_opens_it_again(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 earlier"])
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.pane_open("notebook-editor")])
        self.assertEqual(self.lines()[0], "# kept")

    def test_opens_the_viewer_and_the_clear_prompt_for_an_existing_notebook(
        self,
    ) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 earlier"])
        for command, entrypoint in (
            ("show-notebook", "notebook-viewer"),
            ("clear-notebook", "clear-confirm"),
        ):
            with self.subTest(command=command):
                result = self.invoke(command)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.calls()[-1], self.pane_open(entrypoint))

    def test_notifies_instead_of_opening_a_viewer_without_a_notebook(self) -> None:
        result = self.invoke("show-notebook")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{PANE} has no notebook yet", result.stderr)
        self.assertEqual(
            self.calls(),
            [
                [
                    "herdr",
                    "notification",
                    "show",
                    "Pane notebook unavailable",
                    "--body",
                    f"{PANE} has no notebook yet.",
                ]
            ],
        )

    def test_notifies_instead_of_creating_a_notebook_without_a_focused_pane(
        self,
    ) -> None:
        result = self.invoke("add-memo", HERDR_PLUGIN_CONTEXT_JSON="{}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no focused pane", result.stderr)
        self.assertEqual(self.calls()[-1][:3], ["herdr", "notification", "show"])
        self.assertFalse(os.path.exists(os.path.join(self.state, "panes")))

    def test_reports_the_popup_failure_when_herdr_refuses_to_open_one(self) -> None:
        result = self.invoke("add-memo", HERDR_EXIT=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not open the notebook popup", result.stderr)

    # Memo prompt.

    def test_appends_a_timestamped_memo_and_shows_it_on_the_pane(self) -> None:
        self.invoke("add-memo")
        result = self.popup("run-add-memo", stdin="waiting on CI\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines()[-1][8:], "waiting on CI")
        self.assertRegex(self.lines()[-1], r"^- \d{2}:\d{2} waiting on CI$")
        self.assertEqual(self.calls()[1], self.published("waiting on CI"))
        self.assertEqual(self.calls()[2][:3], ["herdr", "notification", "show"])

    def test_leaves_a_blank_line_under_each_date_heading(self) -> None:
        self.invoke("add-memo")
        self.popup("run-add-memo", stdin="first\n")
        lines = self.lines()
        self.assertEqual(lines[-3], f"## {TODAY}")
        self.assertEqual(lines[-2], "")
        self.assertEqual(lines[-1][8:], "first")

    def test_keeps_same_day_memos_under_one_date_heading(self) -> None:
        self.invoke("add-memo")
        self.popup("run-add-memo", stdin="first\n")
        self.popup("run-add-memo", stdin="second\n")
        lines = self.lines()
        self.assertEqual(lines.count(f"## {TODAY}"), 1)
        self.assertEqual([line[8:] for line in lines[-2:]], ["first", "second"])

    def test_starts_a_new_date_heading_for_a_notebook_left_from_another_day(
        self,
    ) -> None:
        self.write_notebook(["# kept", "", "## 2000-01-01", "", "- 09:00 long ago"])
        self.popup("run-add-memo", stdin="today\n")
        lines = self.lines()
        self.assertEqual(lines[2], "## 2000-01-01")
        self.assertEqual(lines[4], "- 09:00 long ago")
        self.assertEqual(lines[6], f"## {TODAY}")
        self.assertEqual(lines[-1][8:], "today")

    def test_cancels_an_empty_memo_without_touching_the_notebook(self) -> None:
        self.invoke("add-memo")
        before = self.lines()
        for entry in ("\n", "   \n", ""):
            with self.subTest(entry=entry):
                result = self.popup("run-add-memo", stdin=entry)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.lines(), before)
        self.assertEqual(self.calls(), [self.pane_open("memo-prompt")])

    def test_drops_control_characters_a_popup_keypress_can_send(self) -> None:
        self.invoke("add-memo")
        result = self.popup("run-add-memo", stdin="\x1brebasing\tonto main\x07\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines()[-1][8:], "rebasing onto main")
        self.assertEqual(self.calls()[1], self.published("rebasing onto main"))

    def test_shows_the_latest_memo_in_the_prompt(self) -> None:
        self.write_notebook(
            ["# kept", "", f"## {TODAY}", "", "- 09:00 first", "- 10:00 latest"]
        )
        result = self.popup("run-add-memo", stdin="\n")
        self.assertIn("Latest — latest", result.stdout)

    def test_fails_without_a_pane_when_a_popup_is_wired_wrong(self) -> None:
        result = self.invoke("run-add-memo", stdin="anything\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HERDR_NOTEBOOK_PANE is not set", result.stderr)
        self.assertEqual(
            self.calls()[-1][:5],
            ["herdr", "notification", "show", "Pane notebook failed", "--body"],
        )

    # Editor and viewer.

    def test_publishes_what_the_editor_left_behind(self) -> None:
        self.invoke("add-memo")
        result = self.popup(
            "run-open-notebook",
            EDITOR="fake-editor",
            EDITOR_APPENDS="- 11:00 hand written",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], self.published("hand written"))

    def test_publishes_a_hand_written_notebook_without_timestamped_memos(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "reading the RFC"])
        result = self.popup("run-open-notebook", EDITOR="fake-editor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], self.published("reading the RFC"))

    def test_clears_the_pane_when_the_editor_leaves_no_memo_behind(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", ""])
        result = self.popup("run-open-notebook", EDITOR="fake-editor")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[-1], self.cleared())

    def test_reports_an_editor_that_failed_after_publishing_the_notebook(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.popup("run-open-notebook", EDITOR="fake-editor", EDITOR_EXIT=3)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(self.calls()[0], self.published("kept memo"))
        self.assertEqual(
            self.calls()[1],
            [
                "herdr",
                "notification",
                "show",
                "Pane notebook failed",
                "--body",
                "fake-editor exited with status 3.",
            ],
        )

    def test_notifies_when_the_configured_editor_is_missing(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", ""])
        result = self.popup("run-open-notebook", EDITOR="no-such-editor")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not run no-such-editor", result.stderr)

    def test_pages_the_notebook_without_editing_it(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.popup("run-show-notebook", PAGER="fake-pager")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- 09:00 kept memo", result.stdout)
        self.assertEqual(self.calls(), [])

    def test_holds_the_notebook_open_when_no_pager_is_available(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.popup("run-show-notebook", stdin="\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("- 09:00 kept memo", result.stdout)
        self.assertIn("Press Enter to close", result.stdout)

    def test_notifies_instead_of_paging_a_notebook_that_is_gone(self) -> None:
        result = self.popup("run-show-notebook")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"{PANE} has no notebook yet", result.stderr)
        self.assertEqual(self.calls()[-1][3], "Pane notebook failed")

    # Clearing.

    def test_archives_the_notebook_and_clears_the_pane_when_confirmed(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.popup("run-clear-notebook", stdin="y\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1 memo(s)", result.stdout)
        self.assertFalse(os.path.exists(self.notebook()))
        self.assertEqual(len(self.archived()), 1)
        self.assertTrue(self.archived()[0].endswith(f"-{NOTEBOOK_FILE}"))
        self.assertEqual(self.calls()[0], self.cleared())

    def test_keeps_the_notebook_when_clearing_is_not_confirmed(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        for answer in ("\n", "n\n", "", "nope\n"):
            with self.subTest(answer=answer):
                result = self.popup("run-clear-notebook", stdin=answer)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(os.path.exists(self.notebook()))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.archived(), [])

    # Lifecycle hooks.

    def test_archives_the_notebook_when_its_pane_closes(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.hook(
            "on-pane-closed",
            {"type": "pane_closed", "pane_id": PANE, "workspace_id": "w1"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.notebook()))
        self.assertEqual(len(self.archived()), 1)
        self.assertEqual(self.calls(), [])

    def test_ignores_a_closing_pane_that_never_had_a_notebook(self) -> None:
        result = self.hook("on-pane-closed", {"pane_id": "w9:p9"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.archived(), [])

    def test_reads_a_pane_out_of_an_event_envelope(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.hook(
            "on-pane-closed",
            {"event": "pane_closed", "data": {"pane_id": PANE, "workspace_id": "w1"}},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.archived()), 1)

    def test_follows_a_pane_that_moved_to_a_new_workspace(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 kept memo"])
        result = self.hook(
            "on-pane-moved",
            {
                "type": "pane_moved",
                "previous_pane_id": PANE,
                "pane": {"pane_id": "w2:p3"},
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(self.notebook()))
        self.assertEqual(self.lines("w2%3Ap3.md")[-1], "- 09:00 kept memo")
        self.assertEqual(self.calls(), [self.published("kept memo", pane="w2:p3")])

    def test_leaves_a_moved_pane_alone_when_it_has_no_notebook(self) -> None:
        result = self.hook(
            "on-pane-moved",
            {"previous_pane_id": PANE, "pane": {"pane_id": "w2:p3"}},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [])

    def test_fails_on_a_move_event_that_names_no_pane(self) -> None:
        result = self.hook("on-pane-moved", {"previous_pane_id": PANE})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("names no pane", result.stderr)
        # A hook failure belongs in the plugin log, not in a notification.
        self.assertEqual(self.calls(), [])

    # Restore.

    def test_republishes_live_panes_and_archives_the_rest_on_restore(self) -> None:
        self.write_notebook(["# a", "", f"## {TODAY}", "", "- 09:00 still here"])
        self.write_notebook(
            ["# b", "", f"## {TODAY}", "", "- 09:00 gone"], name="w1%3Ap2.md"
        )
        result = self.invoke("restore", LIVE_PANES=f"{PANE} w3:p7")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [["herdr", "pane", "list"], self.published("still here")],
        )
        self.assertTrue(os.path.exists(self.notebook()))
        self.assertEqual(len(self.archived()), 1)
        self.assertTrue(self.archived()[0].endswith("-w1%3Ap2.md"))

    def test_restores_nothing_when_no_pane_has_a_notebook(self) -> None:
        result = self.invoke("restore", LIVE_PANES=PANE)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["herdr", "pane", "list"]])

    def test_sweeps_every_notebook_on_restore_even_when_one_pane_fails(self) -> None:
        self.write_notebook(["# a", "", f"## {TODAY}", "", "- 09:00 first"])
        self.write_notebook(
            ["# b", "", f"## {TODAY}", "", "- 09:00 second"], name="w1%3Ap2.md"
        )
        result = self.invoke("restore", LIVE_PANES=f"{PANE} w1:p2", REPORT_EXIT=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(PANE, result.stderr)
        self.assertIn("w1:p2", result.stderr)
        self.assertEqual(
            self.calls()[1:],
            [self.published("first"), self.published("second", pane="w1:p2")],
        )

    def test_fails_restore_when_the_pane_list_cannot_be_read(self) -> None:
        result = self.invoke("restore", HERDR_EXIT=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not list panes", result.stderr)

    def test_keeps_the_archive_bounded(self) -> None:
        directory = os.path.join(self.state, "archive")
        os.makedirs(directory)
        for index in range(60):
            with open(
                os.path.join(directory, f"20000101-{index:06d}-old.md"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("old\n")
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 newest"])
        result = self.hook("on-pane-closed", {"pane_id": PANE})
        self.assertEqual(result.returncode, 0, result.stderr)
        archived = self.archived()
        self.assertEqual(len(archived), 50)
        self.assertTrue(archived[-1].endswith(f"-{NOTEBOOK_FILE}"))
        self.assertEqual(archived[0], "20000101-000011-old.md")

    # Usage.

    def test_rejects_an_unknown_command(self) -> None:
        for argv in ([], ["nonsense"], ["add-memo", "extra"]):
            with self.subTest(argv=argv):
                result = subprocess.run(
                    [sys.executable, PLUGIN, *argv],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("usage: herdr_notebook.py", result.stderr)


if __name__ == "__main__":
    unittest.main()
