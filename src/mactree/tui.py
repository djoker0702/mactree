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
    stdscr.nodelay(True)
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    i = 0
    while t.is_alive():
        p = scanner.progress
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        title = "🌳 MacTree — scanning"
        stdscr.addnstr(1, 2, title, w - 4, curses.A_BOLD)
        stdscr.addnstr(3, 2, f"{spinner[i % len(spinner)]}  {path}", w - 4)
        stdscr.addnstr(
            5, 2,
            f"folders: {p.dirs:>8}    files: {p.files:>8}", w - 4,
        )
        stdscr.addnstr(
            6, 2,
            f"size:    {human(p.bytes):>8}    elapsed: {p.elapsed:>6.1f}s", w - 4,
        )
        stdscr.addnstr(h - 2, 2, "scanning… (Ctrl-C to abort)", w - 4, curses.A_DIM)
        stdscr.refresh()
        i += 1
        # allow abort
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
    stdscr.addnstr(h // 2, 2, msg, w - 4, curses.A_BOLD)
    stdscr.addnstr(h // 2 + 2, 2, "press any key…", w - 4, curses.A_DIM)
    stdscr.refresh()
    stdscr.getch()


# --------------------------------------------------------------------------
# Main browser loop
# --------------------------------------------------------------------------
def _color_for_fraction(frac: float) -> int:
    """Pick a color pair id based on how big an entry is relative to parent."""
    if frac >= 0.5:
        return 1  # red-ish: huge
    if frac >= 0.2:
        return 2  # yellow: large
    if frac >= 0.05:
        return 3  # green: medium
    return 4      # cyan/blue: small


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_YELLOW, -1)
    curses.init_pair(3, curses.COLOR_GREEN, -1)
    curses.init_pair(4, curses.COLOR_CYAN, -1)
    curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)  # selection
    curses.init_pair(6, curses.COLOR_BLUE, -1)                  # folders


def _browse(stdscr, view: TreeView, scan_path: str) -> str:
    """Run the interactive browser. Returns 'rescan' or 'quit'."""
    curses.curs_set(0)
    _init_colors()
    stdscr.keypad(True)

    selected = 0
    top = 0
    exact = False  # size display: human vs exact bytes

    while True:
        h, w = stdscr.getmaxyx()
        body_h = h - 3  # header + footer
        rows = view.rows

        # keep selection in range
        selected = max(0, min(selected, len(rows) - 1))
        if selected < top:
            top = selected
        elif selected >= top + body_h:
            top = selected - body_h + 1

        stdscr.erase()

        # Header
        root = view.root
        header = f" 🌳 MacTree   {scan_path}   total {human(root.size)} "
        stdscr.attron(curses.A_REVERSE | curses.A_BOLD)
        stdscr.addnstr(0, 0, header.ljust(w), w)
        stdscr.attroff(curses.A_REVERSE | curses.A_BOLD)

        # Rows
        for i in range(top, min(len(rows), top + body_h)):
            row = rows[i]
            node = row.node
            y = 1 + (i - top)
            is_sel = i == selected

            parent_size = _parent_size(view, i)
            frac = (node.size / parent_size) if parent_size else 0.0

            indent = "  " * row.depth
            if node.is_dir:
                marker = "▼ " if view.is_expanded(node) else "▶ "
            else:
                marker = "  "
            name = node.name + ("/" if node.is_dir else "")

            size_str = (f"{node.size:,}" if exact else human(node.size)).rjust(10)
            pct = f"{frac * 100:5.1f}%"
            bar_w = max(6, min(20, w - 60))
            bar_str = bar(frac, bar_w)

            # Compose: size | pct | bar | indent marker name
            left = f"{size_str}  {pct} ["
            right = f"] {indent}{marker}{name}"
            line = left + bar_str + right
            line = line[: w - 1].ljust(w - 1)

            if is_sel:
                stdscr.attron(curses.color_pair(5) | curses.A_BOLD)
                stdscr.addnstr(y, 0, line, w - 1)
                stdscr.attroff(curses.color_pair(5) | curses.A_BOLD)
            else:
                # color the size portion by fraction, folder names in blue
                cpair = curses.color_pair(_color_for_fraction(frac))
                stdscr.attron(cpair)
                stdscr.addnstr(y, 0, left + bar_str + "]", w - 1)
                stdscr.attroff(cpair)
                namex = len(left + bar_str + "]")
                if namex < w - 1:
                    attr = curses.color_pair(6) | curses.A_BOLD if node.is_dir else 0
                    stdscr.attron(attr)
                    stdscr.addnstr(y, namex, f" {indent}{marker}{name}", w - 1 - namex)
                    stdscr.attroff(attr)

        # Footer / help
        pos = f"{selected + 1}/{len(rows)}"
        help_txt = " ↑↓ move   →/⏎ expand   ← collapse   g/G top/bottom   s units   r rescan   q quit "
        footer = f"{help_txt}  {pos} "
        stdscr.attron(curses.A_REVERSE)
        stdscr.addnstr(h - 1, 0, footer.ljust(w)[: w - 1], w - 1)
        stdscr.attroff(curses.A_REVERSE)

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
        elif ch in (curses.KEY_NPAGE,):
            selected += body_h
        elif ch in (curses.KEY_PPAGE,):
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
                    selected += 1  # step into first child
        elif ch in (curses.KEY_LEFT, ord("h")):
            node = rows[selected].node
            if node.is_dir and view.is_expanded(node):
                view.collapse(node)
            else:
                # jump to parent row
                parent_idx = _parent_index(view, selected)
                if parent_idx is not None:
                    selected = parent_idx
        elif ch == ord("s"):
            exact = not exact
        elif ch == ord("r"):
            return "rescan"


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
