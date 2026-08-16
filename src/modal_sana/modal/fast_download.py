from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from modal_sana.modal.secrets import hf_token


def download_hf_repo(repo_id: str, dest: Path, token: str | None = None) -> str:
    """Download a Hugging Face repo as fast as this container allows.

    Order: ``hf`` / ``huggingface-cli`` (Xet) → aria2c 16-wide → snapshot_download.
    Tokens come from the argument or ``HF_TOKEN`` / ``HUGGING_FACE_HUB_TOKEN``.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    auth = token or hf_token()
    if _try_hf_cli(repo_id, dest, auth):
        return "hf-cli"
    if _try_aria2c(repo_id, dest, auth):
        return "aria2c"
    from huggingface_hub import snapshot_download

    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest),
        token=auth,
        max_workers=8,
    )
    return "snapshot_download"


def _try_hf_cli(repo_id: str, dest: Path, token: str | None) -> bool:
    cli = shutil.which("hf") or shutil.which("huggingface-cli")
    if not cli:
        return False
    env = os.environ.copy()
    if token:
        env["HF_TOKEN"] = token
        env["HUGGING_FACE_HUB_TOKEN"] = token
    if cli.endswith("huggingface-cli") or Path(cli).name == "huggingface-cli":
        cmd = [cli, "download", repo_id, "--local-dir", str(dest)]
    else:
        cmd = [cli, "download", repo_id, "--local-dir", str(dest)]
    print(f"hf download {repo_id} -> {dest}", flush=True)
    result = subprocess.run(cmd, env=env, check=False)
    return result.returncode == 0 and (dest / "model_index.json").is_file()


def _try_aria2c(repo_id: str, dest: Path, token: str | None) -> bool:
    if not shutil.which("aria2c"):
        return False
    try:
        from huggingface_hub import hf_hub_url, list_repo_files
    except Exception:
        return False
    try:
        files = [
            name
            for name in list_repo_files(repo_id, token=token)
            if name and not name.startswith(".") and "/.git" not in name
        ]
    except Exception as exc:
        print(f"aria2c: could not list {repo_id}: {exc}", flush=True)
        return False
    if not files:
        return False
    print(f"aria2c -x 16 -s 16 ({len(files)} files) {repo_id} -> {dest}", flush=True)
    errors = 0
    with ThreadPoolExecutor(max_workers=min(8, max(len(files), 1))) as pool:
        futures = [
            pool.submit(_aria2c_file, repo_id, name, dest, token)
            for name in files
        ]
        for future in as_completed(futures):
            if not future.result():
                errors += 1
    return errors == 0 and (dest / "model_index.json").is_file()


def _aria2c_file(repo_id: str, filename: str, dest: Path, token: str | None) -> bool:
    from huggingface_hub import hf_hub_url

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
    result = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"aria2c failed for {filename} (exit {result.returncode})", flush=True)
        return False
    return True
