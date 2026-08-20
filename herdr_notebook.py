"""Keep a memo notebook beside a Herdr pane for as long as that pane lives."""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote

PLUGIN_ID = "herdr-notebook"
METADATA_SOURCE = "plugin:herdr-notebook"
MEMO_TOKEN = "notebook"
PANE_ENV = "HERDR_NOTEBOOK_PANE"
MEMO_PROMPT_ENTRYPOINT = "memo-prompt"
EDITOR_ENTRYPOINT = "notebook-editor"
VIEWER_ENTRYPOINT = "notebook-viewer"
CLEAR_ENTRYPOINT = "clear-confirm"
ARCHIVE_LIMIT = 50
NOTEBOOK_SUFFIX = ".md"
MEMO = re.compile(r"^- (\d{2}:\d{2}) +(\S.*)$")
DATE_HEADING = re.compile(r"^## \d{4}-\d{2}-\d{2}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
CONFIRMED = frozenset({"y", "yes"})
ACTION_COMMANDS = frozenset(
    {
        "add-memo",
        "open-notebook",
        "show-notebook",
        "clear-notebook",
    }
)
POPUP_COMMANDS = frozenset(
    {
        "run-add-memo",
        "run-open-notebook",
        "run-show-notebook",
        "run-clear-notebook",
    }
)
USAGE = (
    "usage: herdr_notebook.py "
    "(add-memo | open-notebook | show-notebook | clear-notebook | "
    "run-add-memo | run-open-notebook | run-show-notebook | run-clear-notebook | "
    "on-pane-closed | on-pane-moved | restore)"
)


class PluginError(Exception):
    pass


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def clean(value: str) -> str:
    """Reduce a typed line to what a memo and a sidebar token can hold.

    A popup receives every key the terminal sends, Escape included, so a line
    read from it can carry control bytes that would corrupt the notebook file
    and be rejected as metadata.
    """
    return " ".join(CONTROL.sub(" ", value).split())


def now() -> datetime.datetime:
    """Read the wall clock the memos are stamped against.

    A notebook is read next to the pane it describes, so its timestamps are
    deliberately local and naive rather than UTC.
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


def exit_status(returncode: int) -> int:
    """Report a signalled child the way a shell would, not as a negative code."""
    return 128 - returncode if returncode < 0 else returncode


def notify(title: str, body: str) -> None:
    run(
        [herdr_bin(), "notification", "show", title, "--body", body],
        capture_output=True,
        text=True,
    )


def json_env(name: str, label: str) -> dict:
    raw = os.environ.get(name)
    if not raw:
        raise PluginError(f"{name} is not set")
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise PluginError(f"could not read the {label}: {error}") from error
    if not isinstance(payload, dict):
        raise PluginError(f"the {label} is not an object")
    return payload


def action_target() -> tuple[str, str, str | None]:
    """Read the pane an action was invoked on out of Herdr's context."""
    context = json_env("HERDR_PLUGIN_CONTEXT_JSON", "plugin context")
    pane_id = text(context.get("focused_pane_id")) or text(
        os.environ.get("HERDR_PANE_ID")
    )
    if not pane_id:
        raise PluginError("there is no focused pane to keep a notebook for")
    cwd = (
        text(context.get("focused_pane_cwd"))
        or text(context.get("workspace_cwd"))
        or os.path.expanduser("~")
    )
    return pane_id, cwd, text(context.get("workspace_label"))


def popup_target() -> str:
    """Read the pane a popup was opened for.

    A popup has no pane id of its own and never exports HERDR_PANE_ID, so the
    action that opened it passes the pane through the popup's environment.
    """
    pane_id = text(os.environ.get(PANE_ENV))
    if not pane_id:
        raise PluginError(f"{PANE_ENV} is not set")
    return pane_id


def event_pane_ids() -> tuple[str | None, str | None]:
    """Return the pane an event is about, and the id it moved away from.

    Herdr may hand a hook the event envelope or its data alone, and a pane
    arrives either as a bare `pane_id` or inside a `pane` snapshot.
    """
    payload = json_env("HERDR_PLUGIN_EVENT_JSON", "plugin event")
    data = payload.get("data")
    if isinstance(data, dict):
        payload = data
    pane = payload.get("pane")
    pane_id = text(payload.get("pane_id"))
    if not pane_id and isinstance(pane, dict):
        pane_id = text(pane.get("pane_id"))
    return pane_id, text(payload.get("previous_pane_id"))


def state_dir() -> str:
    directory = text(os.environ.get("HERDR_PLUGIN_STATE_DIR"))
    if not directory:
        raise PluginError("HERDR_PLUGIN_STATE_DIR is not set")
    return directory


def notebook_dir() -> str:
    return os.path.join(state_dir(), "panes")


def archive_dir() -> str:
    return os.path.join(state_dir(), "archive")


def file_key(pane_id: str) -> str:
    """Encode a pane id as one reversible filename component.

    Public pane ids are workspace-qualified, as in `w1:p1`, and percent
    encoding keeps the colon out of the filename while letting `restore` read
    the pane id back off disk.
    """
    return quote(pane_id, safe="")


def notebook_path(pane_id: str) -> str:
    return os.path.join(notebook_dir(), file_key(pane_id) + NOTEBOOK_SUFFIX)


def stored_notebooks() -> list[tuple[str, str]]:
    directory = notebook_dir()
    if not os.path.isdir(directory):
        return []
    found = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(NOTEBOOK_SUFFIX):
            continue
        pane_id = unquote(name[: -len(NOTEBOOK_SUFFIX)])
        found.append((pane_id, os.path.join(directory, name)))
    return found


def read_lines(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return handle.read().splitlines()


def write_lines(path: str, lines: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def ensure_notebook(
    pane_id: str, cwd: str, workspace: str | None, moment: datetime.datetime
) -> str:
    """Create this pane's notebook with a header the pane can be traced from."""
    path = notebook_path(pane_id)
    if os.path.exists(path):
        return path
    heading = f"# {pane_id}"
    if workspace:
        heading += f" — {workspace}"
    write_lines(
        path,
        [
            heading,
            "",
            f"Notebook for the Herdr pane working in `{cwd}`.",
            (
                f"Opened {moment.strftime('%Y-%m-%d %H:%M')}. "
                "Herdr archives this file when the pane closes."
            ),
            "",
            f"## {moment.strftime('%Y-%m-%d')}",
            "",
        ],
    )
    return path


def require_notebook(pane_id: str) -> str:
    path = notebook_path(pane_id)
    if not os.path.exists(path):
        raise PluginError(f"{pane_id} has no notebook yet")
    return path


def memo_body(lines: list[str]) -> list[str]:
    """Skip the header, so its prose can never be mistaken for a memo."""
    for index, line in enumerate(lines):
        if DATE_HEADING.match(line):
            return lines[index + 1 :]
    return []


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


def latest_memo(path: str) -> str | None:
    """Read the memo that describes what the pane is doing now.

    Timestamped memos win. A hand-edited notebook may have none, so fall back
    to its last written line rather than showing the pane nothing.
    """
    body = memo_body(read_lines(path))
    for line in reversed(body):
        match = MEMO.match(line)
        if match:
            return clean(match.group(2))
    for line in reversed(body):
        candidate = clean(line.lstrip("-*# ").strip())
        if candidate:
            return candidate
    return None


def count_memos(path: str) -> int:
    return sum(1 for line in memo_body(read_lines(path)) if MEMO.match(line))


def publish(pane_id: str, memo: str | None) -> None:
    """Show the memo on the pane, or take a stale one down.

    The value lands as the pane's metadata title and as a `$notebook` sidebar
    token; both are display-only and expire with the pane.
    """
    argv = [
        herdr_bin(),
        "pane",
        "report-metadata",
        pane_id,
        "--source",
        METADATA_SOURCE,
    ]
    if memo:
        argv += ["--title", memo, "--token", f"{MEMO_TOKEN}={memo}"]
    else:
        argv += ["--clear-title", "--clear-token", MEMO_TOKEN]
    result = run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise PluginError(f"could not show the memo on {pane_id}: {diagnostic(result)}")


def open_popup(entrypoint: str, pane_id: str, cwd: str) -> int:
    """Open a popup that works on one pane's notebook.

    A popup is session-modal and leaves the tiled layout alone, so the pane the
    memo is about keeps its place and its focus.
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
            entrypoint,
            "--cwd",
            cwd,
            "--env",
            f"{PANE_ENV}={pane_id}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PluginError(f"could not open the notebook popup: {diagnostic(result)}")
    return 0


def archive_notebook(pane_id: str, moment: datetime.datetime) -> str | None:
    """Retire a notebook whose pane is gone, without discarding what it holds."""
    path = notebook_path(pane_id)
    if not os.path.exists(path):
        return None
    directory = archive_dir()
    os.makedirs(directory, exist_ok=True)
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    target = os.path.join(directory, f"{stamp}-{file_key(pane_id)}{NOTEBOOK_SUFFIX}")
    os.replace(path, target)
    trim_archive()
    return target


def trim_archive() -> None:
    directory = archive_dir()
    if not os.path.isdir(directory):
        return
    # Every archived name starts with its timestamp, so name order is age order.
    archived = sorted(
        name for name in os.listdir(directory) if name.endswith(NOTEBOOK_SUFFIX)
    )
    for name in archived[: max(0, len(archived) - ARCHIVE_LIMIT)]:
        os.remove(os.path.join(directory, name))


def follow_pane(previous_pane_id: str, pane_id: str, moment: datetime.datetime) -> None:
    """Carry a notebook to the pane id a move gave it.

    A pane moved into another workspace is handed a new workspace-qualified id,
    which would otherwise orphan the notebook filed under the old one.
    """
    source = notebook_path(previous_pane_id)
    if previous_pane_id == pane_id or not os.path.exists(source):
        return
    archive_notebook(pane_id, moment)
    os.replace(source, notebook_path(pane_id))


def live_pane_ids() -> set:
    result = run(
        [herdr_bin(), "pane", "list"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PluginError(f"could not list panes: {diagnostic(result)}")
    try:
        panes = json.loads(result.stdout)["result"]["panes"]
    except (ValueError, KeyError, TypeError) as error:
        raise PluginError(f"could not read the pane list: {error}") from error
    return {text(pane.get("pane_id")) for pane in panes} - {None}


def editor_command() -> list[str]:
    for name in ("VISUAL", "EDITOR"):
        configured = text(os.environ.get(name))
        if configured:
            return shlex.split(configured)
    return ["vi"]


def pager_command() -> list[str] | None:
    configured = text(os.environ.get("PAGER"))
    if configured:
        return shlex.split(configured)
    if shutil.which("less"):
        return ["less"]
    return None


def add_memo() -> int:
    pane_id, cwd, workspace = action_target()
    ensure_notebook(pane_id, cwd, workspace, now())
    return open_popup(MEMO_PROMPT_ENTRYPOINT, pane_id, cwd)


def open_notebook() -> int:
    pane_id, cwd, workspace = action_target()
    ensure_notebook(pane_id, cwd, workspace, now())
    return open_popup(EDITOR_ENTRYPOINT, pane_id, cwd)


def show_notebook() -> int:
    pane_id, cwd, _ = action_target()
    require_notebook(pane_id)
    return open_popup(VIEWER_ENTRYPOINT, pane_id, cwd)


def clear_notebook() -> int:
    pane_id, cwd, _ = action_target()
    require_notebook(pane_id)
    return open_popup(CLEAR_ENTRYPOINT, pane_id, cwd)


def ask(prompt: str) -> str | None:
    """Read one line from the popup, or None when the reader backed out."""
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def run_add_memo() -> int:
    pane_id = popup_target()
    # The popup runs in the pane's own directory, which is the header this
    # notebook would want if the file went missing between action and popup.
    path = ensure_notebook(pane_id, os.getcwd(), None, now())
    print(f"Notebook — {pane_id}")
    latest = latest_memo(path)
    if latest:
        print(f"Latest — {latest}")
    print()
    entered = ask("Memo (empty cancels): ")
    if entered is None:
        return 0
    memo = clean(entered)
    if not memo:
        return 0
    moment = now()
    append_memo(path, memo, moment)
    publish(pane_id, memo)
    notify("Memo added", f"{pane_id} — {memo}")
    return 0


def run_open_notebook() -> int:
    pane_id = popup_target()
    path = ensure_notebook(pane_id, os.getcwd(), None, now())
    command = editor_command()
    status = exit_status(run([*command, path]).returncode)
    # Publish whatever the editor left behind, including nothing at all, so a
    # deleted memo stops describing the pane.
    publish(pane_id, latest_memo(path))
    if 0 < status < 128:
        notify("Pane notebook failed", f"{command[0]} exited with status {status}.")
    return status


def run_show_notebook() -> int:
    pane_id = popup_target()
    path = require_notebook(pane_id)
    pager = pager_command()
    if pager:
        return exit_status(run([*pager, path]).returncode)
    # Without a pager the popup would close before anything could be read.
    with open(path, encoding="utf-8") as handle:
        sys.stdout.write(handle.read())
    ask("Press Enter to close: ")
    return 0


def run_clear_notebook() -> int:
    pane_id = popup_target()
    path = require_notebook(pane_id)
    memos = count_memos(path)
    print(f"Notebook — {pane_id}")
    print(f"{memos} memo(s) in {path}")
    print()
    answer = ask("Archive this notebook and start over? [y/N] ")
    if answer is None or clean(answer).lower() not in CONFIRMED:
        return 0
    target = archive_notebook(pane_id, now())
    publish(pane_id, None)
    notify("Notebook cleared", f"{pane_id} — archived to {target}")
    return 0


def on_pane_closed() -> int:
    pane_id, _ = event_pane_ids()
    if not pane_id:
        raise PluginError("the pane closed event names no pane")
    archive_notebook(pane_id, now())
    return 0


def on_pane_moved() -> int:
    pane_id, previous_pane_id = event_pane_ids()
    if not pane_id or not previous_pane_id:
        raise PluginError("the pane moved event names no pane")
    moment = now()
    follow_pane(previous_pane_id, pane_id, moment)
    path = notebook_path(pane_id)
    if os.path.exists(path):
        publish(pane_id, latest_memo(path))
    return 0


def restore() -> int:
    """Re-attach notebooks to the panes Herdr just restored.

    Metadata tokens do not survive a server restart, and panes that did not
    come back have finished their lifecycle, so their notebooks are archived.
    """
    live = live_pane_ids()
    moment = now()
    # One pane that cannot be reached must not cost every other pane its
    # notebook, so failures are collected and reported after the sweep.
    failures = []
    for pane_id, path in stored_notebooks():
        try:
            if pane_id in live:
                publish(pane_id, latest_memo(path))
            else:
                archive_notebook(pane_id, moment)
        except PluginError as error:
            failures.append(str(error))
    if failures:
        raise PluginError("; ".join(failures))
    return 0


COMMANDS = {
    "add-memo": add_memo,
    "open-notebook": open_notebook,
    "show-notebook": show_notebook,
    "clear-notebook": clear_notebook,
    "run-add-memo": run_add_memo,
    "run-open-notebook": run_open_notebook,
    "run-show-notebook": run_show_notebook,
    "run-clear-notebook": run_clear_notebook,
    "on-pane-closed": on_pane_closed,
    "on-pane-moved": on_pane_moved,
    "restore": restore,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        return COMMANDS[argv[0]]()
    except PluginError as error:
        # A user-triggered failure needs a notification: an action runs headless
        # and a popup takes its own output down with it when it closes. Hook and
        # startup failures stay in `herdr plugin log list`, where they belong.
        if argv[0] in POPUP_COMMANDS:
            notify("Pane notebook failed", f"{error}.")
        elif argv[0] in ACTION_COMMANDS:
            notify("Pane notebook unavailable", f"{error}.")
        print(f"{PLUGIN_ID}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
