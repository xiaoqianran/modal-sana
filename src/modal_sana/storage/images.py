from __future__ import annotations

from pathlib import Path


class ImageStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def job_dir(self, job_id: str) -> Path:
        path = self.root / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def image_path(self, job_id: str, index: int, image_format: str) -> Path:
        name = f"{index:06d}.{image_format}"
        return self.job_dir(job_id) / name

    def write_image(self, job_id: str, index: int, image_format: str, data: bytes) -> Path:
        path = self.image_path(job_id, index, image_format)
        path.write_bytes(data)
        return path

    def metadata_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "metadata.jsonl"

    def append_metadata(self, job_id: str, line: str) -> None:
        path = self.metadata_path(job_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")
