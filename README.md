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

| Key | Action |
| --- | --- |
| `↑` / `↓` or `j` / `k` | move selection |
| `→` / `Enter` / `l` | expand folder / descend |
| `←` / `h` | collapse / jump to parent |
| `g` / `G` | jump to top / bottom |
| `PgUp` / `PgDn` | page up / down |
| `s` | toggle size units (human-readable ↔ exact bytes) |
| `r` | rescan |
| `q` | quit |

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
├── tui.py          # curses terminal UI
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
