"""Interactive terminal UI (TUI) for MacTree.

A curses-based tree browser, in the spirit of ``ncdu``. It scans a directory
with the multi-threaded :class:`~mactree.scanner.Scanner`, showing live progress,
then lets you walk the tree: expand/collapse folders, see each entry's size and
its share of the parent as a bar, sorted largest-first.

Keys
----
  ↑/↓, j/k      move selection
  →/Enter/l     expand folder (or descend)
  ←/h           collapse / go to parent
  g / G         jump to top / bottom
  PgUp / PgDn   page up / down
  s             toggle size units (allocated vs logical not available post-scan;
                toggles human-readable vs exact bytes)
  r             rescan
  q             quit

This module only uses the standard library (``curses``), so there is no extra
dependency for the terminal UI.
"""

from __future__ import annotations

import curses
import threading
import time
from typing import List, Optional

from .scanner import Node, Scanner


# --------------------------------------------------------------------------
# A flattened, navigable view over the Node tree.
# --------------------------------------------------------------------------
class Row:
    """A visible row in the tree view."""

    __slots__ = ("node", "depth", "expanded")

    def __init__(self, node: Node, depth: int) -> None:
        self.node = node
        self.depth = depth
        self.expanded = False


class TreeView:
    """Maintains the expand/collapse state and the flattened visible list."""

    def __init__(self, root: Node) -> None:
        self.root = root
        self._expanded: set[str] = set()
        # Pre-sort children of every directory largest-first, once.
        self._sort_cache: dict[int, List[Node]] = {}
        self._expanded.add(root.path)  # root starts expanded
        self.rows: List[Row] = []
        self.rebuild()

    def _children(self, node: Node) -> List[Node]:
        key = id(node)
        cached = self._sort_cache.get(key)
        if cached is None:
            cached = sorted(
                (c for c in node.children),
                key=lambda c: c.size,
                reverse=True,
            )
            self._sort_cache[key] = cached
        return cached

    def rebuild(self) -> None:
        rows: List[Row] = []

        def walk(node: Node, depth: int) -> None:
            row = Row(node, depth)
            row.expanded = node.path in self._expanded
            rows.append(row)
            if node.is_dir and row.expanded:
                for child in self._children(node):
                    if child.is_dir:
                        walk(child, depth + 1)
                    else:
                        rows.append(Row(child, depth + 1))

        walk(self.root, 0)
        self.rows = rows

    def is_expanded(self, node: Node) -> bool:
        return node.path in self._expanded

    def expand(self, node: Node) -> None:
        if node.is_dir and node.children:
            self._expanded.add(node.path)
            self.rebuild()

    def collapse(self, node: Node) -> None:
        self._expanded.discard(node.path)
        self.rebuild()

    def toggle(self, node: Node) -> None:
        if node.path in self._expanded:
            self.collapse(node)
        else:
            self.expand(node)


# --------------------------------------------------------------------------
# Formatting helpers
# --------------------------------------------------------------------------
_UNITS = ["B", "K", "M", "G", "T", "P"]


def human(n: int) -> str:
    if n <= 0:
        return "0 B"
    i = 0
    f = float(n)
    while f >= 1024 and i < len(_UNITS) - 1:
        f /= 1024.0
        i += 1
    if i == 0:
        return f"{int(f)} B"
    return f"{f:.1f} {_UNITS[i]}"


def bar(fraction: float, width: int) -> str:
    """A unicode block bar representing ``fraction`` of ``width`` columns."""
    fraction = max(0.0, min(1.0, fraction))
    full = int(fraction * width)
    rem = fraction * width - full
    blocks = "█" * full
    if full < width:
        eighths = " ▏▎▍▌▋▊▉"
        idx = int(rem * 8)
        if idx > 0:
            blocks += eighths[idx]
            full += 1
    return blocks + " " * (width - len(blocks))


# --------------------------------------------------------------------------
# File-type glyphs
# --------------------------------------------------------------------------
_EXT_GLYPH = {
    # code
    "py": "", "js": "", "ts": "", "jsx": "", "tsx": "", "go": "", "rs": "",
    "c": "", "h": "", "cpp": "", "cc": "", "java": "", "rb": "", "php": "",
    "sh": "", "html": "", "css": "", "json": "", "yml": "", "yaml": "",
    "toml": "", "xml": "", "sql": "", "md": "", "txt": "", "ipynb": "",
    # images
    "png": "", "jpg": "", "jpeg": "", "gif": "", "svg": "", "webp": "",
    "heic": "", "bmp": "", "tiff": "", "ico": "",
    # media
    "mp4": "", "mov": "", "mkv": "", "avi": "", "webm": "",
    "mp3": "", "wav": "", "flac": "", "aac": "", "m4a": "",
    # archives
    "zip": "", "tar": "", "gz": "", "bz2": "", "xz": "", "7z": "", "rar": "",
    "dmg": "", "pkg": "", "iso": "",
    # docs
    "pdf": "", "doc": "", "docx": "", "xls": "", "xlsx": "", "ppt": "", "pptx": "",
    # misc
    "app": "", "exe": "", "bin": "", "o": "", "so": "", "dylib": "",
    "pyc": "", "lock": "", "log": "", "db": "", "sqlite": "",
}


def _glyph(node: Node, expanded: bool) -> str:
    if node.is_dir:
        return "" if expanded else ""  # open / closed folder (fallback-safe)
    name = node.name
    i = name.rfind(".")
    ext = name[i + 1:].lower() if i > 0 else ""
    return _EXT_GLYPH.get(ext, "")


# --------------------------------------------------------------------------
# Scan with a live progress screen
# --------------------------------------------------------------------------
def _scan_with_progress(stdscr, path: str, workers: Optional[int]) -> Optional[Node]:
    """Run a scan on a worker thread, animating progress until done."""
    scanner = Scanner(workers=workers)
    result: dict = {}

    def run() -> None:
        try:
            result["root"] = scanner.scan(path)
        except Exception as exc:  # noqa: BLE001
            result["error"] = str(exc)

    t = threading.Thread(target=run, daemon=True)
    t.start()

    curses.curs_set(0)
    _init_colors()
    stdscr.nodelay(True)
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while t.is_alive():
        p = scanner.progress
        stdscr.erase()
        h, w = stdscr.getmaxyx()

        # centered card
        cw = min(60, w - 4)
        cx = (w - cw) // 2
        cy = max(1, h // 2 - 5)
        _draw_box(stdscr, cy, cx, 11, cw, title=" MacTree ", title_attr=C(C_ACCENT) | curses.A_BOLD)

        tline = " 🌳  scanning your disk…"
        _put(stdscr, cy + 1, cx + 2, tline[: cw - 4], C(C_HEADER) | curses.A_BOLD)

        pstr = path if len(path) <= cw - 8 else "…" + path[-(cw - 9):]
        _put(stdscr, cy + 3, cx + 4, f"{spinner[i % len(spinner)]}  {pstr}", C(C_DIM))

        # animated indeterminate bar
        bw = cw - 8
        pos = i % (bw + 10)
        barbuf = []
        for x in range(bw):
            d = (x - (pos - 5))
            barbuf.append("█" if 0 <= d < 6 else "░")
        _put(stdscr, cy + 5, cx + 4, "".join(barbuf), C(C_ACCENT))

        stats = f"  {p.dirs:>7} folders   {p.files:>7} files"
        _put(stdscr, cy + 7, cx + 2, stats.ljust(cw - 4), C(C_FG))
        stats2 = f"  {human(p.bytes):>9}            {p.elapsed:>5.1f}s"
        _put(stdscr, cy + 8, cx + 2, stats2.ljust(cw - 4), C(C_GREEN) | curses.A_BOLD)

        _put(stdscr, h - 1, 0, " scanning…  press q to abort ".ljust(w)[:w - 1],
             C(C_BAR) | curses.A_BOLD)

        stdscr.refresh()
        i += 1
        try:
            if stdscr.getch() in (ord("q"), 27):
                return None
        except curses.error:
            pass
        time.sleep(0.08)

    t.join()
    stdscr.nodelay(False)
    if "error" in result:
        _message(stdscr, f"Scan error: {result['error']}")
        return None
    return result.get("root")


def _message(stdscr, msg: str) -> None:
    stdscr.nodelay(False)
    h, w = stdscr.getmaxyx()
    stdscr.erase()
    _put(stdscr, h // 2, 2, msg, C(C_RED) | curses.A_BOLD)
    _put(stdscr, h // 2 + 2, 2, "press any key…", C(C_DIM))
    stdscr.refresh()
    stdscr.getch()


# --------------------------------------------------------------------------
# Color setup
# --------------------------------------------------------------------------
# Logical color-pair ids
C_FG = 10
C_DIM = 11
C_ACCENT = 12      # brand / highlights (magenta-ish)
C_HEADER = 13      # header text
C_GREEN = 14
C_YELLOW = 15
C_RED = 16
C_FOLDER = 17
C_BAR = 18         # reversed status bars
C_SEL = 19         # selected row
C_GUIDE = 20       # tree guide lines
C_PANEL = 21       # detail panel bg

# 256-color gradient pair ids for the bar (filled by _init_colors)
_GRAD_PAIRS: List[int] = []

_has_256 = False


def C(pair_id: int) -> int:
    try:
        return curses.color_pair(pair_id)
    except curses.error:
        return 0


def _init_colors() -> None:
    global _has_256, _GRAD_PAIRS
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
    except curses.error:
        pass
    _has_256 = curses.COLORS >= 256

    if _has_256:
        # 256-color palette: pick xterm cube colors for a rich look
        curses.init_pair(C_FG, 252, -1)
        curses.init_pair(C_DIM, 244, -1)
        curses.init_pair(C_ACCENT, 177, -1)   # soft violet
        curses.init_pair(C_HEADER, 81, -1)    # cyan
        curses.init_pair(C_GREEN, 114, -1)
        curses.init_pair(C_YELLOW, 221, -1)
        curses.init_pair(C_RED, 203, -1)
        curses.init_pair(C_FOLDER, 75, -1)    # bright blue
        curses.init_pair(C_BAR, 16, 177)      # black on violet
        curses.init_pair(C_SEL, 231, 61)      # white on indigo
        curses.init_pair(C_GUIDE, 240, -1)
        curses.init_pair(C_PANEL, 250, 236)   # light on dark gray

        # Heatmap gradient for bars: red->orange->yellow->green->cyan
        grad_colors = [196, 202, 208, 214, 220, 226, 190, 154, 118, 82, 46, 48, 51]
        _GRAD_PAIRS = []
        base = 40
        for n, col in enumerate(grad_colors):
            pid = base + n
            try:
                curses.init_pair(pid, col, -1)
                _GRAD_PAIRS.append(pid)
            except curses.error:
                break
    else:
        # 8/16-color fallback
        curses.init_pair(C_FG, curses.COLOR_WHITE, -1)
        curses.init_pair(C_DIM, curses.COLOR_WHITE, -1)
        curses.init_pair(C_ACCENT, curses.COLOR_MAGENTA, -1)
        curses.init_pair(C_HEADER, curses.COLOR_CYAN, -1)
        curses.init_pair(C_GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(C_YELLOW, curses.COLOR_YELLOW, -1)
        curses.init_pair(C_RED, curses.COLOR_RED, -1)
        curses.init_pair(C_FOLDER, curses.COLOR_BLUE, -1)
        curses.init_pair(C_BAR, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(C_SEL, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(C_GUIDE, curses.COLOR_WHITE, -1)
        curses.init_pair(C_PANEL, curses.COLOR_WHITE, curses.COLOR_BLUE)
        _GRAD_PAIRS = [C_RED, C_YELLOW, C_GREEN, C_HEADER]


def _grad_attr(frac: float) -> int:
    """Color attribute for a bar cell whose position fraction is ``frac``."""
    if not _GRAD_PAIRS:
        return 0
    idx = int(max(0.0, min(0.999, frac)) * len(_GRAD_PAIRS))
    return C(_GRAD_PAIRS[idx])


def _size_attr(frac: float) -> int:
    if frac >= 0.5:
        return C(C_RED) | curses.A_BOLD
    if frac >= 0.2:
        return C(C_YELLOW) | curses.A_BOLD
    if frac >= 0.05:
        return C(C_GREEN)
    return C(C_DIM)


# --------------------------------------------------------------------------
# Low-level drawing helpers
# --------------------------------------------------------------------------
def _put(stdscr, y: int, x: int, s: str, attr: int = 0) -> None:
    h, w = stdscr.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    s = s[: max(0, w - x - 1)]
    try:
        stdscr.addstr(y, x, s, attr)
    except curses.error:
        pass


def _draw_box(stdscr, y, x, height, width, title="", title_attr=0) -> None:
    tl, tr, bl, br, hz, vt = "╭", "╮", "╰", "╯", "─", "│"
    attr = C(C_GUIDE)
    _put(stdscr, y, x, tl + hz * (width - 2) + tr, attr)
    for r in range(1, height - 1):
        _put(stdscr, y + r, x, vt, attr)
        _put(stdscr, y + r, x + width - 1, vt, attr)
    _put(stdscr, y + height - 1, x, bl + hz * (width - 2) + br, attr)
    if title:
        _put(stdscr, y, x + 2, title, title_attr)


def _gradient_bar(stdscr, y: int, x: int, width: int, frac: float, dim=False) -> None:
    """Draw a heatmap-gradient bar of ``width`` cells filled to ``frac``."""
    frac = max(0.0, min(1.0, frac))
    filled = frac * width
    full = int(filled)
    for cx in range(width):
        if cx < full:
            ch = "█"
            attr = _grad_attr(cx / max(1, width))
        elif cx == full and (filled - full) > 0.15:
            eighths = " ▏▎▍▌▋▊▉"
            ch = eighths[min(7, int((filled - full) * 8))]
            attr = _grad_attr(cx / max(1, width))
        else:
            ch = "░"
            attr = C(C_GUIDE)
        if dim:
            attr |= curses.A_DIM
        _put(stdscr, y, x + cx, ch, attr)


def _tree_prefix(view: TreeView, idx: int) -> str:
    """Build │ ├─ └─ guide lines for row ``idx`` based on depth/siblings."""
    rows = view.rows
    depth = rows[idx].depth
    if depth == 0:
        return ""
    # Determine, for each ancestor depth level, whether a vertical line
    # continues (i.e. there is a later sibling at that level).
    cont = [False] * depth
    # is this row the last child at its depth within its parent's block?
    def last_at(level: int, start: int) -> bool:
        for j in range(start + 1, len(rows)):
            d = rows[j].depth
            if d < level:
                return True
            if d == level:
                return False
        return True
    is_last = last_at(depth, idx)
    for lvl in range(1, depth):
        cont[lvl] = not last_at(lvl, idx)
    parts = []
    for lvl in range(1, depth):
        parts.append("│  " if cont[lvl] else "   ")
    parts.append("└─ " if is_last else "├─ ")
    return "".join(parts)


# --------------------------------------------------------------------------
# Main browser loop
# --------------------------------------------------------------------------
def _browse(stdscr, view: TreeView, scan_path: str) -> str:
    """Run the interactive browser. Returns 'rescan' or 'quit'."""
    curses.curs_set(0)
    _init_colors()
    stdscr.keypad(True)

    selected = 0
    top = 0
    exact = False

    while True:
        h, w = stdscr.getmaxyx()
        # layout: title(0) colhdr(1) body(2..h-5) panel(h-4..h-2) help(h-1)
        body_top = 2
        body_h = max(1, h - 6)
        rows = view.rows
        root = view.root

        selected = max(0, min(selected, len(rows) - 1))
        if selected < top:
            top = selected
        elif selected >= top + body_h:
            top = selected - body_h + 1

        stdscr.erase()

        # ---- title bar ----
        title = f"  🌳 MacTree "
        meta = f" {human(root.size)} • {len(rows)} items "
        bar_line = title + " " + _ellip(scan_path, max(0, w - len(title) - len(meta) - 3))
        bar_line = bar_line.ljust(w - len(meta)) + meta
        _put(stdscr, 0, 0, bar_line[: w - 1], C(C_BAR) | curses.A_BOLD)

        # ---- column header ----
        hdr = f"{'SIZE':>10}  {'%':>5}  {'USAGE':<{_barw(w)}}  NAME"
        _put(stdscr, 1, 1, hdr[: w - 2], C(C_DIM) | curses.A_UNDERLINE)

        # ---- rows ----
        bw = _barw(w)
        for i in range(top, min(len(rows), top + body_h)):
            row = rows[i]
            node = row.node
            y = body_top + (i - top)
            is_sel = i == selected

            psize = _parent_size(view, i)
            frac = (node.size / psize) if psize else 0.0

            size_str = (f"{node.size:,}" if exact else human(node.size)).rjust(10)
            pct = f"{frac*100:4.1f}%"
            prefix = _tree_prefix(view, i)
            glyph = _glyph(node, view.is_expanded(node))
            caret = ("▾ " if view.is_expanded(node) else "▸ ") if node.is_dir and node.children else "  "
            name = node.name + ("/" if node.is_dir else "")

            if is_sel:
                # full-width selection highlight
                _put(stdscr, y, 0, " " * (w - 1), C(C_SEL))
            # size
            _put(stdscr, y, 1, size_str, (C(C_SEL) | curses.A_BOLD) if is_sel else _size_attr(frac))
            # pct
            _put(stdscr, y, 13, pct, (C(C_SEL)) if is_sel else C(C_DIM))
            # bar
            _gradient_bar(stdscr, y, 20, bw, frac, dim=is_sel)
            # name section
            nx = 20 + bw + 2
            name_attr = (C(C_SEL) | curses.A_BOLD) if is_sel else (
                (C(C_FOLDER) | curses.A_BOLD) if node.is_dir else C(C_FG))
            guide_attr = (C(C_SEL)) if is_sel else C(C_GUIDE)
            _put(stdscr, y, nx, prefix, guide_attr)
            nx2 = nx + _wlen(prefix)
            _put(stdscr, y, nx2, caret, (C(C_SEL)) if is_sel else C(C_ACCENT))
            nx3 = nx2 + 2
            _put(stdscr, y, nx3, f"{glyph} ", name_attr)
            _put(stdscr, y, nx3 + 2, name, name_attr)

        # ---- detail panel (selected item) ----
        sel = rows[selected].node if rows else None
        py = h - 4
        _put(stdscr, py, 0, "─" * (w - 1), C(C_GUIDE))
        if sel:
            psize = _parent_size(view, selected)
            frac = (sel.size / psize) if psize else 0.0
            kind = "folder" if sel.is_dir else "file"
            nkids = len(sel.children) if sel.is_dir else 0
            l1 = f" {('📁' if sel.is_dir else '📄')} {sel.name}"
            _put(stdscr, py + 1, 0, l1[: w - 1], C(C_HEADER) | curses.A_BOLD)
            l2 = (f"   {human(sel.size)}  ({sel.size:,} B)   "
                  f"{frac*100:.1f}% of parent   {kind}"
                  + (f"   {nkids} children" if sel.is_dir else ""))
            _put(stdscr, py + 2, 0, l2[: w - 1], C(C_FG))
            _put(stdscr, py + 3, 0, "   " + _ellip(sel.path, w - 5), C(C_DIM))

        # ---- help bar ----
        help_txt = ("  ↑↓/jk move   →/⏎ open   ← back   g/G top/btm   "
                    "s units   r rescan   q quit  ")
        pos = f" {selected+1}/{len(rows)} "
        line = help_txt.ljust(w - len(pos)) + pos
        _put(stdscr, h - 1, 0, line[: w - 1], C(C_BAR) | curses.A_BOLD)

        stdscr.refresh()

        # -- input --
        try:
            ch = stdscr.getch()
        except KeyboardInterrupt:
            return "quit"

        if ch in (ord("q"), 27):
            return "quit"
        elif ch in (curses.KEY_DOWN, ord("j")):
            selected += 1
        elif ch in (curses.KEY_UP, ord("k")):
            selected -= 1
        elif ch == curses.KEY_NPAGE:
            selected += body_h
        elif ch == curses.KEY_PPAGE:
            selected -= body_h
        elif ch == ord("g"):
            selected = 0
        elif ch == ord("G"):
            selected = len(rows) - 1
        elif ch in (curses.KEY_RIGHT, ord("l"), ord("\n"), curses.KEY_ENTER, 10, 13):
            node = rows[selected].node
            if node.is_dir and node.children:
                if not view.is_expanded(node):
                    view.expand(node)
                else:
                    selected += 1
        elif ch in (curses.KEY_LEFT, ord("h")):
            node = rows[selected].node
            if node.is_dir and view.is_expanded(node):
                view.collapse(node)
            else:
                pidx = _parent_index(view, selected)
                if pidx is not None:
                    selected = pidx
        elif ch == ord("s"):
            exact = not exact
        elif ch == ord("r"):
            return "rescan"


def _barw(w: int) -> int:
    return max(8, min(24, w - 64))


def _wlen(s: str) -> int:
    return len(s)


def _ellip(s: str, width: int) -> str:
    if width <= 1:
        return ""
    if len(s) <= width:
        return s
    return "…" + s[-(width - 1):]


def _parent_index(view: TreeView, idx: int) -> Optional[int]:
    depth = view.rows[idx].depth
    for j in range(idx - 1, -1, -1):
        if view.rows[j].depth < depth:
            return j
    return None


def _parent_size(view: TreeView, idx: int) -> int:
    pj = _parent_index(view, idx)
    if pj is None:
        return view.rows[idx].node.size or 1
    return view.rows[pj].node.size or 1


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def run_tui(path: str, workers: Optional[int] = None) -> None:
    """Scan ``path`` and launch the interactive terminal browser."""

    def _main(stdscr) -> None:
        while True:
            root = _scan_with_progress(stdscr, path, workers)
            if root is None:
                return
            view = TreeView(root)
            action = _browse(stdscr, view, path)
            if action == "quit":
                return
            # action == "rescan" -> loop and scan again

    curses.wrapper(_main)
