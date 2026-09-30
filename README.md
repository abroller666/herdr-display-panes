# herdr-display-panes

[日本語](README.ja.md)

tmux's `display-panes` for [Herdr](https://herdr.dev).

Press one key to open a popup with a map of the panes in the current tab.
Each pane gets a one-key label, top-left first: the home row
(`a` `s` `d` `f` `g` `h` `j` `k` `l`), then the top row (`w` … `p`) and the
bottom row (`z` … `m`), up to 25 panes. Press a label to focus that pane.

```
┌──────────────────────────────┐
│              a               │
│            claude            │
│      fix-login-redirect      │
└──────────────────────────────┘
┌──────────────┐┌──────────────┐
│      s       ││      d       │
│    codex     ││  ~/project   │
└──────────────┘└──────────────┘
Press a key to jump / q or Esc to close
```

- The focused pane is highlighted.
- Agent panes show the agent name and its session title; shell panes show the working directory.
- No graphics protocol needed: the map is plain text, so it works in any terminal.
- The popup closes on `q`, `Esc`, or after 10 seconds.

## Requirements

- Herdr 0.9.2 or newer
- macOS or Linux (tested on macOS)
- `python3` (3.8+, standard library only)

## Install

```sh
herdr plugin install abroller666/herdr-display-panes
```

To pin a release, pass `--ref`:

```sh
herdr plugin install abroller666/herdr-display-panes --ref v0.1.0
```

Add a keybinding to `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+i"
type = "plugin_action"
command = "abroller666.display-panes.open"
description = "display panes"
```

Then reload: `herdr server reload-config`.

## Configuration

Optional. Create `config.json` in the plugin config directory
(`herdr plugin config-dir abroller666.display-panes`):

```json
{
  "labels": "asdfghjklwertyuiopzxcvbnm",
  "timeout": 10
}
```

- `labels`: keys assigned to panes, top-left to bottom-right. Duplicates and
  `q` are ignored. At most `len(labels)` panes get a label.
- `timeout`: seconds before the popup closes by itself.

## License

MIT
