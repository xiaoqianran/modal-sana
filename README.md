# modal-sana

本地提交 Prompt、管理任务、浏览结果；[Modal](https://modal.com) 负责 GPU 推理。模型是 NVIDIA [SANA](https://nvlabs.github.io/Sana/Sprint/)，默认 **SANA-Sprint-1.6B**。

```text
Prompt → modal-sana Core → Modal GPU workers → SANA → 实时回传 → 本地 Gallery
```

CLI 和 Web 共用同一套 Core。第一版不包含桌面 EXE（以后用 Tauri 包这套 Web）。

## 架构（对照 Modal 官方能力之后的取舍）

设计文档的分层是对的：**Interface / Core / Modal 必须拆开**。对照 [Modal llms.txt](https://modal.com/llms.txt) 之后，有几处刻意没有“自己再造一轮云调度”：

| 需求 | 不这样做 | 实际做法 |
| --- | --- | --- |
| 万级 Prompt 并发 | 本地 for-loop 或自写分布式队列 | `SanaWorker.generate_batch.map(..., order_outputs=False)` |
| 换 GPU | 为每种卡写一个 Function | [`with_options(gpu=...)`](https://modal.com/docs/guide/dynamic-function-config) |
| `--workers 4` | 自己管进程池 | `with_options(max_containers=4)` |
| 模型权重 | GPU 容器里 `from_pretrained` 边下边加载 | **CPU** `prefetch_model` 写入 Volume `/cache/models/{id}` 并 `commit()`；**GPU** 只 `local_files_only` 加载。空闲 **10s** 后 GPU 容器释放 |
| 瞬时失败 | 只在本地重试 | Modal `retries=` + 本地 Job resume |
| 图片 | 塞进 SQLite Blob | SQLite 只存 metadata，文件在 `data/outputs/` |

本地 Scheduler 只做三件事：展开 Prompt×N、按分辨率/步数切 GPU batch、把结果落盘并推 SSE。真正的扩缩容交给 Modal。

每一张图都要能定位到 Modal 调用，并估出这一分钱花在哪：

```text
job.create
job.run
  ├─ modal.map                 wall clock of .map()
  │    └─ modal.generate       function_call_id + input_id + container
  │         load / infer / encode   GPU seconds × list $/s
  └─ persist.image             local disk + SQLite
```

`modal-sana trace JOB` 打调用树，`modal-sana cost JOB` 打每张图的 load/infer/encode 和美分级估价。Web 的 Job 详情页同一套数据。估价用 `src/modal_sana/modal/gpu.py` 的公开标价，**不含** image build CPU 和 scaledown 空转；对账用 `modal billing report`。

```text
CLI / Web
    ↓
JobService (SQLite + events)
    ↓
ImageGenerator protocol
    ├── MockGenerator   (--dry-run / 测试)
    └── ModalSanaGenerator
            ↓
        prefetch_model (CPU, Volume)
            ↓
        SanaWorker.with_options(gpu, max_containers).map()
```

第一版范围（有意收口）：

- 模型：SANA Sprint / SANA / SANA 1.5（默认 Sprint 1.6B）
- 入口：CLI + 本地 Web（`:7860`）
- 输入：单 Prompt、TXT、JSONL、JSON、CSV
- 能力：Job、batch、retry、resume、Gallery、SSE 进度
- 不做：Tauri EXE、FLUX、Prompt Matrix、Auto GPU

## 安装

```bash
uv sync
cp .env.example .env
uv run modal setup          # 只需一次，写入 Modal token
uv run modal-sana doctor
```

依赖是 `modal[api-proxy-support]`，本地 HTTP CONNECT / SOCKS 代理走标准环境变量：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
# 或 ALL_PROXY=socks5://127.0.0.1:1080
uv run modal-sana doctor          # 会显示 extra 是否装齐、当前代理
```

关掉代理：`MODAL_DISABLE_API_PROXY=1`。

没有 Modal 账号时，所有命令都可以加 `--dry-run`：本地生成带 Prompt 的占位图，用来跑通 Gallery / Job / Resume。

## CLI

```bash
# 单 Prompt
uv run modal-sana generate "a futuristic Tokyo street at night"

# 一张 Prompt 出 20 张（自动 seed+i）
uv run modal-sana generate "a cyberpunk cat" -n 20 --gpu L40S

# 批量。txt / jsonl / json / csv 都会识别
uv run modal-sana batch examples/prompts.jsonl --batch-size 8 --workers 4

# 任务
uv run modal-sana jobs
uv run modal-sana job job_01K...
uv run modal-sana trace job_01K...
uv run modal-sana cost job_01K...
uv run modal-sana resume job_01K...

# 本地工作台
uv run modal-sana web
```

默认：`sana-sprint-1.6b` · `L40S` · **该模型原生 1024×1024** · `2 steps` · `png`。换模型会改到该权重的原生分辨率（1.5 仍是 1024；4K 请选 `sana-1.6b-4k` → 4096²）。

```bash
uv run modal-sana models
uv run modal-sana prefetch                 # CPU 下载常用 1024 模型（不含 2K/4K）
uv run modal-sana prefetch sana-sprint-1.6b
uv run modal-sana prefetch --all
uv run modal-sana prefetch --status
uv run modal-sana gpus
uv run modal-sana benchmark --gpu L40S,RTX-PRO-6000 --count 8
```

本地 Web / CLI **不是** `modal serve`。查得到已 deploy 的 `SanaWorker` 就直接调用；**查不到就自己 `app.deploy()`**（和 `modal deploy -m modal_sana.modal.worker` 同一件事）。只有工作区 **deploy 名额满了、且没有 `modal-sana`** 才回退一次性 `app.run()`。

| 方式 | 谁启动 | 快照 |
| --- | --- | --- |
| **deployed**（默认） | 已有则 `from_name`；没有则自动 deploy | 有 |
| **ephemeral** `app.run()` | 仅名额满且没有本 App，或 `--ephemeral` | 无 |
| `modal serve` | 本仓库不用 | — |

```bash
uv run modal-sana generate "a white cat"   # 没有 deployed app 时会自动 deploy
uv run modal-sana web

# 强制一次性 ephemeral
uv run modal-sana generate "a white cat" --ephemeral
```

## Web

`uv run modal-sana web` 打开 `http://127.0.0.1:7860`。这是本地 FastAPI，不是 Modal 的 `serve`。GPU 默认打已 deploy 的 `modal-sana` App。

- **Generate** 单 Prompt
- **Batch** 拖文件或粘贴多行
- **Jobs** 状态 / Resume
- **Gallery** 分页、筛选、Hover Prompt、详情里复制 / 再生成 / 下载
- **Benchmark** GPU 价目与推荐 batch
- **Settings** `doctor` 结果

进度走 SSE：`image.completed` 一张一张进来，不会等整批结束才刷新。

## 数据

```text
data/
├── modal-sana.db          # Job / PromptTask / Generation / Image / TraceSpan
└── outputs/
    └── job_01K.../
        ├── 000001.png
        └── metadata.jsonl
```

JSONL 是批量任务的标准协议：

```jsonl
{"prompt":"a beautiful forest"}
{"prompt":"a white cat","count":4}
{"id":"tokyo","prompt":"an astronaut in Tokyo","seed":12345}
```

## 开发

```bash
uv sync --extra dev
uv run pytest
```

Modal 相关代码只在 `src/modal_sana/modal/`。Core 只依赖 `ImageGenerator`，测试用 `MockGenerator`，不需要 GPU 或 token。

Agent 技能来自 [modal-auto-research-skills](https://github.com/modal-projects/modal-auto-research-skills.git)，已放到 `.claude/skills/`。见 `AGENTS.md`。
