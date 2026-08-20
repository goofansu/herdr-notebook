# herdr-notebook

Keeps a Markdown notebook beside a Herdr pane for as long as that pane lives, so
you can jot down what the pane is doing and read it back later. The newest memo
is shown on the pane itself; the notebook is archived when the pane closes.

## Install

```shell
herdr plugin install goofansu/herdr-notebook
```

## Actions

| Action | What it does |
| --- | --- |
| `add-memo` | Asks for one line in a popup and timestamps it into this pane's notebook |
| `open-notebook` | Opens this pane's notebook in `$EDITOR` for longer notes |
| `show-notebook` | Reads the notebook in a popup pager without editing it |
| `clear-notebook` | Archives this pane's notebook after confirmation and starts over |

Bind the ones you use:

```toml
[[keys.command]]
key = "prefix+ctrl+n"
type = "plugin_action"
command = "herdr-notebook.add-memo"
description = "add a memo to this pane"

[[keys.command]]
key = "prefix+alt+n"
type = "plugin_action"
command = "herdr-notebook.open-notebook"
description = "edit this pane's notebook"

[[keys.command]]
key = "prefix+alt+m"
type = "plugin_action"
command = "herdr-notebook.show-notebook"
description = "show this pane's notebook"
```

## Seeing the memo

Every saved memo is reported as display-only pane metadata: as the pane's title,
and as a `$notebook` sidebar token. Add the token to an Agent row to keep the
memo next to the pane it belongs to:

```toml
[ui.sidebar.agents]
rows = [
  ["state_icon", "workspace", "tab"],
  ["agent"],
  ["$notebook"],
]
```

Rows with no value disappear, so panes without a notebook look unchanged.

## Lifecycle

A notebook belongs to one pane and follows that pane's life:

- It is created the first time you add or edit a memo for the pane.
- It follows the pane when the pane moves to another tab or workspace, which
  gives the pane a new id.
- It is re-attached to its pane after a Herdr restart, which restores panes but
  not their metadata.
- It is archived when the pane closes, when its pane does not come back after a
  restart, or when you clear it.

Notebooks are one Markdown file per pane under the plugin's state directory,
with the 50 most recently archived notebooks kept beside them:

```shell
ls ~/.local/state/herdr/plugins/herdr-notebook/panes
ls ~/.local/state/herdr/plugins/herdr-notebook/archive
```

## Requirements

- Herdr 0.8.0 or newer, on Linux or macOS
- Python 3.9 or newer
- An `$EDITOR` for `open-notebook`, and a `$PAGER` or `less` for `show-notebook`

## Development

```shell
herdr plugin link .
python3 -m unittest discover -s tests -t .
uvx ruff@0.16.2 check . && uvx ruff@0.16.2 format --check .
```
