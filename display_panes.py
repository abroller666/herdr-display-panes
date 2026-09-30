#!/usr/bin/env python3
"""tmux display-panes for Herdr.

Draws a map of the panes in the current tab inside a popup, labels each pane
with a home-row key, and focuses the pane whose key you press.
"""

import json
import re
import os
import select
import shutil
import socket
import subprocess
import sys
import termios
import time
import tty
import unicodedata

DEFAULT_LABELS = "asdfghjklwertyuiopzxcvbnm"  # home row, then top and bottom rows (no q)
DEFAULT_TIMEOUT = 10  # seconds, like tmux display-panes-time
CLOSE_KEYS = ("q", "\x1b", "\x03")  # q, Esc, Ctrl-C



def fg(rgb):
    return "\033[38;2;%d;%d;%dm" % rgb


def bg(rgb):
    return "\033[48;2;%d;%d;%dm" % rgb


GREEN_RGB = (158, 206, 106)
BADGE_TEXT_RGB = (17, 24, 39)  # text on colored badges
# Colors derived from the terminal's background/foreground (see use_terminal_colors).
# These defaults assume a dark terminal and are used if the terminal does not answer.
BASE_RGB = (17, 24, 39)
GRAY_RGB = (95, 111, 100)
TEXT_RGB = (156, 163, 175)
IDLE_BADGE_RGB = (75, 85, 99)
IDLE_BADGE_TEXT_RGB = (229, 231, 235)
# Agent pane colors; overridable with "agent_color" / "agent_colors" in config.json.
AGENT_RGB = GREEN_RGB  # default for every agent
AGENT_COLORS = {}  # agent name (lowercase) -> RGB

BOLD = "\033[1m"
RESET = "\033[0m"

LIGHT_BOX = "─│┌┐└┘"
HEAVY_BOX = "━┃┏┓┗┛"  # focused pane


HERDR = os.environ.get("HERDR_BIN_PATH") or "herdr"


def parse_color(value):
    """Parse "#rrggbb" or "#rgb" into an RGB tuple."""
    s = str(value).strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    try:
        if len(s) != 6:
            raise ValueError
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        raise ValueError(f"invalid color {value!r} (use #rrggbb or #rgb)") from None


def load_config():
    """Read optional overrides from $HERDR_PLUGIN_CONFIG_DIR/config.json."""
    global AGENT_RGB
    labels, timeout = DEFAULT_LABELS, DEFAULT_TIMEOUT
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if config_dir:
        try:
            with open(os.path.join(config_dir, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            labels = "".join(dict.fromkeys(c for c in str(cfg.get("labels", labels)) if c not in CLOSE_KEYS))
            timeout = float(cfg.get("timeout", timeout))
            if "agent_color" in cfg:
                AGENT_RGB = parse_color(cfg["agent_color"])
            for name, color in (cfg.get("agent_colors") or {}).items():
                AGENT_COLORS[str(name).lower()] = parse_color(color)
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


def sanitize(text):
    """Replace control characters (newline, ESC, BEL, ...) so they can't reach the terminal."""
    return "".join("?" if unicodedata.category(ch) == "Cc" else ch for ch in text)


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


def accent(info, focused):
    """Agent panes use their configured color; shell panes only when focused."""
    agent = (info.get("agent") or "").lower()
    if agent:
        return AGENT_COLORS.get(agent, AGENT_RGB)
    return GREEN_RGB if focused else None


def mix(rgb, amount, base=None):
    """Blend rgb into base (the terminal background); 0 gives base, 1 gives rgb."""
    return tuple(round(b + (c - b) * amount) for c, b in zip(rgb, base or BASE_RGB))


def use_terminal_colors(bg_rgb, fg_rgb):
    """Derive the neutral colors from the terminal so light themes work too."""
    global BASE_RGB, GRAY_RGB, TEXT_RGB, IDLE_BADGE_RGB, IDLE_BADGE_TEXT_RGB
    BASE_RGB = bg_rgb
    GRAY_RGB = mix(fg_rgb, 0.4)
    TEXT_RGB = mix(fg_rgb, 0.75)
    IDLE_BADGE_RGB = mix(fg_rgb, 0.5)
    IDLE_BADGE_TEXT_RGB = bg_rgb


def query_terminal_colors(fd, timeout=0.2):
    """Ask the terminal for its background (OSC 11) and foreground (OSC 10)."""
    sys.stdout.write("\033]11;?\033\\\033]10;?\033\\")
    sys.stdout.flush()
    buf = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end and buf.count("rgb:") < 2:
        if select.select([fd], [], [], max(0, end - time.monotonic()))[0]:
            buf += os.read(fd, 1024).decode(errors="ignore")
    found = {}
    for code, r, g, b in re.findall(r"\]1([01]);rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", buf):
        found[code] = tuple(int(v, 16) * 255 // (16 ** len(v) - 1) for v in (r, g, b))
    if "1" in found and "0" in found:
        use_terminal_colors(found["1"], found["0"])


def badge(content, pad_x, pad_y):
    """Surround label rows with padding; every cell is painted as the badge."""
    width = max(display_width(r) for r in content) + 2 * pad_x
    blank = " " * width
    rows = [blank] * pad_y
    for r in content:
        left = (width - display_width(r)) // 2
        rows.append(" " * left + r + " " * (width - left - display_width(r)))
    return rows + [blank] * pad_y


def label_layouts(mark, lines):
    """Candidate (badge rows, gap, description lines), largest first."""
    keycap = badge([mark.upper()], 4, 1)
    tiny = badge([mark.upper()], 1, 0)
    return [
        (keycap, 1, lines),
        (keycap, 0, lines),
        (keycap, 0, lines[:1]),
        (tiny, 0, lines),
        (tiny, 0, lines[:1]),
        (tiny, 0, []),
    ]


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
    tint = [[""] * cols for _ in range(rows)]  # background of the focused pane

    def put(x, y, ch, c):
        if 0 <= x < cols and 0 <= y < rows:
            grid[y][x] = ch
            color[y][x] = c

    boxes = []
    for mark, p in zip(labels, panes):
        r = p["rect"]
        x0 = int((r["x"] - area["x"]) * sx)
        y0 = int((r["y"] - area["y"]) * sy)
        x1 = max(x0 + 2, int((r["x"] - area["x"] + r["width"]) * sx) - 1)
        y1 = max(y0 + 2, int((r["y"] - area["y"] + r["height"]) * sy) - 1)
        info = infos.get(p["pane_id"], {})
        boxes.append((mark, p, info, x0, y0, x1, y1, label_layouts(mark, describe(info))))

    def fits(layout, x0, y0, x1, y1):
        mark_rows, gap, lines = layout
        return (display_width(mark_rows[0]) <= x1 - x0 - 3
                and len(mark_rows) + gap + len(lines) <= y1 - y0 - 1)

    # use the same (largest) badge size for every pane so the map looks uniform
    last = len(boxes[0][7]) - 1 if boxes else 0
    choice = max(
        (next((i for i, lay in enumerate(b[7]) if fits(lay, *b[3:7])), last) for b in boxes),
        default=0,
    )

    for mark, p, info, x0, y0, x1, y1, layouts in boxes:
        focused = p["focused"]
        rgb = accent(info, focused)
        # the focused pane is drawn at full strength; the others are dimmed
        if focused:
            c = BOLD + fg(rgb)
            for y in range(y0 + 1, y1):
                for x in range(x0 + 1, x1):
                    if 0 <= x < cols and 0 <= y < rows:
                        tint[y][x] = bg(mix(rgb, 0.16))
        else:
            c = fg(mix(rgb, 0.45)) if rgb else fg(GRAY_RGB)
        h_, v_, tl, tr, bl, br = HEAVY_BOX if focused else LIGHT_BOX
        for x in range(x0, x1 + 1):
            put(x, y0, h_, c)
            put(x, y1, h_, c)
        for y in range(y0, y1 + 1):
            put(x0, y, v_, c)
            put(x1, y, v_, c)
        for x, y, ch in ((x0, y0, tl), (x1, y0, tr), (x0, y1, bl), (x1, y1, br)):
            put(x, y, ch, c)
        title = " ● current "
        if focused and x1 - x0 - 3 >= len(title):
            for i, ch in enumerate(title):
                put(x0 + 2 + i, y0, ch, c)

        badge_style = BOLD + bg(rgb or IDLE_BADGE_RGB) + fg(BADGE_TEXT_RGB if rgb else IDLE_BADGE_TEXT_RGB)
        inner_w, inner_h = x1 - x0 - 1, y1 - y0 - 1
        cx = (x0 + x1) // 2
        mark_rows, gap, lines = layouts[choice]
        h = len(mark_rows) + gap + len(lines)
        # vertically center the badge and the description lines as one block
        y = y0 + 1 + max(0, (inner_h - h) // 2)
        for row in mark_rows:
            gx = cx - display_width(row) // 2
            for i, ch in enumerate(row):
                put(gx + i, y, ch, badge_style)
            y += 1
        y += gap
        width = max(0, inner_w - 2)
        for n, text in enumerate(lines):
            if y >= y1:
                break
            # first line (agent name) in the pane color, the rest dimmer
            text_rgb = rgb if rgb and n == 0 and info.get("agent") else TEXT_RGB
            style = BOLD + fg(text_rgb if focused else mix(text_rgb, 0.55))
            text = fit(sanitize(text), width)
            x = cx - display_width(text) // 2
            for ch in text:
                put(x, y, ch, style)
                if display_width(ch) == 2:
                    put(x + 1, y, "", style)
                x += display_width(ch)
            y += 1

    out = ["\033[2J\033[H"]
    for y in range(rows):
        line = ""
        for x in range(cols):
            style = tint[y][x] + (color[y][x] or "")
            line += f"{style}{grid[y][x]}{RESET}" if style else grid[y][x]
        out.append(line + "\r\n")
    out.append(f"{fg(GRAY_RGB)}Press a key to jump / q or Esc to close{RESET}")
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
        query_terminal_colors(fd)
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
