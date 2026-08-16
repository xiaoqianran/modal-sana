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
    image_format: str = "webp"
    quality: int = 90
    status: str = Field(default="pending", index=True)
    attempt: int = 0
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    latency_ms: float | None = None
    task_hash: str = Field(index=True)


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


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(self.engine)
        self._lock = threading.RLock()

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
