from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from modal_sana.web.api import router

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="modal-sana", docs_url="/api/docs")
app.include_router(router)


def spa_index() -> FileResponse:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(500, "workbench index.html is missing")
    return FileResponse(index)


@app.get("/")
def index() -> FileResponse:
    return spa_index()


@app.get("/generate")
@app.get("/batch")
@app.get("/gallery")
@app.get("/jobs")
@app.get("/cost")
@app.get("/benchmark")
@app.get("/settings")
def spa_page() -> FileResponse:
    return spa_index()


@app.get("/job/{job_id}")
def spa_job(job_id: str) -> FileResponse:
    if not job_id.strip():
        raise HTTPException(404, "missing job")
    return spa_index()


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
