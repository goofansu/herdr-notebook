"""Keep one Markdown notebook per Herdr workspace, and never throw it away."""

from __future__ import annotations

import datetime
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
DATE_HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
UNSLUGGABLE = re.compile(r"[^a-z0-9]+")
USAGE = "usage: herdr_notebook.py (open-notebook | run-open-notebook)"


class PluginError(Exception):
    pass


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def clean(value: str) -> str:
    """Reduce a typed line to what a memo can hold.

    A terminal sends every keypress the reader makes, Escape included, so a
    line read from the overlay can carry control bytes that would corrupt the
    notebook file.
    """
    return " ".join(CONTROL.sub(" ", value).split())


def now() -> datetime.datetime:
    """Read the wall clock the memos are stamped against.

    A notebook is read next to the workspace it describes, so its timestamps
    are deliberately local and naive rather than UTC.
    """
    return datetime.datetime.now()  # noqa: DTZ005


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


def workspace_target() -> tuple[str, str | None]:
    """Read the workspace an action was invoked in out of Herdr's context."""
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
    return os.path.normpath(cwd), text(context.get("workspace_label"))


def state_dir() -> str:
    directory = text(os.environ.get("HERDR_PLUGIN_STATE_DIR"))
    if not directory:
        raise PluginError("HERDR_PLUGIN_STATE_DIR is not set")
    return directory


def notebook_path(cwd: str) -> str:
    """Name a notebook after the directory its workspace works in.

    A workspace id is handed out fresh every time a workspace is opened, so
    keying on it would strand the notebook the moment the workspace closes.
    The directory is what a workspace comes back as, and the digest keeps two
    checkouts that share a basename apart.
    """
    slug = UNSLUGGABLE.sub("-", os.path.basename(cwd).lower()).strip("-")
    digest = hashlib.sha256(cwd.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
    name = f"{slug[:SLUG_LENGTH]}-{digest}" if slug else digest
    return os.path.join(state_dir(), "notebooks", name + NOTEBOOK_SUFFIX)


def read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def write_lines(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def ensure_notebook(path: str, cwd: str, label: str | None, moment: datetime.datetime):
    """Create the workspace's notebook the first time it is opened."""
    if os.path.exists(path):
        return path
    write_lines(
        path,
        [
            f"# {label or os.path.basename(cwd) or cwd}",
            "",
            f"Notebook for the Herdr workspace in `{cwd}`.",
            f"Started {moment.strftime('%Y-%m-%d %H:%M')}.",
            "",
            f"## {moment.strftime('%Y-%m-%d')}",
            "",
        ],
    )
    return path


def append_memo(path: str, memo: str, moment: datetime.datetime) -> None:
    lines = read_lines(path)
    while lines and not lines[-1].strip():
        lines.pop()
    heading = f"## {moment.strftime('%Y-%m-%d')}"
    dates = [line for line in lines if DATE_HEADING.match(line)]
    if not dates or dates[-1] != heading:
        lines += ["", heading]
    if DATE_HEADING.match(lines[-1]):
        lines.append("")
    lines.append(f"- {moment.strftime('%H:%M')} {memo}")
    write_lines(path, lines)


def open_overlay(path: str, cwd: str) -> int:
    """Open the notebook over the active pane.

    An overlay is an ordinary Herdr pane, so keybindings and pane navigation
    keep working while it is up, and Herdr restores the previous focus and zoom
    when the process behind it exits.
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
    cwd, label = workspace_target()
    path = notebook_path(cwd)
    ensure_notebook(path, cwd, label, now())
    return open_overlay(path, cwd)


def run_open_notebook() -> int:
    """Show the notebook in the overlay and take one memo before closing."""
    path = text(os.environ.get(PATH_ENV))
    if not path:
        raise PluginError(f"{PATH_ENV} is not set")
    sys.stdout.write("\n".join(read_lines(path)) + "\n\n")
    try:
        entered = input("Memo (empty closes): ")
    except (EOFError, KeyboardInterrupt):
        print()
        return 0
    memo = clean(entered)
    if not memo:
        return 0
    append_memo(path, memo, now())
    return 0


COMMANDS = {
    "open-notebook": open_notebook,
    "run-open-notebook": run_open_notebook,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return COMMANDS[argv[0]]()
    except PluginError as error:
        # An action runs headless and an overlay takes its own output down when
        # it closes, so a notification is the only report a reader would see.
        notify("Notebook", f"{error}.")
        print(f"{PLUGIN_ID}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
