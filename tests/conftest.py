from __future__ import annotations

from pathlib import Path

import pytest

from modal_sana.core.config import Settings
from modal_sana.core.events import EventBus
from modal_sana.core.jobs import JobService


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir)


@pytest.fixture
def service(settings: Settings) -> JobService:
    return JobService(settings, events=EventBus())
