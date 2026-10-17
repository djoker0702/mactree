# 🌳 MacTree

A fast disk-usage analyzer for macOS (and Linux), inspired by **WinDirStat** and
**ncdu**. It scans a directory tree with a multi-threaded scanner and lets you
explore where your space went — either in an **interactive terminal UI** or an
**interactive browser UI** with a D3 treemap / sunburst.

```
python -m mactree              # terminal UI (default)
python -m mactree --web        # browser UI
```

---

## Features

- **Two frontends, one engine**
  - **TUI** (default): an `ncdu`-style terminal browser — expand/collapse folders,
    per-entry size bars, color-coded by how big each item is, keyboard-driven.
  - **Web**: a nested, animated **treemap** and **sunburst** drawn with D3.js,
    with a live metadata inspector, hover highlighting, zoom, and breadcrumbs.
- **Fast multi-threaded scanning** — a work-queue thread pool walks the tree with
  `os.scandir`. The `scandir`/`stat` syscalls release the GIL while blocked on
  I/O, so threads give real speedups on disk-bound workloads. Each directory is a
  unit of work; subdirectories are pushed back onto the queue so every worker
  stays busy even on lopsided trees.
- **Live progress** while scanning (folders / files / bytes / elapsed).
- **Residual dev-artifact cleanup** (TUI) — press `c` to scan the results for
  known regenerable junk (`node_modules`, `.terraform`, `.npm`, `.yarn`,
  `__pycache__`, `target`, `.venv`, caches, build dirs …), tick items with
  `[ ]`/`[x]`, and delete the selected ones (with a confirmation prompt) to
  reclaim space. Ambiguous names (`build`/`dist`/`vendor`/`venv`) are flagged ⚠,
  and deletions are guarded to stay inside the scanned directory.
- **Accurate sizes** — uses allocated blocks (`st_blocks * 512`), i.e. "size on
  disk", like WinDirStat. Symlinks are not followed, to avoid cycles.
- **Zero heavy deps for the terminal UI** — the TUI uses only the standard
  library (`curses`). The web UI adds FastAPI + Uvicorn.

---

## Install

Requires Python 3.9+.

```bash
git clone <your-repo-url> mactree
cd mactree

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Or install as a package (provides a `mactree` command):

```bash
pip install -e .
```

---

## Usage

### Terminal UI (default)

```bash
python -m mactree                 # scan your home directory
python -m mactree ~/Downloads     # scan a specific path
python -m mactree / --workers 32  # scan the whole disk with 32 scan threads
```

**Keys**

The TUI has two views — a **list** (tree) view and a **grid** (treemap) view —
switchable at any time.

| Key | Action |
| --- | --- |
| `L` | switch to **list** (tree) view |
| `G` | switch to **grid** (treemap) view |
| `↑` / `↓` or `j` / `k` | move selection |
| `→` / `Enter` / `l` | expand folder / descend (list); in grid, `Enter` descends |
| `←` / `h` | collapse / jump to parent (list) |
| `Backspace` | go up a level (grid) |
| `g` / `Home` | jump to top (list) |
| `End` | jump to bottom (list) |
| `PgUp` / `PgDn` | page up / down (list) |
| `s` | toggle size units (human-readable ↔ exact bytes) |
| `c` | **clean**: find residual dev artifacts, tick `[ ]`/`[x]`, delete selected |
| `r` | rescan |
| `q` | quit |

In **list** view, each row shows the entry's size, its percentage of the parent
folder, and a gradient usage bar, with tree guide lines and file-type glyphs.
In **grid** view, the current folder's children are packed as proportionally
sized treemap tiles (bigger = more disk) — navigate with the arrows and press
`Enter` to zoom into a folder, `Backspace` to go back up.

Each row shows the entry's size, its percentage of the parent folder, and a bar.
Colors indicate relative size: red (huge) → yellow → green → cyan (small);
folders are shown in blue.

### Web UI

```bash
python -m mactree --web                        # http://127.0.0.1:8000
python -m mactree --web --port 8010            # pick another port
python -m mactree --web --host 0.0.0.0 --port 8080 --workers 32
```

Open the printed URL, type a path (the box is pre-filled with your home
directory), and click **Scan**. Then:

- **Treemap** / **Sunburst** toggle in the top-right.
- **Hover** any region to highlight its ancestor chain and populate the metadata
  inspector (size, share of parent, type, child count, full path).
- **Click** a folder to zoom in; use the **breadcrumbs** (or the sunburst center)
  to go back.

---

## CLI reference

```
python -m mactree [path] [--tui | --web] [--workers N]
                  [--host HOST] [--port PORT]
```

| Flag | Applies to | Description |
| --- | --- | --- |
| `path` | TUI | Directory to scan (default: home directory). |
| `--tui` | — | Interactive terminal UI (**default**). |
| `--web` | — | Browser UI (starts a local web server). |
| `--workers N` | both | Scanner thread-pool size (default: based on CPU count). |
| `--host` | web | Bind host (default `127.0.0.1`). |
| `--port` | web | Bind port (default `8000`). |

---

## How scanning works

Each directory is a unit of work pushed onto a shared queue. A pool of worker
threads pulls directories, calls `os.scandir`, records file sizes, and pushes
discovered subdirectories back onto the queue. Directory sizes are then
aggregated bottom-up. This keeps all worker threads busy even on highly
unbalanced trees, and parallelizes the many small, I/O-bound syscalls that
dominate scan time.

Because the workload is syscall/I/O-bound (not CPU-bound), a **thread** pool —
not processes — is the right tool: the kernel releases the GIL during the
blocking `scandir`/`stat` calls, so threads run in genuine parallel without
inter-process overhead. The pool is intentionally oversubscribed
(`min(32, cpu_count * 4)`) since threads spend most of their time waiting on disk.

---

## Project layout

```
src/mactree/
├── scanner.py      # multi-threaded disk scanner (Node tree + progress)
├── serialize.py    # tree pruning → compact JSON for the web UI
├── tui.py          # curses terminal UI (list + grid + artifact cleanup)
├── artifacts.py    # residual dev-artifact detection + guarded deletion
├── app.py          # FastAPI backend (scan jobs + API)
├── __main__.py     # CLI: python -m mactree
└── static/         # web frontend (index.html, style.css, app.js)
```

---

## Notes

- On recent macOS, scanning protected locations (Desktop, Documents, etc.) may be
  blocked by TCC privacy controls. MacTree skips what it can't read rather than
  failing. Grant your terminal **Full Disk Access** in *System Settings → Privacy
  & Security* for a complete scan.
- Sizes default to allocated blocks, so totals match Finder's "size on disk" and
  can differ from logical file sizes for sparse or compressed files.

## License

MIT
