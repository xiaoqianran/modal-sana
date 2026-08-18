# modal-sana

基于 **Modal GPU + NVIDIA SANA** 的本地 AI 图像生成工作台。

你只需要在本地提交提示词，`modal-sana` 会负责模型缓存、GPU 调度、批量生成、任务管理、实时进度、结果保存与费用追踪；真正的模型推理由 Modal 云端 GPU 完成。

```text
提示词 / 批量文件
      ↓
modal-sana 本地核心
      ↓
Modal GPU Worker
      ↓
NVIDIA SANA
      ↓
实时返回图片与运行信息
      ↓
本地图库 / 任务 / 费用记录
```

默认模型：**SANA-Sprint-1.6B**  
默认 GPU：**L40S**  
默认分辨率：**1024 × 1024**  
默认推理步数：**2 steps**

---

## 主要功能

### 单提示词生成

直接从命令行提交一句提示词：

```bash
uv run modal-sana generate "a futuristic Tokyo street at night"
```

### 批量生成

支持：

- TXT
- JSONL
- JSON
- CSV

例如：

```bash
uv run modal-sana batch examples/prompts.jsonl --batch-size 8 --workers 4
```

### 一次生成多张图片

```bash
uv run modal-sana generate "a cyberpunk cat" -n 20 --gpu L40S
```

系统会自动展开任务，并为每张图片分配对应 seed。

### 本地 Web 工作台

```bash
uv run modal-sana web
```

浏览器打开：

```text
http://127.0.0.1:7862
```

Web 页面包括：

- `/generate`：单提示词生成
- `/batch`：批量任务
- `/gallery`：图库
- `/jobs`：任务管理
- `/cost`：GPU 费用与调用链
- `/benchmark`：GPU 基准测试与推荐 batch
- `/settings`：本地环境与 Modal 状态

图片通过 **SSE 实时回传**。完成一张就显示一张，不需要等待整个批次结束。

### 任务恢复

批量生成过程中即使本地进程退出，也可以继续恢复已有任务：

```bash
uv run modal-sana resume job_01K...
```

### GPU 费用追踪

每张图片都可以追踪对应的 Modal 调用与 GPU 时间：

```bash
uv run modal-sana trace job_01K...
uv run modal-sana cost job_01K...
```

调用关系大致如下：

```text
job.create
job.run
  ├─ modal.map
  │    └─ modal.generate
  │         ├─ load
  │         ├─ infer
  │         └─ encode
  └─ persist.image
```

`cost` 会根据项目中的 GPU 公开价格估算 load / infer / encode 阶段成本。

> 该估算主要用于理解单张图片和单个任务的 GPU 消耗，不包含 image build CPU、scaledown 空转等所有 Modal 账单项目。最终账单请以 Modal Billing 为准。

---

## 为什么这样设计

`modal-sana` 不重新实现一套云端调度系统，而是尽可能直接利用 Modal 已经提供的能力。

| 需求 | modal-sana 的实现 |
| --- | --- |
| 大量 Prompt 并发 | `SanaWorker.generate_batch.map(..., order_outputs=False)` |
| 动态切换 GPU | `with_options(gpu=...)` |
| 限制并发 Worker | `with_options(max_containers=...)` |
| 模型权重缓存 | CPU 预下载到 Modal Volume |
| GPU 加载模型 | `local_files_only`，不在 GPU 容器重新下载 |
| 瞬时失败 | Modal retries + 本地任务恢复 |
| 图片持久化 | 图片保存到文件系统，SQLite 只保存元数据 |
| 实时进度 | SSE |

本地 Scheduler 主要负责：

1. 展开 `Prompt × 数量`
2. 按分辨率、步数等参数组织 GPU batch
3. 保存结果
4. 推送实时事件

真正的 GPU 扩缩容和远程执行交给 Modal。

---

## 项目架构

```text
CLI / Web
    ↓
JobService
SQLite + Events
    ↓
ImageGenerator Protocol
    ├── MockGenerator
    │     └── --dry-run / 测试
    │
    └── ModalSanaGenerator
          ↓
      prefetch_model
      prefetch_progress
          ↓
      Modal Volume
          ↓
      SanaWorker
          ↓
      Modal GPU
```

整体分为三层：

```text
Interface
CLI / Web

Core
Job / Scheduler / Storage / Events

Modal
Model Cache / Worker / GPU Runtime
```

CLI 和 Web 共用同一套 Core，不维护两套任务逻辑。

---

## 支持的模型范围

当前主要支持：

- SANA Sprint
- SANA
- SANA 1.5

默认使用：

```text
sana-sprint-1.6b
```

查看可用模型：

```bash
uv run modal-sana models
```

默认模型原生分辨率为：

```text
1024 × 1024
```

切换模型时会使用该权重对应的原生分辨率。

例如 4K 模型：

```text
sana-1.6b-4k → 4096 × 4096
```

---

## 安装

要求：

- Python 3.12+
- uv
- Modal 账号

安装依赖：

```bash
uv sync
```

复制环境变量：

```bash
cp .env.example .env
```

首次配置 Modal：

```bash
uv run modal setup
```

检查环境：

```bash
uv run modal-sana doctor
```

---

## 代理配置

项目依赖：

```text
modal[api-proxy-support]
```

支持标准 HTTP CONNECT / SOCKS 环境变量。

HTTP 代理：

```bash
export HTTPS_PROXY=http://127.0.0.1:7890
```

SOCKS5：

```bash
export ALL_PROXY=socks5://127.0.0.1:1080
```

检查代理状态：

```bash
uv run modal-sana doctor
```

关闭 Modal API Proxy：

```bash
MODAL_DISABLE_API_PROXY=1
```

---

## 没有 Modal 账号也可以测试

所有主要命令都可以使用：

```bash
--dry-run
```

例如：

```bash
uv run modal-sana generate "a white cat" --dry-run
```

此模式不会使用真实 GPU，而是在本地生成带 Prompt 信息的占位图片，用于测试：

- Job
- Gallery
- Resume
- Web UI
- 本地数据流程

---

## CLI 使用

### 单张生成

```bash
uv run modal-sana generate "a futuristic Tokyo street at night"
```

### 一次生成多张

```bash
uv run modal-sana generate "a cyberpunk cat" -n 20 --gpu L40S
```

### 批量 Prompt

```bash
uv run modal-sana batch examples/prompts.jsonl --batch-size 8 --workers 4
```

### 查看任务

```bash
uv run modal-sana jobs
```

### 查看单个任务

```bash
uv run modal-sana job job_01K...
```

### 查看调用链

```bash
uv run modal-sana trace job_01K...
```

### 查看费用

```bash
uv run modal-sana cost job_01K...
```

### 恢复任务

```bash
uv run modal-sana resume job_01K...
```

### 启动 Web

```bash
uv run modal-sana web
```

---

## 模型预下载

为了避免 GPU 容器启动后再下载模型，项目会优先使用 CPU Function 将模型缓存到 Modal Volume。

下载常用 1024 模型：

```bash
uv run modal-sana prefetch
```

下载指定模型：

```bash
uv run modal-sana prefetch sana-sprint-1.6b
```

下载全部模型，包括 2K / 4K：

```bash
uv run modal-sana prefetch --all
```

查看缓存状态：

```bash
uv run modal-sana prefetch --status
```

已完整下载的快照会直接跳过，不重复下载。

GPU Worker 加载模型时只读取本地 Volume 中的文件。

---

## GPU 与 Benchmark

查看 GPU：

```bash
uv run modal-sana gpus
```

运行基准测试：

```bash
uv run modal-sana benchmark --gpu L40S,RTX-PRO-6000 --count 8
```

Benchmark 页面和 CLI 可以用于比较不同 GPU 的：

- 生成速度
- 单位成本
- 推荐 batch
- 吞吐量

---

## Modal 运行方式

本地 Web / CLI **不是** `modal serve`。

正常情况下：

1. 查找已经部署的 `modal-sana`
2. 找到则直接 `from_name` 调用
3. 找不到则自动 `app.deploy()`
4. 如果工作区 deploy 名额已满且不存在本 App，则回退到一次性 `app.run()`

| 模式 | 行为 | 是否保留部署快照 |
| --- | --- | --- |
| deployed | 默认模式，已有则调用，没有则自动 deploy | 是 |
| ephemeral | 一次性 `app.run()` | 否 |
| modal serve | 本项目不使用 | — |

普通运行：

```bash
uv run modal-sana generate "a white cat"
```

强制 ephemeral：

```bash
uv run modal-sana generate "a white cat" --ephemeral
```

---

## Web 工作台

启动：

```bash
uv run modal-sana web
```

访问：

```text
http://127.0.0.1:7862
```

### 生成

```text
/generate
```

单 Prompt 生图。

### 批量

```text
/batch
```

支持拖入文件或直接粘贴多行 Prompt。

### 图库

```text
/gallery
```

支持：

- 分页
- 筛选
- 图片卡片
- 查看 Prompt
- 复制
- 再生成
- 下载

### 任务

```text
/jobs
```

查看任务状态，并对失败或中断任务执行 Resume。

### 费用

```text
/cost
```

查看：

- GPU 类型
- GPU 单价
- 调用链
- load / infer / encode 时间
- 单张图片估算费用

### Benchmark

```text
/benchmark
```

查看 GPU 性能与推荐 batch。

### 设置

```text
/settings
```

查看 `doctor` 环境检测结果。

---

## 批量输入格式

推荐使用 JSONL 作为批量任务的标准协议。

```jsonl
{"prompt":"a beautiful forest"}
{"prompt":"a white cat","count":4}
{"id":"tokyo","prompt":"an astronaut in Tokyo","seed":12345}
```

也支持 TXT、JSON 和 CSV。

---

## 本地数据

```text
data/
├── modal-sana.db
└── outputs/
    └── job_01K.../
        ├── 000001.png
        └── metadata.jsonl
```

SQLite 主要保存：

- Job
- PromptTask
- Generation
- Image
- TraceSpan

图片文件本身保存在：

```text
data/outputs/
```

不会把大量图片二进制数据直接塞入 SQLite Blob。

---

## 开发

安装开发依赖：

```bash
uv sync --extra dev
```

运行测试：

```bash
uv run pytest
```

Modal 相关实现集中在：

```text
src/modal_sana/modal/
```

Core 只依赖统一的 `ImageGenerator` 接口。

测试环境使用：

```text
MockGenerator
```

因此大多数核心测试不需要：

- GPU
- Modal Token
- 真实模型权重

---

## 当前范围

当前版本重点是把 **SANA + Modal + 本地任务工作台** 这一条链路做好。

已经包含：

- CLI
- 本地 Web
- 单 Prompt
- 批量 Prompt
- 多图片生成
- Job
- Batch
- Retry
- Resume
- Gallery
- SSE 实时进度
- Modal Volume 模型缓存
- GPU Benchmark
- Trace
- Cost 估算
- Dry Run

暂不包含：

- Tauri 桌面 EXE
- FLUX
- Prompt Matrix
- Auto GPU

未来如果需要桌面版本，可以基于当前 Web 工作台继续使用 Tauri 封装，而不需要重写 Core。

---

## Agent Skills

项目中的 Agent 技能来自：

`modal-projects/modal-auto-research-skills`

已经放入：

```text
.claude/skills/
```

更多说明见：

```text
AGENTS.md
```

---

## License

Apache-2.0
