# Agent notes

## Modal skills

This repo vendors [modal-auto-research-skills](https://github.com/modal-projects/modal-auto-research-skills.git) under `.claude/skills/`. Use them for all GPU / Modal work:

- `modal-basic-skills` — app as a package, `modal deploy -m`, lazy `from_name()`, CLI
- `modal-gpu-dev` — interactive GPU sandboxes and profiling
- `modal-gpu-experiment` — volumes, secrets, retries, checkpoints
- `sub-agents` — parallel agents across GPUs

Refresh:

```bash
git clone https://github.com/modal-projects/modal-auto-research-skills.git /tmp/modal-auto-research-skills
cp -R /tmp/modal-auto-research-skills/{modal-basic-skills,modal-gpu-dev,modal-gpu-experiment,sub-agents} \
  .claude/skills/
```

## Architecture (do not reinvent Modal)

Local CLI/Web own jobs, prompts, SQLite metadata, and the gallery. Modal owns GPU scheduling.

- Fan-out: `SanaWorker.generate_batch.map(..., order_outputs=False, return_exceptions=True)`
- GPU / concurrency: `with_options(gpu=..., max_containers=...)`
- Warm weights: CPU `prefetch_model` writes `/cache/models/{id}` on Volume and `commit()`s; GPU `@enter` only `from_pretrained(..., local_files_only=True)`. `modal-sana prefetch` with no args downloads every model (aria2c / `hf` CLI / snapshot). Tokens: `HF_TOKEN`, `CIVITAI_TOKEN`, `GITHUB_TOKEN`.
- Deploy: `uv run modal deploy -m modal_sana.modal.worker` (registers prefetch + SanaWorker). Local web/CLI are **not** `modal serve`. Generate prefers the deployed app (snapshots); `--ephemeral` forces `app.run()`.

`SanaWorker` must **not** use `from __future__ import annotations` — `modal.parameter()` needs real types.

## Observability

Every Modal call should be localizable: `job_id` → `generation_id` → `modal_function_call_id` / `modal_input_id` → container. Persist spans in SQLite (`trace_spans`) and roll GPU seconds + estimated USD onto the job.

Cost is a **list-price estimate** (`src/modal_sana/modal/gpu.py` × charged GPU seconds). It does not include image-build CPU or scaledown idle. Compare with `modal billing report` when optimizing.

## Local proxy

Depend on `modal[api-proxy-support]`. The client honors `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` / `NO_PROXY`. Opt out with `MODAL_DISABLE_API_PROXY=1`.
