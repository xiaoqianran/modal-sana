from __future__ import annotations

import sqlite3
from pathlib import Path

from modal_sana.storage.database import Database


def test_adds_observability_columns(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE jobs (
            id VARCHAR PRIMARY KEY,
            status VARCHAR,
            created_at DATETIME,
            updated_at DATETIME,
            started_at DATETIME,
            completed_at DATETIME,
            config_json VARCHAR,
            total_images INTEGER,
            completed_images INTEGER,
            failed_images INTEGER,
            error VARCHAR
        );
        CREATE TABLE generations (
            id VARCHAR PRIMARY KEY,
            job_id VARCHAR,
            prompt_task_id VARCHAR,
            prompt VARCHAR,
            negative_prompt VARCHAR,
            seed INTEGER,
            model VARCHAR,
            gpu VARCHAR,
            steps INTEGER,
            guidance FLOAT,
            width INTEGER,
            height INTEGER,
            image_format VARCHAR,
            quality INTEGER,
            status VARCHAR,
            attempt INTEGER,
            error VARCHAR,
            started_at DATETIME,
            completed_at DATETIME,
            latency_ms FLOAT,
            task_hash VARCHAR
        );
        """
    )
    conn.close()

    Database(path)
    check = sqlite3.connect(path)
    job_cols = {row[1] for row in check.execute("PRAGMA table_info(jobs)")}
    gen_cols = {row[1] for row in check.execute("PRAGMA table_info(generations)")}
    tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    check.close()

    assert "cost_usd" in job_cols
    assert "gpu_seconds" in job_cols
    assert "modal_app_id" in job_cols
    assert "modal_function_call_id" in gen_cols
    assert "infer_ms" in gen_cols
    assert "trace_spans" in tables
