"""FastAPI backend for MacTree.

Endpoints
---------
POST /api/scan        -> start a scan job for a path; returns a job id
GET  /api/scan/{id}   -> progress snapshot (and result when done)
GET  /api/tree/{id}   -> serialized (sub)tree, optionally rooted at ?path=
GET  /api/home        -> the user's home directory (UI default)
GET  /                -> the single-page frontend
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .scanner import Node, Scanner
from .serialize import find_node, serialize

STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class Job:
    id: str
    path: str
    scanner: Scanner
    thread: threading.Thread
    root: Optional[Node] = None
    error: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class ScanRequest(BaseModel):
    path: str
    workers: Optional[int] = None
    use_allocated: bool = True


_jobs: Dict[str, Job] = {}
_jobs_lock = threading.Lock()


def create_app(default_workers: Optional[int] = None) -> FastAPI:
    app = FastAPI(title="MacTree", version="0.1.0")

    def _run_scan(job: Job) -> None:
        try:
            root = job.scanner.scan(job.path)
            with job.lock:
                job.root = root
        except Exception as exc:  # noqa: BLE001
            with job.lock:
                job.error = str(exc)

    @app.post("/api/scan")
    def start_scan(req: ScanRequest) -> dict:
        path = os.path.abspath(os.path.expanduser(req.path))
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail=f"Path not found: {path}")
        if not os.path.isdir(path):
            raise HTTPException(status_code=400, detail="Path is not a directory")

        job_id = uuid.uuid4().hex[:12]
        scanner = Scanner(
            workers=req.workers or default_workers,
            use_allocated=req.use_allocated,
        )
        thread = threading.Thread(
            target=lambda: _run_scan(_jobs[job_id]), daemon=True
        )
        job = Job(id=job_id, path=path, scanner=scanner, thread=thread)
        with _jobs_lock:
            _jobs[job_id] = job
        thread.start()
        return {"id": job_id, "path": path}

    @app.get("/api/scan/{job_id}")
    def scan_status(job_id: str) -> dict:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id")
        p = job.scanner.progress
        with job.lock:
            error = job.error or p.error
            ready = job.root is not None
        return {
            "id": job_id,
            "path": job.path,
            "dirs": p.dirs,
            "files": p.files,
            "bytes": p.bytes,
            "elapsed": p.elapsed,
            "done": ready or error is not None,
            "ready": ready,
            "error": error,
        }

    @app.get("/api/tree/{job_id}")
    def get_tree(job_id: str, path: Optional[str] = Query(default=None)) -> dict:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job id")
        with job.lock:
            root = job.root
        if root is None:
            raise HTTPException(status_code=409, detail="Scan not finished")

        target = root
        if path and path != root.path:
            found = find_node(root, path)
            if found is None:
                raise HTTPException(status_code=404, detail="Path not in tree")
            target = found
        return serialize(target)

    @app.get("/api/home")
    def home_dir() -> dict:
        return {"home": str(Path.home())}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
