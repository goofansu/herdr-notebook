# herdr-notebook

One Markdown notebook per Herdr workspace, opened over the active pane. Write a
line about what you are doing, press Enter, and it is timestamped into the
notebook and the overlay closes.

Notebooks are permanent. Closing the workspace does not remove one, and
reopening the same directory as a workspace brings the same notebook back.

## Install

```shell
herdr plugin install goofansu/herdr-notebook
```

## Use

One action, `open-notebook`. Bind it:

```toml
[[keys.command]]
key = "prefix+n"
type = "plugin_action"
command = "herdr-notebook.open-notebook"
description = "open this workspace's notebook"
```

Pressing it opens the notebook over the active pane:

```text
# herdr-notebook

Notebook for the Herdr workspace in `/Users/james/code/herdr-notebook`.
Started 2026-08-20 11:43.

## 2026-08-20

- 11:12 reading the plugin docs
- 11:43 waiting on CI

Memo (empty closes):
```

Type a line and press Enter to save it. Press Enter on an empty line to close
without writing anything.

The overlay is an ordinary Herdr pane, not a modal popup, so prefix keybindings
and pane navigation keep working while it is up. Herdr restores the previous
focus and zoom when it closes.

## Storage

One Markdown file per workspace directory, named after the directory's basename
plus a digest of its full path:

```shell
ls ~/.local/state/herdr/plugins/herdr-notebook/notebooks
```

The notebook is keyed to the directory rather than to the workspace id, because
Herdr hands out a fresh workspace id every time a workspace is opened. Keying on
the directory is what lets a notebook outlive its workspace.

Nothing in this plugin deletes a notebook. Edit or remove the files yourself when
you want to.

## Requirements

- Herdr 0.8.0 or newer, on Linux or macOS
- Python 3.9 or newer

## Development

```shell
herdr plugin link .
python3 -m unittest discover -s tests -t .
uvx ruff@0.16.2 check . && uvx ruff@0.16.2 format --check .
```
