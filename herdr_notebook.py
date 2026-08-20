"""Open a workspace's plain Markdown notebook over the active pane."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys

PLUGIN_ID = "herdr-notebook"
NOTEBOOK_ENTRYPOINT = "notebook"
PATH_ENV = "HERDR_NOTEBOOK_PATH"
NOTEBOOK_SUFFIX = ".md"
DIGEST_LENGTH = 12
SLUG_LENGTH = 40
UNSLUGGABLE = re.compile(r"[^a-z0-9]+")
USAGE = "usage: herdr_notebook.py open-notebook"


class PluginError(Exception):
    pass


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, check=False, **kwargs)
    except OSError as error:
        raise PluginError(f"could not run {argv[0]}: {error}") from error


def diagnostic(result: subprocess.CompletedProcess) -> str:
    for output in (result.stderr, result.stdout):
        if isinstance(output, str) and output.strip():
            return output.strip().splitlines()[0]
    return f"exit status {result.returncode}"


def notify(title: str, body: str) -> None:
    run(
        [herdr_bin(), "notification", "show", title, "--body", body],
        capture_output=True,
        text=True,
    )


def workspace_cwd() -> str:
    """Read the directory of the workspace this action was invoked in."""
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if not raw:
        raise PluginError("HERDR_PLUGIN_CONTEXT_JSON is not set")
    try:
        context = json.loads(raw)
    except ValueError as error:
        raise PluginError(f"could not read the plugin context: {error}") from error
    if not isinstance(context, dict):
        raise PluginError("the plugin context is not an object")
    cwd = text(context.get("workspace_cwd")) or text(context.get("focused_pane_cwd"))
    if not cwd:
        raise PluginError("there is no workspace to keep a notebook for")
    return canonical_directory(cwd)


def stored_spelling(parent: str, name: str) -> str:
    """Return a name as its parent directory stores it, case included."""
    try:
        entries = os.listdir(parent)
    except OSError:
        return name
    if name in entries:
        return name
    # Only a case-insensitive filesystem can reach here, and only one entry can
    # match once case is ignored. A case-sensitive one already matched exactly.
    folded = [entry for entry in entries if entry.lower() == name.lower()]
    return folded[0] if len(folded) == 1 else name


def canonical_directory(cwd: str) -> str:
    """Reduce a directory to the one spelling its notebook is filed under.

    Symlinks and letter case both let a single directory wear more than one
    name, and every spare name would open its own empty notebook. realpath
    settles the symlinks, and walking the components settles the case by
    preferring what the parent directory stores over what was asked for.
    Anything that cannot be listed keeps the spelling it arrived with.
    """
    resolved = os.path.realpath(os.path.normpath(cwd))
    canonical = os.sep
    for part in resolved.strip(os.sep).split(os.sep):
        canonical = os.path.join(canonical, stored_spelling(canonical, part))
    return canonical


def state_dir() -> str:
    directory = text(os.environ.get("HERDR_PLUGIN_STATE_DIR"))
    if not directory:
        raise PluginError("HERDR_PLUGIN_STATE_DIR is not set")
    return directory


def notebook_path(cwd: str) -> str:
    """Name a notebook after the directory its workspace works in.

    A workspace id is handed out fresh every time a workspace is opened, so
    keying on it would strand the notebook the moment the workspace closed.
    The directory is what a workspace comes back as, and the digest keeps two
    checkouts that share a basename apart. The caller resolves the directory
    to one canonical spelling first, so the digest is stable.
    """
    slug = UNSLUGGABLE.sub("-", os.path.basename(cwd).lower()).strip("-")
    digest = hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
    name = f"{slug[:SLUG_LENGTH]}-{digest}" if slug else digest
    return os.path.join(state_dir(), "notebooks", name + NOTEBOOK_SUFFIX)


def open_overlay(path: str, cwd: str) -> int:
    """Open the notebook over the active pane.

    An overlay is a temporary zoomed pane rather than a modal popup, so
    keybindings and pane navigation keep working while the editor is up, and
    Herdr restores the previous focus and zoom once it exits.
    """
    result = run(
        [
            herdr_bin(),
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN_ID,
            "--entrypoint",
            NOTEBOOK_ENTRYPOINT,
            "--placement",
            "overlay",
            "--cwd",
            cwd,
            "--env",
            f"{PATH_ENV}={path}",
            "--focus",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PluginError(f"could not open the notebook: {diagnostic(result)}")
    return 0


def open_notebook() -> int:
    """Point the editor at this workspace's notebook.

    Only the directory is prepared. The file itself is the editor's to create,
    so quitting without saving leaves nothing behind and the plugin never
    writes a line the reader did not type.
    """
    cwd = workspace_cwd()
    path = notebook_path(cwd)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except OSError as error:
        # The only filesystem call in the plugin, and the only one that can
        # fail on a full or read-only state volume. It has to arrive as a
        # PluginError or the reader gets no overlay and no notification.
        raise PluginError(
            f"could not prepare the notebook directory: {error}"
        ) from error
    return open_overlay(path, cwd)


def main(argv: list[str]) -> int:
    if argv != ["open-notebook"]:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return open_notebook()
    except PluginError as error:
        # The action runs headless, so a notification is the only report a
        # reader would ever see.
        notify("Notebook", f"{error}.")
        print(f"{PLUGIN_ID}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
