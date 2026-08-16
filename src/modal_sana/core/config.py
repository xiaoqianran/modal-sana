from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MODAL_SANA_",
        env_file=".env",
        extra="ignore",
    )

    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 7860
    default_model: str = "sana-sprint-1.6b"
    default_gpu: str = "L40S"
    deployed: bool = False
    app_name: str = "modal-sana"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "modal-sana.db"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
