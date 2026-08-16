"""Download Hugging Face snapshots without re-fetching complete files.

Progress is reported through ``on_progress`` so Modal generators can stream
bytes to the local CLI. A file is skipped when its local size already matches
the Hub listing. Incomplete files resume via aria2c ``-c``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from modal_sana.modal.secrets import hf_token

ProgressFn = Callable[[dict[str, Any]], None]


def download_hf_repo(
    repo_id: str,
    dest: Path,
    token: str | None = None,
    *,
    on_progress: ProgressFn | None = None,
) -> str:
    """Download a Hugging Face repo. Complete local files are left untouched."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    auth = token or hf_token()
    files = list_repo_files(repo_id, auth)
    if files:
        _download_listed(repo_id, dest, auth, files, on_progress)
        return "files"
    _emit(
        on_progress,
        status="listing-fallback",
        current="snapshot",
        files_done=0,
        files_total=0,
        bytes_done=_folder_bytes(dest),
        bytes_total=0,
    )
    if _try_hf_cli(repo_id, dest, auth, on_progress=on_progress):
        return "hf-cli"
    _run_with_dir_progress(dest, on_progress, lambda: _snapshot_download(repo_id, dest, auth))
    return "snapshot_download"


def _snapshot_download(repo_id: str, dest: Path, token: str | None) -> None:
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest),
        token=token,
        max_workers=8,
    )


def list_repo_files(hf_id: str, token: str | None) -> list[dict[str, Any]]:
    """Return downloadable files with sizes. Empty list if Hub listing fails."""
    try:
        from huggingface_hub import HfApi
    except Exception:
        return []
    try:
        api = HfApi(token=token)
        files: list[dict[str, Any]] = []
        for item in api.list_repo_tree(hf_id, recursive=True, repo_type="model"):
            path = getattr(item, "path", None)
            if not path or getattr(item, "type", "file") == "directory":
                continue
            if str(path).startswith(".") or "/.git" in str(path):
                continue
            files.append({"path": str(path), "size": int(getattr(item, "size", 0) or 0)})
        return files
    except Exception as exc:
        print(f"list_repo_tree failed for {hf_id}: {exc}", flush=True)
        return []


def _download_listed(
    repo_id: str,
    dest: Path,
    token: str | None,
    files: list[dict[str, Any]],
    on_progress: ProgressFn | None,
) -> None:
    total_bytes = sum(int(item["size"]) for item in files)
    done_bytes = 0
    files_done = 0
    files_total = len(files)

    def emit(status: str, current: str, extra_bytes: int = 0) -> None:
        _emit(
            on_progress,
            status=status,
            current=current,
            files_done=files_done,
            files_total=files_total,
            bytes_done=min(
                done_bytes + extra_bytes,
                total_bytes if total_bytes else done_bytes + extra_bytes,
            ),
            bytes_total=total_bytes,
        )

    emit("planning", "")
    for item in files:
        rel = str(item["path"])
        expected = int(item["size"] or 0)
        target = dest / rel
        if _local_complete(target, expected):
            done_bytes += expected if expected > 0 else target.stat().st_size
            files_done += 1
            print(f"skip {rel} (already {target.stat().st_size} bytes)", flush=True)
            emit("skip", rel)
            continue
        already = target.stat().st_size if target.is_file() else 0
        last_seen = already

        def on_bytes(size: int, *, _rel: str = rel, _already: int = already) -> None:
            nonlocal last_seen
            last_seen = size
            emit("downloading", _rel, extra_bytes=max(0, size - _already))

        print(f"download {rel} ({expected} bytes)", flush=True)
        emit("downloading", rel, extra_bytes=max(0, already))
        _download_one(repo_id, rel, dest, token, on_bytes)
        final = target.stat().st_size if target.is_file() else last_seen
        if expected > 0 and final != expected:
            print(f"size mismatch {rel}: got {final}, expected {expected}; retry", flush=True)
            if target.is_file():
                target.unlink()
            _download_one(repo_id, rel, dest, token, on_bytes)
            final = target.stat().st_size if target.is_file() else 0
        done_bytes += expected if expected > 0 else final
        files_done += 1
        emit("file-done", rel)
    emit("complete", "")


def _local_complete(path: Path, expected: int) -> bool:
    if not path.is_file() or path.stat().st_size <= 0:
        return False
    if expected > 0 and path.stat().st_size != expected:
        return False
    sibling_parts = (f"{path.name}.aria2", f"{path.name}.incomplete", f"{path.name}.tmp")
    if any((path.parent / name).exists() for name in sibling_parts):
        return False
    return True


def _download_one(
    repo_id: str,
    rel: str,
    dest: Path,
    token: str | None,
    on_bytes: Callable[[int], None],
) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    poller = threading.Thread(target=_poll_file, args=(target, stop, on_bytes), daemon=True)
    poller.start()
    try:
        if _aria2c_file(repo_id, rel, dest, token) and target.is_file() and target.stat().st_size > 0:
            return
        from huggingface_hub import hf_hub_download

        hf_hub_download(
            repo_id=repo_id,
            filename=rel,
            local_dir=str(dest),
            token=token,
        )
    finally:
        stop.set()
        poller.join(timeout=2)
        if target.is_file():
            on_bytes(target.stat().st_size)


def _aria2c_file(repo_id: str, filename: str, dest: Path, token: str | None) -> bool:
    if not shutil.which("aria2c"):
        return False
    try:
        from huggingface_hub import hf_hub_url
    except Exception:
        return False
    url = hf_hub_url(repo_id=repo_id, filename=filename, revision="main")
    out_dir = dest / Path(filename).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "aria2c",
        "-x",
        "16",
        "-s",
        "16",
        "-c",
        "-k",
        "1M",
        "--file-allocation=none",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--console-log-level=notice",
        "--summary-interval=5",
        "-d",
        str(out_dir),
        "-o",
        Path(filename).name,
        url,
    ]
    if token:
        cmd.extend(["--header", f"Authorization: Bearer {token}"])
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"aria2c failed for {filename} (exit {result.returncode})", flush=True)
        return False
    return True


def _try_hf_cli(
    repo_id: str,
    dest: Path,
    token: str | None,
    *,
    on_progress: ProgressFn | None = None,
) -> bool:
    cli = shutil.which("hf") or shutil.which("huggingface-cli")
    if not cli:
        return False
    env = os.environ.copy()
    if token:
        env["HF_TOKEN"] = token
        env["HUGGING_FACE_HUB_TOKEN"] = token
    cmd = [cli, "download", repo_id, "--local-dir", str(dest)]
    print(f"hf download {repo_id} -> {dest}", flush=True)

    def _run() -> None:
        result = subprocess.run(cmd, env=env, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"hf-cli exited {result.returncode}")

    try:
        _run_with_dir_progress(dest, on_progress, _run)
    except RuntimeError:
        return False
    return (dest / "model_index.json").is_file()


def _run_with_dir_progress(dest: Path, on_progress: ProgressFn | None, fn: Callable[[], None]) -> None:
    stop = threading.Event()

    def poll() -> None:
        while not stop.wait(0.4):
            _emit(
                on_progress,
                status="downloading",
                current="snapshot",
                files_done=0,
                files_total=0,
                bytes_done=_folder_bytes(dest),
                bytes_total=0,
            )

    poller = threading.Thread(target=poll, daemon=True)
    poller.start()
    try:
        fn()
    finally:
        stop.set()
        poller.join(timeout=2)
        _emit(
            on_progress,
            status="complete",
            current="",
            files_done=0,
            files_total=0,
            bytes_done=_folder_bytes(dest),
            bytes_total=0,
        )


def _poll_file(path: Path, stop: threading.Event, on_size: Callable[[int], None]) -> None:
    last = -1
    while not stop.wait(0.4):
        try:
            size = path.stat().st_size if path.is_file() else 0
        except OSError:
            size = 0
        if size != last:
            last = size
            on_size(size)
    try:
        size = path.stat().st_size if path.is_file() else 0
        if size != last:
            on_size(size)
    except OSError:
        pass


def _folder_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.name.endswith((".aria2", ".incomplete", ".tmp")):
            total += item.stat().st_size
    return total


def _emit(
    on_progress: ProgressFn | None,
    *,
    status: str,
    current: str,
    files_done: int,
    files_total: int,
    bytes_done: int,
    bytes_total: int,
) -> None:
    if on_progress is None:
        return
    on_progress(
        {
            "status": status,
            "current": current,
            "files_done": files_done,
            "files_total": files_total,
            "bytes_done": bytes_done,
            "bytes_total": bytes_total,
        }
    )


# Keep the name used by older call sites / tests.
download_repo = download_hf_repo
