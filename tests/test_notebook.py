from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "herdr_notebook.py")
MANIFEST = os.path.join(ROOT, "herdr-plugin.toml")
CWD = "/work/Some Repo"
LABEL = "some repo"
# The plugin stamps memos against the local wall clock; so does this.
TODAY = datetime.date.today().isoformat()  # noqa: DTZ011

HERDR = r"""#!{python}
import json
import os
import sys

with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(["herdr", *sys.argv[1:]]) + "\n")

sys.exit(int(os.environ.get("HERDR_EXIT", "0")))
"""


def notebook_name(cwd: str) -> str:
    digest = hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:12]
    return f"some-repo-{digest}.md"


class NotebookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.bin)
        os.makedirs(self.state)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        path = os.path.join(self.bin, "herdr")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(HERDR.format(python=sys.executable))
        os.chmod(path, 0o755)

    # Invocation helpers.

    def invoke(self, command: str, stdin: str = "", **extra):
        env = {
            "PATH": self.bin,
            "CALL_LOG": self.log,
            "HERDR_PLUGIN_STATE_DIR": self.state,
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(
                {"workspace_cwd": CWD, "workspace_label": LABEL}
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

    def overlay(self, stdin: str = "", path: str | None = None, **extra):
        return self.invoke(
            "run-open-notebook",
            stdin=stdin,
            HERDR_NOTEBOOK_PATH=path or self.notebook(),
            **extra,
        )

    # Assertion helpers.

    def calls(self) -> list[list[str]]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def pane_open(self, path: str) -> list[str]:
        return [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "herdr-notebook",
            "--entrypoint",
            "notebook",
            "--placement",
            "overlay",
            "--cwd",
            CWD,
            "--env",
            f"HERDR_NOTEBOOK_PATH={path}",
            "--focus",
        ]

    # Notebook file helpers.

    def notebook(self, cwd: str = CWD) -> str:
        return os.path.join(self.state, "notebooks", notebook_name(cwd))

    def write_notebook(self, body: list[str], cwd: str = CWD) -> str:
        path = self.notebook(cwd)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(body) + "\n")
        return path

    def lines(self, cwd: str = CWD) -> list[str]:
        with open(self.notebook(cwd), encoding="utf-8") as handle:
            return handle.read().splitlines()

    # Opening the notebook.

    def test_opens_an_overlay_over_a_notebook_it_created(self) -> None:
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.pane_open(self.notebook())])
        lines = self.lines()
        self.assertEqual(lines[0], f"# {LABEL}")
        self.assertIn(CWD, lines[2])
        self.assertEqual(lines[-2], f"## {TODAY}")

    def test_keeps_an_existing_notebook_when_the_workspace_opens_it_again(self) -> None:
        self.write_notebook(["# kept", "", f"## {TODAY}", "", "- 09:00 earlier"])
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines()[0], "# kept")
        self.assertEqual(self.lines()[-1], "- 09:00 earlier")

    def test_names_the_notebook_after_the_workspace_directory(self) -> None:
        self.invoke("open-notebook")
        self.assertTrue(os.path.exists(self.notebook()))
        # A second workspace whose directory shares a basename gets its own.
        other = "/elsewhere/Some Repo"
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": other}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(self.notebook(other)))
        self.assertNotEqual(notebook_name(CWD), notebook_name(other))
        self.assertEqual(
            len(os.listdir(os.path.join(self.state, "notebooks"))),
            2,
        )

    def test_reaches_the_same_notebook_from_an_unnormalized_directory(self) -> None:
        self.invoke("open-notebook")
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": CWD + "/"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(os.listdir(os.path.join(self.state, "notebooks"))), 1)

    def test_falls_back_to_the_focused_pane_directory(self) -> None:
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"focused_pane_cwd": CWD}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines()[0], "# Some Repo")

    def test_notifies_instead_of_opening_without_a_workspace_directory(self) -> None:
        result = self.invoke("open-notebook", HERDR_PLUGIN_CONTEXT_JSON="{}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no workspace to keep a notebook for", result.stderr)
        self.assertEqual(
            self.calls(),
            [
                [
                    "herdr",
                    "notification",
                    "show",
                    "Notebook",
                    "--body",
                    "there is no workspace to keep a notebook for.",
                ]
            ],
        )

    def test_reports_an_overlay_herdr_refused_to_open(self) -> None:
        result = self.invoke("open-notebook", HERDR_EXIT=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not open the notebook", result.stderr)
        self.assertEqual(self.calls()[-1][3], "Notebook")

    # Writing in the overlay.

    def test_shows_the_notebook_and_appends_one_timestamped_memo(self) -> None:
        self.invoke("open-notebook")
        result = self.overlay(stdin="waiting on CI\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"## {TODAY}", result.stdout)
        self.assertIn("Memo (empty closes):", result.stdout)
        self.assertRegex(self.lines()[-1], r"^- \d{2}:\d{2} waiting on CI$")

    def test_leaves_a_blank_line_under_each_date_heading(self) -> None:
        self.invoke("open-notebook")
        self.overlay(stdin="first\n")
        lines = self.lines()
        self.assertEqual(lines[-3], f"## {TODAY}")
        self.assertEqual(lines[-2], "")
        self.assertEqual(lines[-1][8:], "first")

    def test_keeps_same_day_memos_under_one_date_heading(self) -> None:
        self.invoke("open-notebook")
        self.overlay(stdin="first\n")
        self.overlay(stdin="second\n")
        lines = self.lines()
        self.assertEqual(lines.count(f"## {TODAY}"), 1)
        self.assertEqual([line[8:] for line in lines[-2:]], ["first", "second"])

    def test_starts_a_new_date_heading_for_a_notebook_left_from_another_day(
        self,
    ) -> None:
        self.write_notebook(["# kept", "", "## 2000-01-01", "", "- 09:00 long ago"])
        self.overlay(stdin="today\n")
        lines = self.lines()
        self.assertEqual(lines[2], "## 2000-01-01")
        self.assertEqual(lines[4], "- 09:00 long ago")
        self.assertEqual(lines[6], f"## {TODAY}")
        self.assertEqual(lines[-1][8:], "today")

    def test_closes_without_writing_when_the_memo_is_empty(self) -> None:
        self.invoke("open-notebook")
        before = self.lines()
        for entry in ("\n", "   \n", ""):
            with self.subTest(entry=entry):
                result = self.overlay(stdin=entry)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.lines(), before)

    def test_drops_control_characters_a_keypress_can_send(self) -> None:
        self.invoke("open-notebook")
        result = self.overlay(stdin="\x1brebasing\tonto main\x07\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.lines()[-1][8:], "rebasing onto main")

    def test_fails_without_a_path_when_the_overlay_is_wired_wrong(self) -> None:
        result = self.invoke("run-open-notebook", stdin="anything\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HERDR_NOTEBOOK_PATH is not set", result.stderr)
        self.assertEqual(self.calls()[-1][3], "Notebook")

    # The manifest is the other half of the contract.

    def test_the_manifest_declares_one_overlay_and_no_lifecycle_hooks(self) -> None:
        with open(MANIFEST, encoding="utf-8") as handle:
            manifest = handle.read()
        self.assertEqual(manifest.count("[[actions]]"), 1)
        self.assertEqual(manifest.count("[[panes]]"), 1)
        self.assertIn('placement = "overlay"', manifest)
        # A notebook outlives its workspace, so nothing may retire one.
        self.assertNotIn("[[events]]", manifest)
        self.assertNotIn("[[startup]]", manifest)

    def test_rejects_an_unknown_command(self) -> None:
        for argv in ([], ["nonsense"], ["open-notebook", "extra"]):
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
