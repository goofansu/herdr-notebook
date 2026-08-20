from __future__ import annotations

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

HERDR = r"""#!{python}
import json
import os
import sys

with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(["herdr", *sys.argv[1:]]) + "\n")

sys.exit(int(os.environ.get("HERDR_EXIT", "0")))
"""


def notebook_name(cwd: str, slug: str = "some-repo") -> str:
    digest = hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}.md"


class NotebookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        # The plugin resolves symlinks, and on macOS /var is one.
        self.root = os.path.realpath(self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.bin)
        os.makedirs(self.state)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        path = os.path.join(self.bin, "herdr")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(HERDR.format(python=sys.executable))
        os.chmod(path, 0o755)

    def invoke(self, *argv: str, **extra):
        env = {
            "PATH": self.bin,
            "CALL_LOG": self.log,
            "HERDR_PLUGIN_STATE_DIR": self.state,
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps({"workspace_cwd": CWD}),
        }
        env.update({key: str(value) for key, value in extra.items()})
        return subprocess.run(
            [sys.executable, PLUGIN, *argv],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def pane_open(self, path: str, cwd: str = CWD) -> list[str]:
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
            cwd,
            "--env",
            f"HERDR_NOTEBOOK_PATH={path}",
            "--focus",
        ]

    def notebook(self, cwd: str = CWD, slug: str = "some-repo") -> str:
        return os.path.join(self.state, "notebooks", notebook_name(cwd, slug))

    def notebooks(self) -> list[str]:
        directory = os.path.join(self.state, "notebooks")
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    def test_opens_an_overlay_on_this_workspaces_notebook(self) -> None:
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.pane_open(self.notebook())])

    def test_prepares_the_directory_but_leaves_the_file_to_the_editor(self) -> None:
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.isdir(os.path.join(self.state, "notebooks")))
        # Quitting the editor without saving must leave nothing behind.
        self.assertEqual(self.notebooks(), [])

    def test_never_touches_an_existing_notebook(self) -> None:
        path = self.notebook()
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("whatever I wrote\n")
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "whatever I wrote\n")

    def test_names_the_notebook_after_the_workspace_directory(self) -> None:
        other = "/elsewhere/Some Repo"
        self.invoke("open-notebook")
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": other}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        # Two checkouts sharing a basename still get separate notebooks.
        self.assertNotEqual(notebook_name(CWD), notebook_name(other))
        self.assertEqual(
            [call[-2] for call in self.calls()],
            [
                f"HERDR_NOTEBOOK_PATH={self.notebook()}",
                f"HERDR_NOTEBOOK_PATH={self.notebook(other)}",
            ],
        )

    def test_reaches_the_same_notebook_from_an_unnormalized_directory(self) -> None:
        self.invoke("open-notebook")
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": CWD + "/"}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0], self.calls()[1])

    def test_reaches_one_notebook_through_a_symlinked_directory(self) -> None:
        real = os.path.join(self.root, "real")
        link = os.path.join(self.root, "link")
        os.mkdir(real)
        os.symlink(real, link)
        for cwd in (real, link):
            with self.subTest(cwd=cwd):
                result = self.invoke(
                    "open-notebook",
                    HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": cwd}),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0], self.calls()[1])

    def test_reaches_one_notebook_through_a_differently_cased_directory(self) -> None:
        real = os.path.join(self.root, "Real")
        os.mkdir(real)
        if not os.path.exists(os.path.join(self.root, "real")):
            self.skipTest("filesystem is case-sensitive")
        for cwd in (real, os.path.join(self.root, "real")):
            with self.subTest(cwd=cwd):
                result = self.invoke(
                    "open-notebook",
                    HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": cwd}),
                )
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0], self.calls()[1])
        # The stored spelling wins, not the one that was asked for.
        self.assertIn("--cwd", self.calls()[0])
        self.assertEqual(self.calls()[0][self.calls()[0].index("--cwd") + 1], real)

    def test_keeps_the_spelling_of_a_directory_that_is_not_there(self) -> None:
        gone = os.path.join(self.root, "Never", "Existed")
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"workspace_cwd": gone}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[0][self.calls()[0].index("--cwd") + 1], gone)

    def test_falls_back_to_the_focused_pane_directory(self) -> None:
        result = self.invoke(
            "open-notebook",
            HERDR_PLUGIN_CONTEXT_JSON=json.dumps({"focused_pane_cwd": CWD}),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [self.pane_open(self.notebook())])

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

    def test_fails_without_a_state_directory(self) -> None:
        result = self.invoke("open-notebook", HERDR_PLUGIN_STATE_DIR="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("HERDR_PLUGIN_STATE_DIR is not set", result.stderr)

    def test_notifies_instead_of_crashing_when_the_directory_cannot_be_made(
        self,
    ) -> None:
        blocked = os.path.join(self.state, "notebooks")
        with open(blocked, "w", encoding="utf-8") as handle:
            handle.write("in the way\n")
        result = self.invoke("open-notebook")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("could not prepare the notebook directory", result.stderr)
        self.assertEqual(self.calls()[-1][3], "Notebook")

    def test_the_manifest_hands_the_file_to_the_editor_and_keeps_no_hooks(self) -> None:
        with open(MANIFEST, encoding="utf-8") as handle:
            manifest = handle.read()
        self.assertEqual(manifest.count("[[actions]]"), 1)
        self.assertEqual(manifest.count("[[panes]]"), 1)
        self.assertIn('placement = "overlay"', manifest)
        self.assertIn('exec ${VISUAL:-${EDITOR:-vi}} "$HERDR_NOTEBOOK_PATH"', manifest)
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
