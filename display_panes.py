#!/usr/bin/env python3
"""tmux display-panes for Herdr.

Draws a map of the panes in the current tab inside a popup, labels each pane
with a home-row key, and focuses the pane whose key you press.
"""

import json
import os
import select
import shutil
import socket
import subprocess
import sys
import termios
import tty
import unicodedata

DEFAULT_LABELS = "asdfghjklwertyuiopzxcvbnm"  # home row, then top and bottom rows (no q)
DEFAULT_TIMEOUT = 10  # seconds, like tmux display-panes-time
CLOSE_KEYS = ("q", "\x1b", "\x03")  # q, Esc, Ctrl-C

GREEN = "\033[1;38;2;158;206;106m"
GRAY = "\033[38;2;95;111;100m"
BOLD = "\033[1m"
RESET = "\033[0m"

HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"


def load_config():
    """Read optional overrides from $HERDR_PLUGIN_CONFIG_DIR/config.json."""
    labels, timeout = DEFAULT_LABELS, DEFAULT_TIMEOUT
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if config_dir:
        try:
            with open(os.path.join(config_dir, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            labels = "".join(dict.fromkeys(c for c in str(cfg.get("labels", labels)) if c not in CLOSE_KEYS))
            timeout = float(cfg.get("timeout", timeout))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError) as e:
            print(f"display-panes: ignoring invalid config.json: {e}", file=sys.stderr)
    return labels or DEFAULT_LABELS, timeout


def herdr(*args):
    out = subprocess.run([HERDR, *args], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)["result"]


def focus(pane_id):
    path = os.environ.get("HERDR_SOCKET_PATH")
    if not path:
        return
    s = socket.socket(socket.AF_UNIX)
    s.connect(path)
    req = {"id": "display-panes", "method": "pane.focus", "params": {"pane_id": pane_id}}
    s.sendall((json.dumps(req) + "\n").encode())
    s.recv(65536)
    s.close()


def find_pane_id(value):
    """Find the first pane id in the plugin context JSON."""
    if isinstance(value, dict):
        for key in ("pane_id", "focused_pane_id"):
            if isinstance(value.get(key), str):
                return value[key]
        for key in ("pane", "focused_pane"):
            found = find_pane_id(value.get(key))
            if found:
                return found
        for v in value.values():
            found = find_pane_id(v)
            if found:
                return found
    return None


def active_pane_id():
    try:
        ctx = json.loads(os.environ.get("HERDR_PLUGIN_CONTEXT_JSON") or "{}")
    except ValueError:
        ctx = {}
    return (
        find_pane_id(ctx)
        or os.environ.get("HERDR_ACTIVE_PANE_ID")
        or os.environ.get("HERDR_PANE_ID")
    )


def display_width(text):
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def fit(text, width):
    """Truncate text to a display width, ending with an ellipsis if cut."""
    if display_width(text) <= width:
        return text
    out = ""
    for ch in text:
        if display_width(out + ch) > width - 1:
            break
        out += ch
    return out + "…" if width > 0 else ""


def describe(info):
    """Lines shown under the label: agent name and session title, or cwd."""
    if info.get("agent"):
        title = info.get("terminal_title_stripped") or ""
        return [info["agent"], title] if title else [info["agent"]]
    cwd = info.get("foreground_cwd") or info.get("cwd") or ""
    home = os.path.expanduser("~")
    return ["~" + cwd[len(home):] if cwd.startswith(home) else cwd]


def draw(panes, labels, area, infos):
    cols, rows = shutil.get_terminal_size()
    rows -= 1  # last line is the hint
    sx = cols / area["width"]
    sy = rows / area["height"]
    grid = [[" "] * cols for _ in range(rows)]
    color = [[None] * cols for _ in range(rows)]

    def put(x, y, ch, c):
        if 0 <= x < cols and 0 <= y < rows:
            grid[y][x] = ch
            color[y][x] = c

    for mark, p in zip(labels, panes):
        r = p["rect"]
        x0 = int((r["x"] - area["x"]) * sx)
        y0 = int((r["y"] - area["y"]) * sy)
        x1 = max(x0 + 2, int((r["x"] - area["x"] + r["width"]) * sx) - 1)
        y1 = max(y0 + 2, int((r["y"] - area["y"] + r["height"]) * sy) - 1)
        c = GREEN if p["focused"] else GRAY
        for x in range(x0, x1 + 1):
            put(x, y0, "─", c)
            put(x, y1, "─", c)
        for y in range(y0, y1 + 1):
            put(x0, y, "│", c)
            put(x1, y, "│", c)
        for x, y, ch in ((x0, y0, "┌"), (x1, y0, "┐"), (x0, y1, "└"), (x1, y1, "┘")):
            put(x, y, ch, c)
        cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
        put(cx, cy, mark, GREEN if p["focused"] else BOLD)
        width = max(0, x1 - x0 - 3)
        for dy, text in enumerate(describe(infos.get(p["pane_id"], {})), 1):
            if cy + dy >= y1:
                break
            text = fit(text, width)
            x = cx - display_width(text) // 2
            for ch in text:
                put(x, cy + dy, ch, c)
                if display_width(ch) == 2:
                    put(x + 1, cy + dy, "", c)
                x += display_width(ch)

    out = ["\033[2J\033[H"]
    for y in range(rows):
        line = ""
        for x in range(cols):
            c = color[y][x]
            line += f"{c}{grid[y][x]}{RESET}" if c else grid[y][x]
        out.append(line + "\r\n")
    out.append(f"{GRAY}Press a key to jump / q or Esc to close{RESET}")
    sys.stdout.write("".join(out))
    sys.stdout.flush()


def main():
    labels, timeout = load_config()
    active = active_pane_id()
    layout = herdr("pane", "layout", *(["--pane", active] if active else ["--current"]))["layout"]
    panes = sorted(layout["panes"], key=lambda p: (p["rect"]["y"], p["rect"]["x"]))[: len(labels)]
    infos = {p["pane_id"]: p for p in herdr("pane", "list")["panes"]}

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write("\033[?25l")
        draw(panes, labels, layout["area"], infos)
        ready, _, _ = select.select([fd], [], [], timeout)
        key = os.read(fd, 1).decode(errors="ignore") if ready else ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()

    if key and key in labels[: len(panes)]:
        focus(panes[labels.index(key)]["pane_id"])


if __name__ == "__main__":
    main()
