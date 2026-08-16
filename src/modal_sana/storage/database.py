from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlmodel import Field, Session, SQLModel, col, create_engine, select

from modal_sana.schemas.job import JobConfig, JobSummary


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobRow(SQLModel, table=True):
    __tablename__ = "jobs"

    id: str = Field(primary_key=True)
    status: str = Field(index=True)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    config_json: str
    total_images: int = 0
    completed_images: int = 0
    failed_images: int = 0
    error: str | None = None
    gpu_seconds: float | None = None
    cost_usd: float | None = None
    modal_app_id: str | None = None
    modal_run_url: str | None = None

    def config(self) -> JobConfig:
        return JobConfig.model_validate_json(self.config_json)

    def summary(self) -> JobSummary:
        cfg = self.config()
        return JobSummary(
            id=self.id,
            status=self.status,  # type: ignore[arg-type]
            created_at=self.created_at,
            updated_at=self.updated_at,
            started_at=self.started_at,
            completed_at=self.completed_at,
            model=cfg.model,
            gpu=cfg.gpu,
            total_images=self.total_images,
            completed_images=self.completed_images,
            failed_images=self.failed_images,
            dry_run=cfg.dry_run,
            error=self.error,
            config=cfg,
            gpu_seconds=self.gpu_seconds,
            cost_usd=self.cost_usd,
            modal_app_id=self.modal_app_id,
            modal_run_url=self.modal_run_url,
        )


class PromptTaskRow(SQLModel, table=True):
    __tablename__ = "prompt_tasks"

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    prompt: str
    negative_prompt: str = ""
    count: int = 1
    status: str = "pending"
    source_id: str | None = None
    config_json: str = "{}"


class GenerationRow(SQLModel, table=True):
    __tablename__ = "generations"

    id: str = Field(primary_key=True)
    job_id: str = Field(index=True)
    prompt_task_id: str = Field(index=True)
    prompt: str
    negative_prompt: str = ""
    seed: int
    model: str
    gpu: str
    steps: int
    guidance: float
    width: int
    height: int
    image_format: str = "png"
    quality: int = 90
    status: str = Field(default="pending", index=True)
    attempt: int = 0
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: float | None = None
    task_hash: str = Field(index=True)
    modal_function_call_id: str | None = Field(default=None, index=True)
    modal_input_id: str | None = Field(default=None, index=True)
    gpu_seconds: float | None = None
    cost_usd: float | None = None
    load_ms: float | None = None
    infer_ms: float | None = None
    encode_ms: float | None = None
    vram_allocated_mb: float | None = None
    vram_reserved_mb: float | None = None
    vram_peak_mb: float | None = None
    extra_json: str | None = None


class ImageRow(SQLModel, table=True):
    __tablename__ = "images"

    id: str = Field(primary_key=True)
    generation_id: str = Field(index=True)
    job_id: str = Field(index=True)
    prompt_task_id: str = Field(index=True)
    path: str
    width: int
    height: int
    format: str
    byte_size: int
    created_at: datetime


class TraceSpanRow(SQLModel, table=True):
    __tablename__ = "trace_spans"

    span_id: str = Field(primary_key=True)
    parent_span_id: str | None = Field(default=None, index=True)
    job_id: str = Field(index=True)
    generation_id: str | None = Field(default=None, index=True)
    name: str = Field(index=True)
    kind: str = "local"
    status: str = "ok"
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: float | None = None
    gpu: str | None = None
    model: str | None = None
    modal_function_call_id: str | None = Field(default=None, index=True)
    modal_input_id: str | None = None
    modal_app_id: str | None = None
    cost_usd: float | None = None
    extra_json: str = "{}"


_JOB_MIGRATIONS = {
    "gpu_seconds": "FLOAT",
    "cost_usd": "FLOAT",
    "modal_app_id": "VARCHAR",
    "modal_run_url": "VARCHAR",
}
_GENERATION_MIGRATIONS = {
    "modal_function_call_id": "VARCHAR",
    "modal_input_id": "VARCHAR",
    "gpu_seconds": "FLOAT",
    "cost_usd": "FLOAT",
    "load_ms": "FLOAT",
    "infer_ms": "FLOAT",
    "encode_ms": "FLOAT",
    "vram_allocated_mb": "FLOAT",
    "vram_reserved_mb": "FLOAT",
    "vram_peak_mb": "FLOAT",
    "extra_json": "TEXT",
}


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.engine)
        self._ensure_columns()
        self._lock = threading.RLock()

    def _ensure_columns(self) -> None:
        """SQLite create_all will not ADD columns to an existing file."""
        from sqlalchemy import inspect, text

        inspector = inspect(self.engine)
        wanted = {"jobs": _JOB_MIGRATIONS, "generations": _GENERATION_MIGRATIONS}
        with self.engine.begin() as conn:
            for table, columns in wanted.items():
                if table not in inspector.get_table_names():
                    continue
                existing = {col["name"] for col in inspector.get_columns(table)}
                for name, ddl in columns.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._lock:
            with Session(self.engine, expire_on_commit=False) as session:
                yield session
                session.commit()


def dump_json(data: Any) -> str:
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    return json.dumps(data, default=str)


def now() -> datetime:
    return _utcnow()


def list_jobs(session: Session, limit: int = 100) -> list[JobRow]:
    statement = select(JobRow).order_by(col(JobRow.created_at).desc()).limit(limit)
    return list(session.exec(statement))


def get_job(session: Session, job_id: str) -> JobRow | None:
    return session.get(JobRow, job_id)


def generations_for_job(session: Session, job_id: str) -> list[GenerationRow]:
    statement = select(GenerationRow).where(GenerationRow.job_id == job_id)
    return list(session.exec(statement))


def pending_generations(session: Session, job_id: str) -> list[GenerationRow]:
    statement = select(GenerationRow).where(
        GenerationRow.job_id == job_id,
        col(GenerationRow.status).in_(("pending", "failed", "retrying")),
    )
    return list(session.exec(statement))
