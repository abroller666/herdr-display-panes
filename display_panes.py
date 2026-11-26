#!/usr/bin/env python3
"""tmux display-panes for Herdr.

Draws a map of the panes in the current tab inside a popup, labels each pane
with a home-row key, and focuses the pane whose key you press.
"""

import json
import os
import re
import select
import socket
import sys
import termios
import time
import tty
import unicodedata

DEFAULT_LABELS = "asdfghjklwertyuiopzxcvbnm"  # home row, then top and bottom rows (no q)
DEFAULT_TIMEOUT = 10  # seconds, like tmux display-panes-time
MAX_TIMEOUT = 100_000_000  # macOS select() rejects larger timeouts with EINVAL
CLOSE_KEYS = ("q", "\x1b", "\x03")  # q, Esc, Ctrl-C
HERDR_TIMEOUT = 5  # seconds to wait for the Herdr CLI / socket to respond



def fg(rgb):
    return "\033[38;2;{};{};{}m".format(*rgb)


def bg(rgb):
    return "\033[48;2;{};{};{}m".format(*rgb)


GREEN_RGB = (158, 206, 106)
BADGE_TEXT_RGB = (17, 24, 39)  # text on colored badges
# Colors derived from the terminal's background/foreground (see use_terminal_colors).
# These defaults assume a dark terminal and are used if the terminal does not answer.
BASE_RGB = (17, 24, 39)
GRAY_RGB = (95, 111, 100)
TEXT_RGB = (156, 163, 175)
IDLE_BADGE_RGB = (75, 85, 99)
IDLE_BADGE_TEXT_RGB = (229, 231, 235)
FG_RGB = (229, 231, 235)  # the terminal's foreground (white on dark)
# Agent pane colors; overridable with "agent_color" / "agent_colors" in config.json.
AGENT_RGB = GREEN_RGB  # default for every agent
AGENT_COLORS = {}  # agent name (lowercase) -> RGB
# Focused shell pane color; overridable with "shell_color". None follows FG_RGB.
SHELL_RGB = None

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


def parse_labels(value):
    """Keep labels readable as one keypress byte: printable ASCII, no space."""
    bad = sorted({c for c in value if not "!" <= c <= "~"})
    if bad:
        print(f"display-panes: ignoring labels with unsupported characters {''.join(bad)!r}"
              " (use printable ASCII, no spaces)", file=sys.stderr)
        return DEFAULT_LABELS
    return "".join(dict.fromkeys(c for c in value if c not in CLOSE_KEYS))


def load_config():
    """Read optional overrides from $HERDR_PLUGIN_CONFIG_DIR/config.json."""
    global AGENT_RGB, SHELL_RGB
    labels, timeout = DEFAULT_LABELS, DEFAULT_TIMEOUT
    config_dir = os.environ.get("HERDR_PLUGIN_CONFIG_DIR")
    if config_dir:
        try:
            with open(os.path.join(config_dir, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise TypeError("expected a JSON object")
            labels = parse_labels(str(cfg.get("labels", labels)))
            timeout = float(cfg.get("timeout", timeout))
            if not 0 <= timeout <= MAX_TIMEOUT:  # also rejects NaN
                raise ValueError(f"invalid timeout {timeout!r} (use 0 to {MAX_TIMEOUT} seconds)")
            agent_rgb = parse_color(cfg["agent_color"]) if "agent_color" in cfg else AGENT_RGB
            agent_colors = cfg.get("agent_colors") or {}
            if not isinstance(agent_colors, dict):
                raise TypeError("agent_colors must be an object")
            agent_colors = {str(name).lower(): parse_color(color) for name, color in agent_colors.items()}
            shell_rgb = parse_color(cfg["shell_color"]) if "shell_color" in cfg else SHELL_RGB
            AGENT_RGB = agent_rgb
            SHELL_RGB = shell_rgb
            AGENT_COLORS.update(agent_colors)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, OverflowError) as e:
            print(f"display-panes: ignoring invalid config.json: {e}", file=sys.stderr)
            labels, timeout = DEFAULT_LABELS, DEFAULT_TIMEOUT
    return labels or DEFAULT_LABELS, timeout


def herdr_cli(*args):
    import subprocess  # only needed without a socket; importing it costs ~4ms at startup

    try:
        out = subprocess.run([HERDR, *args], capture_output=True, text=True, check=True, timeout=HERDR_TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.exit(f"display-panes: herdr {' '.join(args)} did not respond within {HERDR_TIMEOUT}s")
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip().splitlines()
        sys.exit(f"display-panes: herdr {' '.join(args)} failed" + (f": {detail[-1]}" if detail else ""))
    except OSError as e:
        sys.exit(f"display-panes: could not run herdr: {e}")
    try:
        return json.loads(out.stdout)["result"]
    except (ValueError, KeyError, TypeError):
        sys.exit(f"display-panes: unexpected output from herdr {' '.join(args)}")


def herdr_socket(path, method, params):
    """One request over the Herdr socket API (much faster than spawning the CLI)."""
    req = {"id": "display-panes", "method": method, "params": params}
    reply = b""
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(HERDR_TIMEOUT)
            s.connect(path)
            s.sendall((json.dumps(req) + "\n").encode())
            while not reply.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                reply += chunk
    except socket.timeout:  # noqa: UP041 -- not an alias of TimeoutError until Python 3.10 (we support 3.8+)
        sys.exit(f"display-panes: Herdr socket did not respond within {HERDR_TIMEOUT}s")
    except OSError as e:
        sys.exit(f"display-panes: could not reach Herdr socket: {e}")
    try:
        resp = json.loads(reply)
    except ValueError:
        sys.exit(f"display-panes: unexpected reply from Herdr socket for {method}")
    if "error" in resp:
        err = resp["error"]
        sys.exit(f"display-panes: Herdr {method} failed: {err.get('message', err) if isinstance(err, dict) else err}")
    return resp["result"]


def herdr(method, params, *cli_args):
    """Call Herdr over its socket, falling back to the CLI when there is no socket."""
    path = os.environ.get("HERDR_SOCKET_PATH")
    return herdr_socket(path, method, params) if path else herdr_cli(*cli_args)


def focus(pane_id):
    """Focus a pane. Needs the socket: the Herdr CLI cannot focus a pane by id."""
    path = os.environ.get("HERDR_SOCKET_PATH")
    if not path:
        sys.exit("display-panes: cannot jump to the pane: HERDR_SOCKET_PATH is not set"
                 " (run display-panes from Herdr's plugin action)")
    herdr_socket(path, "pane.focus", {"pane_id": pane_id})


def show_error(fd, message, timeout):
    """Keep the popup open with the error until a key is pressed, so it isn't lost."""
    sys.stdout.write(f"\033[2J\033[H{message}\r\n\r\n{fg(GRAY_RGB)}Press any key to close{RESET}")
    sys.stdout.flush()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)
        if select.select([fd], [], [], timeout)[0]:
            os.read(fd, 1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


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
    out, used = [], 0
    for ch in text:
        used += display_width(ch)
        if used > width - 1:
            break
        out.append(ch)
    return "".join(out) + "…" if width > 0 else ""


def accent(info, focused):
    """Agent panes use their configured color; shell panes only when focused."""
    agent = (info.get("agent") or "").lower()
    if agent:
        return AGENT_COLORS.get(agent, AGENT_RGB)
    return (SHELL_RGB or FG_RGB) if focused else None


def mix(rgb, amount, base=None):
    """Blend rgb into base (the terminal background); 0 gives base, 1 gives rgb."""
    return tuple(round(b + (c - b) * amount) for c, b in zip(rgb, base or BASE_RGB))


def use_terminal_colors(bg_rgb, fg_rgb):
    """Derive the neutral colors from the terminal so light themes work too."""
    global BASE_RGB, GRAY_RGB, TEXT_RGB, IDLE_BADGE_RGB, IDLE_BADGE_TEXT_RGB, FG_RGB
    BASE_RGB = bg_rgb
    FG_RGB = fg_rgb
    GRAY_RGB = mix(fg_rgb, 0.4)
    TEXT_RGB = mix(fg_rgb, 0.75)
    IDLE_BADGE_RGB = mix(fg_rgb, 0.5)
    IDLE_BADGE_TEXT_RGB = bg_rgb


def query_terminal_colors(fd, timeout=0.2):
    """Ask the terminal for its background (OSC 11) and foreground (OSC 10).

    Returns any ordinary input (keypresses) read alongside the responses.
    """
    sys.stdout.write("\033]11;?\033\\\033]10;?\033\\")
    sys.stdout.flush()
    buf = ""
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(re.findall(r"\x1b\]1[01];[^\x07\x1b]*(?:\x07|\x1b\\)", buf)) < 2:
        if select.select([fd], [], [], max(0, end - time.monotonic()))[0]:
            buf += os.read(fd, 1024).decode(errors="ignore")
    found = {}
    for code, r, g, b in re.findall(r"\]1([01]);rgb:([0-9a-fA-F]+)/([0-9a-fA-F]+)/([0-9a-fA-F]+)", buf):
        found[code] = tuple(int(v, 16) * 255 // (16 ** len(v) - 1) for v in (r, g, b))
    if "1" in found and "0" in found:
        use_terminal_colors(found["1"], found["0"])
    # Drop the responses (and any cut off by the timeout); keep the keypresses.
    return re.sub(r"\x1b\]1[01];[^\x07\x1b]*(?:\x07|\x1b\\|\x1b?$)", "", buf)


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
    try:
        cols, rows = os.get_terminal_size(sys.stdout.fileno())  # cheaper than importing shutil
    except OSError:
        cols, rows = 80, 24
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

    def put_text(x, y, text, c):
        for ch in text:
            put(x, y, ch, c)
            if display_width(ch) == 2:
                put(x + 1, y, "", c)  # the wide char covers the next cell
            x += display_width(ch)

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
            pane_bg = bg(mix(rgb, 0.16))
            for y in range(y0 + 1, y1):
                for x in range(x0 + 1, x1):
                    if 0 <= x < cols and 0 <= y < rows:
                        tint[y][x] = pane_bg
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
        # border heading: the pane's own title, with a dot on the focused pane
        name = sanitize(info.get("label") or "").strip()
        prefix = "● " if focused else ""
        room = x1 - x0 - 3  # cells between the corner padding
        if name and room - 2 - len(prefix) >= 1:
            put_text(x0 + 2, y0, f" {prefix}{fit(name, room - 2 - len(prefix))} ", c)
        elif focused and room >= 3:
            put_text(x0 + 2, y0, " ● ", c)

        if not rgb:
            badge_rgb, badge_text_rgb = IDLE_BADGE_RGB, IDLE_BADGE_TEXT_RGB
        elif info.get("agent"):
            badge_rgb, badge_text_rgb = rgb, BADGE_TEXT_RGB
        else:  # focused shell pane; the default foreground-colored badge gets background-colored text
            badge_rgb, badge_text_rgb = rgb, BADGE_TEXT_RGB if SHELL_RGB else BASE_RGB
        badge_style = BOLD + bg(badge_rgb) + fg(badge_text_rgb)
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
            put_text(cx - display_width(text) // 2, y, text, style)
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
    layout = herdr("pane.layout", {"pane_id": active} if active else {},
                   "pane", "layout", *(["--pane", active] if active else ["--current"]))["layout"]
    panes = sorted(layout["panes"], key=lambda p: (p["rect"]["y"], p["rect"]["x"]))[: len(labels)]
    infos = {p["pane_id"]: p for p in herdr("pane.list", {}, "pane", "list")["panes"]}

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)  # keep keys pressed before raw mode
        pending = query_terminal_colors(fd)
        sys.stdout.write("\033[?1049h\033[?25l")  # alternate screen, hide cursor
        draw(panes, labels, layout["area"], infos)
        if pending:
            key = pending[0]
        else:
            ready, _, _ = select.select([fd], [], [], timeout)
            key = os.read(fd, 1).decode(errors="ignore") if ready else ""
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\033[?25h\033[?1049l")  # show cursor, restore the screen
        sys.stdout.flush()

    if key and key in labels[: len(panes)]:
        try:
            focus(panes[labels.index(key)]["pane_id"])
        except SystemExit as e:
            if isinstance(e.code, str):
                show_error(fd, e.code, timeout or DEFAULT_TIMEOUT)
            raise


if __name__ == "__main__":
    main()
