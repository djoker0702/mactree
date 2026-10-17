"""Multi-threaded disk usage scanner.

The scanner walks a directory tree using a shared work queue and a pool of
worker threads. Each unit of work is a single directory: a worker calls
``os.scandir`` on it, records the sizes of contained files, and pushes any
discovered subdirectories back onto the queue for other workers to pick up.

This design parallelizes the many ``scandir``/``stat`` syscalls that dominate
disk-scan time. Those syscalls release the GIL while blocked on I/O, so a thread
pool gives real speedups on I/O-bound trees without the overhead of processes.

After the walk, directory sizes are aggregated bottom-up so each node reports the
total size of everything beneath it.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from queue import Queue
from typing import Optional


@dataclass
class Node:
    """A node in the scanned tree (file or directory)."""

    name: str
    path: str
    is_dir: bool
    # size: for files, the file's own size; for dirs, aggregated total (filled later)
    size: int = 0
    children: list["Node"] = field(default_factory=list)
    # own_size: bytes directly in this dir's files (not subdirs); 0 for files
    own_size: int = 0


@dataclass
class Progress:
    """Thread-safe-ish snapshot of scan progress (ints are read atomically)."""

    dirs: int = 0
    files: int = 0
    bytes: int = 0
    done: bool = False
    error: Optional[str] = None
    elapsed: float = 0.0


class Scanner:
    """A multi-threaded directory scanner.

    Parameters
    ----------
    workers:
        Number of worker threads. Defaults to ``min(32, (os.cpu_count() or 4) * 4)``
        because the workload is I/O-bound and benefits from oversubscription.
    use_allocated:
        If True (default), use allocated block size (``st_blocks * 512``) like
        WinDirStat's "size on disk". If False, use logical file size.
    follow_symlinks:
        If False (default), symlinked directories are recorded but not descended
        into, avoiding cycles and double-counting.
    """

    def __init__(
        self,
        workers: Optional[int] = None,
        use_allocated: bool = True,
        follow_symlinks: bool = False,
    ) -> None:
        self.workers = workers or min(32, (os.cpu_count() or 4) * 4)
        self.use_allocated = use_allocated
        self.follow_symlinks = follow_symlinks

        self._queue: "Queue[Optional[Node]]" = Queue()
        self._lock = threading.Lock()
        self._progress = Progress()
        self._outstanding = 0  # directories queued but not yet processed
        self._outstanding_cv = threading.Condition(self._lock)
        self._start_time = 0.0

    # -- public API ---------------------------------------------------------

    @property
    def progress(self) -> Progress:
        with self._lock:
            p = Progress(
                dirs=self._progress.dirs,
                files=self._progress.files,
                bytes=self._progress.bytes,
                done=self._progress.done,
                error=self._progress.error,
            )
        if self._start_time:
            end = self._start_time + p.elapsed if p.done else time.monotonic()
            p.elapsed = round(end - self._start_time, 3)
        return p

    def scan(self, root_path: str) -> Node:
        """Scan ``root_path`` and return the populated root :class:`Node`.

        Blocks until the whole tree has been walked and aggregated.
        """
        root_path = os.path.abspath(os.path.expanduser(root_path))
        if not os.path.exists(root_path):
            raise FileNotFoundError(f"Path does not exist: {root_path}")
        if not os.path.isdir(root_path):
            # A single file: trivially the whole result.
            size = self._entry_size(os.stat(root_path))
            node = Node(
                name=os.path.basename(root_path) or root_path,
                path=root_path,
                is_dir=False,
                size=size,
                own_size=size,
            )
            with self._lock:
                self._progress.files = 1
                self._progress.bytes = size
                self._progress.done = True
            return node

        self._start_time = time.monotonic()
        root = Node(
            name=os.path.basename(root_path.rstrip(os.sep)) or root_path,
            path=root_path,
            is_dir=True,
        )

        # Seed the queue with the root directory.
        with self._lock:
            self._outstanding = 1
        self._queue.put(root)

        threads = [
            threading.Thread(target=self._worker, name=f"scan-{i}", daemon=True)
            for i in range(self.workers)
        ]
        for t in threads:
            t.start()

        # Wait until all queued directories have been processed.
        with self._outstanding_cv:
            while self._outstanding > 0 and self._progress.error is None:
                self._outstanding_cv.wait()

        # Signal workers to exit by enqueuing sentinels.
        for _ in threads:
            self._queue.put(None)
        for t in threads:
            t.join(timeout=5)

        # Aggregate sizes bottom-up.
        self._aggregate(root)

        with self._lock:
            self._progress.done = True
            self._progress.elapsed = round(time.monotonic() - self._start_time, 3)
        return root

    # -- internals ----------------------------------------------------------

    def _entry_size(self, st: os.stat_result) -> int:
        if self.use_allocated:
            # st_blocks is in 512-byte units (POSIX). Falls back to st_size if
            # unavailable (shouldn't happen on macOS).
            blocks = getattr(st, "st_blocks", None)
            if blocks is not None:
                return blocks * 512
        return st.st_size

    def _worker(self) -> None:
        while True:
            node = self._queue.get()
            if node is None:  # sentinel
                self._queue.task_done()
                return
            try:
                self._scan_dir(node)
            except Exception as exc:  # noqa: BLE001 - record and keep going
                with self._lock:
                    if self._progress.error is None:
                        self._progress.error = f"{node.path}: {exc}"
            finally:
                self._queue.task_done()
                with self._outstanding_cv:
                    self._outstanding -= 1
                    if self._outstanding == 0:
                        self._outstanding_cv.notify_all()

    def _scan_dir(self, node: Node) -> None:
        """Scan a single directory: record files, queue subdirectories."""
        local_files = 0
        local_bytes = 0
        own_size = 0
        new_dirs: list[Node] = []

        try:
            with os.scandir(node.path) as it:
                for entry in it:
                    try:
                        is_dir = entry.is_dir(follow_symlinks=self.follow_symlinks)
                    except OSError:
                        continue

                    if is_dir:
                        child = Node(
                            name=entry.name,
                            path=entry.path,
                            is_dir=True,
                        )
                        node.children.append(child)
                        new_dirs.append(child)
                    else:
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        size = self._entry_size(st)
                        node.children.append(
                            Node(
                                name=entry.name,
                                path=entry.path,
                                is_dir=False,
                                size=size,
                                own_size=size,
                            )
                        )
                        local_files += 1
                        local_bytes += size
                        own_size += size
        except (PermissionError, OSError):
            # Can't read this directory; leave it empty.
            pass

        node.own_size = own_size

        # Publish progress and enqueue discovered subdirectories.
        with self._outstanding_cv:
            self._progress.dirs += 1
            self._progress.files += local_files
            self._progress.bytes += local_bytes
            self._outstanding += len(new_dirs)
        for child in new_dirs:
            self._queue.put(child)

    def _aggregate(self, node: Node) -> int:
        """Compute total size of ``node`` as own files + all descendants."""
        if not node.is_dir:
            return node.size
        total = node.own_size
        for child in node.children:
            total += self._aggregate(child)
        node.size = total
        return total
