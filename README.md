# herdr-notebook

One plain Markdown notebook per Herdr workspace, opened in `$EDITOR` over the
active pane. The plugin decides only where the file lives; everything inside it
is yours to write.

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

Pressing it opens this workspace's notebook in `$EDITOR`, or `$VISUAL` when that
is set, falling back to `vi`. Write whatever you want, then save and quit the way
you normally would; the overlay closes with the editor.

The overlay is a temporary zoomed Herdr pane, not a modal popup, so prefix
keybindings and pane navigation keep working while the editor is up. Herdr
restores the previous focus and zoom when it closes.

## Storage

One Markdown file per workspace directory, named after the directory's basename
plus a digest of its full path:

```shell
ls ~/.local/state/herdr/plugins/herdr-notebook/notebooks
```

The notebook is keyed to the directory rather than to the workspace id, because
Herdr hands out a fresh workspace id every time a workspace is opened. Keying on
the directory is what lets a notebook outlive its workspace.

The directory is reduced to one canonical spelling before it is hashed, so a
single directory can only ever have one notebook. Symbolic links are resolved,
and on a case-insensitive filesystem the spelling the parent directory actually
stores wins over the one that was asked for:

```text
~/code/project       ->  project-<digest>.md
~/Code/project       ->  project-<digest>.md   the same notebook
~/link-to-project    ->  project-<digest>.md   the same notebook
```

Renaming or moving the directory is the one thing that does start a fresh
notebook. The old file stays where it is; rename it yourself to reconnect it.

The plugin creates the directory and nothing else. The file is the editor's to
create, so quitting without saving leaves nothing behind, and no line in a
notebook was written by anything but you. Nothing here deletes a notebook
either; edit or remove the files yourself when you want to.

## Requirements

- Herdr 0.8.0 or newer, on Linux or macOS
- Python 3.9 or newer
- An editor on `PATH`, via `$VISUAL`, `$EDITOR`, or `vi`

## Development

```shell
herdr plugin link .
python3 -m unittest discover -s tests -t .
uvx ruff@0.16.2 check . && uvx ruff@0.16.2 format --check .
```
