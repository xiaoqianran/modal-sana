const main = document.getElementById("main");
const lightbox = document.getElementById("lightbox");
const mastStatus = document.getElementById("mast-status");

const STATUS = {
  pending: "等待",
  running: "进行中",
  completed: "完成",
  failed: "失败",
  cancelled: "已取消",
};

const state = {
  meta: null,
  source: null,
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail);
  }
  if (response.headers.get("content-type")?.includes("text/event-stream")) {
    return response;
  }
  return response.json();
}

function defaultsFrom(meta) {
  const modelId = meta?.defaults?.model || "sana-sprint-1.6b";
  const model = (meta?.models || []).find((item) => item.id === modelId);
  return {
    model: modelId,
    gpu: meta?.defaults?.gpu || "L40S",
    count: 1,
    width: model?.native_width || 1024,
    height: model?.native_height || 1024,
    steps: "",
    guidance: "",
    seed: "",
    batch_size: model?.recommended_batch || 4,
    workers: meta?.defaults?.workers || 1,
    format: meta?.defaults?.image_format || "png",
    dry_run: false,
    prefer_deployed: meta?.defaults?.prefer_deployed !== false,
  };
}

function field(name, label, value, type = "text") {
  const id = `field-${name}`;
  if (type === "checkbox") {
    return `<label class="check" for="${id}"><span>${label}</span><input id="${id}" type="checkbox" name="${name}" ${value ? "checked" : ""} /></label>`;
  }
  const extra = type === "number" ? ` inputmode="numeric" min="0"` : "";
  return `<div class="field"><label for="${id}">${label}</label><input id="${id}" name="${name}" type="${type}" value="${value ?? ""}"${extra} /></div>`;
}

function select(name, label, options, value) {
  const id = `field-${name}`;
  const opts = options.map((item) => {
    const optId = item.id || item;
    const title = item.name || item.id || item;
    return `<option value="${optId}" ${optId === value ? "selected" : ""}>${title}</option>`;
  }).join("");
  return `<div class="field"><label for="${id}">${label}</label><select id="${id}" name="${name}">${opts}</select></div>`;
}

function gpuChoices(meta) {
  return (meta.gpus || []).map((gpu) => ({
    id: gpu.id,
    name: `${String(gpu.id).replaceAll("-", " ")} · ${gpu.vram_gb}GB · $${Number(gpu.usd_per_hour).toFixed(2)}/时`,
  }));
}

function modelChoices(meta) {
  return (meta.models || []).map((model) => ({
    id: model.id,
    name: `${model.name || model.id} · ${model.native_width || 1024}×${model.native_height || 1024}`,
  }));
}

function isFourK(model) {
  if (!model) return false;
  return Number(model.native_width) >= 4096 || String(model.id || "").includes("4k");
}

function applyModelNative(form, meta) {
  const model = (meta.models || []).find((item) => item.id === form.model?.value);
  if (!model) return;
  if (form.width) form.width.value = model.native_width || 1024;
  if (form.height) form.height.value = model.native_height || 1024;
  if (form.batch_size && model.recommended_batch) form.batch_size.value = model.recommended_batch;
  if (isFourK(model) && form.gpu) {
    form.gpu.value = model.recommended_gpu || "RTX-PRO-6000";
  }
}

function settingsGrid(d, meta) {
  return `
    <fieldset class="settings">
      <legend>机位</legend>
      <div class="dials">
        ${select("model", "模型", modelChoices(meta), d.model)}
        ${select("gpu", "GPU", gpuChoices(meta), d.gpu)}
        ${field("count", "张数", d.count, "number")}
        ${select("image_format", "格式", ["png", "jpg", "webp"], d.format)}
      </div>
    </fieldset>
    <details class="advanced">
      <summary>步数、尺寸、并行</summary>
      <div class="grid">
        ${field("width", "宽度", d.width, "number")}
        ${field("height", "高度", d.height, "number")}
        ${field("steps", "步数（空=模型默认）", d.steps)}
        ${field("guidance", "引导系数", d.guidance)}
        ${field("seed", "种子", d.seed)}
        ${field("batch_size", "GPU 批大小", d.batch_size, "number")}
        ${field("workers", "并行容器（1=只用一台 GPU）", d.workers, "number")}
      </div>
      ${field("dry_run", "空跑（不调用 Modal / GPU）", d.dry_run, "checkbox")}
      ${field("prefer_deployed", "优先已部署的 Modal 应用（内存快照）", d.prefer_deployed !== false, "checkbox")}
    </details>
  `;
}

function formPayload(form) {
  const data = new FormData(form);
  const num = (key) => {
    const value = data.get(key);
    return value === "" || value == null ? null : Number(value);
  };
  return {
    model: data.get("model"),
    gpu: data.get("gpu"),
    count: Number(data.get("count") || 1),
    width: Number(data.get("width") || 1024),
    height: Number(data.get("height") || 1024),
    steps: num("steps"),
    guidance: num("guidance"),
    seed: num("seed"),
    batch_size: Number(data.get("batch_size") || 4),
    workers: Number(data.get("workers") || 1),
    image_format: data.get("image_format") || "png",
    dry_run: data.get("dry_run") === "on",
    prefer_deployed: data.get("prefer_deployed") === "on",
  };
}

function jobPayload(form) {
  const payload = formPayload(form);
  payload.deployed = payload.prefer_deployed !== false;
  delete payload.prefer_deployed;
  return payload;
}

function formatRate(usdPerSecond) {
  if (usdPerSecond == null) return "—";
  return `$${Number(usdPerSecond).toFixed(6)}/s · $${(Number(usdPerSecond) * 3600).toFixed(2)}/时`;
}

function kindLabel(kind) {
  if (kind === "gpu_load") return "加载";
  if (kind === "gpu_generate") return "生成";
  return kind || "—";
}

function formatUsd(amount) {
  if (amount == null) return "—";
  const cents = amount * 100;
  if (amount < 0.01) return `$${amount.toFixed(6)} (${cents.toFixed(3)}¢)`;
  return `$${amount.toFixed(4)} (${cents.toFixed(2)}¢)`;
}

function formatMs(ms) {
  if (ms == null) return "—";
  return `${(ms / 1000).toFixed(3)}s`;
}

function formatVram(item) {
  const reserved = item?.vram_reserved_mb;
  const allocated = item?.vram_allocated_mb;
  const peak = item?.vram_peak_mb;
  const mb = reserved ?? allocated ?? peak;
  if (mb == null) return "—";
  const text = mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(0)} MB`;
  if (allocated != null && reserved != null && Math.abs(reserved - allocated) > 1) {
    const live = allocated >= 1024 ? `${(allocated / 1024).toFixed(2)} GB` : `${allocated.toFixed(0)} MB`;
    return `${text}（占用） / ${live} 张量`;
  }
  return text;
}

function statusLabel(value) {
  return STATUS[value] || value || "—";
}

function closeLightbox() {
  if (typeof lightbox.close === "function" && lightbox.open) lightbox.close();
  else lightbox.removeAttribute("open");
}

function openLightboxDialog() {
  if (typeof lightbox.showModal === "function") lightbox.showModal();
  else lightbox.setAttribute("open", "");
}

async function jobDetailPage(jobId) {
  const detail = await api(`/api/jobs/${jobId}`);
  const job = detail.job;
  const tree = detail.trace_tree || [];
  const actual = (detail.generations || []).find((item) => item.actual_gpu);
  main.innerHTML = `
    <h1>任务</h1>
    <p class="lede">每一笔 Modal 调用和估算费用都能对上。时间为墙钟 / GPU；$ 是标价 × 计费 GPU 秒。</p>
    <section class="panel" aria-labelledby="job-summary">
      <h2 class="section" id="job-summary">摘要</h2>
      <div class="check"><span>编号</span><span class="mono">${job.id}</span></div>
      <div class="check"><span>状态</span><span class="pill ${job.status}">${statusLabel(job.status)}</span></div>
      <div class="check"><span>请求的 GPU / 模型</span><span>${job.gpu} · ${job.model}</span></div>
      <div class="check"><span>实际 GPU</span><span>${actual?.actual_gpu || "—"} ${actual?.actual_device || ""}</span></div>
      <div class="check"><span>图片</span><span>${job.completed_images}/${job.total_images}</span></div>
      <div class="check"><span>GPU 秒</span><span class="mono">${(job.gpu_seconds || 0).toFixed(4)}</span></div>
      <div class="check"><span>单价</span><span class="mono">${formatRate(detail.cost?.usd_per_second)}</span></div>
      <div class="check"><span>估算费用</span><span class="mono">${formatUsd(job.cost_usd)}</span></div>
      <p class="apply-line mono">${detail.cost?.formula || ""}</p>
      <div class="check"><span>Modal 路径</span><span class="mono">${job.config?.deployed === true ? "已部署（强制）" : job.config?.deployed === false ? "一次性（强制）" : "自动"}</span></div>
      <div class="check"><span>Modal 应用</span><span class="mono">${job.modal_app_id || "—"}</span></div>
      <div class="check"><span>运行</span>${job.modal_run_url ? `<a href="${job.modal_run_url}" target="_blank" rel="noopener noreferrer">打开 Modal 运行页</a>` : "<span>—</span>"}</div>
      <p class="lede" style="margin:16px 0 0">${detail.cost?.notes || ""}</p>
      <div class="actions" style="margin-top:16px">
        <button type="button" class="ghost" id="to-gallery">图库</button>
        <button type="button" class="ghost" id="to-jobs">全部任务</button>
        <button type="button" class="ghost" id="to-cost">这笔任务的费用</button>
      </div>
    </section>
    <h2 class="section">调用链</h2>
    <div class="panel timeline">${renderTree(tree)}</div>
    <h2 class="section">生成记录</h2>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th scope="col">编号</th>
            <th scope="col">状态</th>
            <th scope="col">加载</th>
            <th scope="col">推理</th>
            <th scope="col">编码</th>
            <th scope="col">显存</th>
            <th scope="col">GPU 秒</th>
            <th scope="col">$/s</th>
            <th scope="col">费用</th>
            <th scope="col">输入</th>
          </tr>
        </thead>
        <tbody>
          ${(detail.generations || []).map((item) => `
            <tr>
              <td class="mono">${item.id}</td>
              <td><span class="pill ${item.status}">${statusLabel(item.status)}</span></td>
              <td>${item.load_ms != null ? item.load_ms.toFixed(0) + "ms" : "—"}</td>
              <td>${item.infer_ms != null ? item.infer_ms.toFixed(0) + "ms" : "—"}</td>
              <td>${item.encode_ms != null ? item.encode_ms.toFixed(0) + "ms" : "—"}</td>
              <td class="mono">${formatVram(item)}</td>
              <td class="mono">${(item.gpu_seconds || 0).toFixed(4)}</td>
              <td class="mono">${formatRate((detail.cost?.by_generation || []).find((row) => row.generation_id === item.id)?.usd_per_second)}</td>
              <td class="mono">${formatUsd(item.cost_usd)}</td>
              <td class="mono">${item.modal_input_id || "—"}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
  document.getElementById("to-gallery").onclick = () => { location.hash = `#/gallery?job=${job.id}`; };
  document.getElementById("to-jobs").onclick = () => { location.hash = "#/jobs"; };
  document.getElementById("to-cost").onclick = () => { location.hash = `#/cost?job=${job.id}`; };
}

function renderTree(nodes, depth = 0) {
  if (!nodes || !nodes.length) return "<p class=\"lede\">还没有跨度记录。</p>";
  return nodes.map((node) => `
    <div class="span" style="padding-inline-start:${depth * 18}px">
      <span class="mono">${node.name}</span>
      <span>${formatMs(node.duration_ms)}</span>
      <span class="pill">${node.kind}</span>
      <span class="mono">${node.cost_usd ? formatUsd(node.cost_usd) : ""}</span>
      <span class="mono muted">${node.modal_input_id || node.generation_id || ""}</span>
    </div>
    ${renderTree(node.children || [], depth + 1)}
  `).join("");
}

function progressBox() {
  return `<div class="progress" id="progress" role="status"><div>0 / 0</div><div class="bar" aria-hidden="true"><span></span></div></div>`;
}

function setProgress(completed, total, extra = "") {
  const root = document.getElementById("progress");
  if (!root) return;
  root.firstElementChild.textContent = `${completed} / ${total} ${extra}`.trim();
  const bar = root.querySelector("span");
  bar.style.width = total ? `${Math.min(100, (completed / total) * 100)}%` : "0%";
}

function showApplied(payload) {
  const root = document.getElementById("applied-banner");
  if (!root) return;
  const requested = payload.requested_gpu;
  const actual = payload.actual_gpu;
  const device = payload.actual_device;
  const model = payload.loaded_model || payload.requested_model;
  if (!requested && !actual && !model) return;
  const match = payload.gpu_match;
  const cls = match === false ? "bad" : "ok";
  const snap = payload.from_snapshot;
  const path = payload.deploy_mode || (payload.deployed ? "deployed" : payload.deployed === false ? "ephemeral" : "");
  const pathBit = path ? ` · 路径=${path}` : "";
  root.innerHTML = `<p class="apply-line ${cls}">容器 GPU=${actual || "?"}（${device || "无 CUDA 名"}）· 请求 ${requested || "?"} · 模型 ${model || "?"} · 匹配=${match == null ? "?" : match} · 快照=${snap == null ? "?" : snap}${pathBit}</p>`;
}

function listenJob(jobId) {
  if (state.source) state.source.close();
  state.source = new EventSource(`/api/jobs/${jobId}/events`);
  state.source.onmessage = (message) => {
    const event = JSON.parse(message.data);
    const progress = event.payload?.progress || event.payload;
    if (event.type === "job.snapshot" || event.type === "job.started") {
      setProgress(progress.completed_images || progress.completed || 0, progress.total_images || progress.total || 0);
    }
    if (event.type === "image.completed" || event.type === "image.failed") {
      setProgress(progress.completed || 0, progress.total || 0, event.type === "image.failed" ? "失败" : "");
      showApplied(event.payload || {});
    }
    if (["job.completed", "job.failed", "job.cancelled"].includes(event.type)) {
      setProgress(event.payload.completed_images || 0, event.payload.total_images || 0, statusLabel(event.payload.status));
      state.source.close();
      if (event.type === "job.failed") alert(event.payload.error || "任务失败");
      if (event.type === "job.completed") render("gallery");
    }
  };
}

function generatePage(meta) {
  const d = defaultsFrom(meta);
  main.innerHTML = `
    <h1>出图</h1>
    <p class="lede">写提示词，选模型和 GPU。本地工作台，不是 <code>modal serve</code>。</p>
    <p class="lede mono" id="runtime-line"></p>
    <form class="sheet" id="gen-form" method="post">
      <label for="field-prompt">提示词</label>
      <textarea id="field-prompt" name="prompt" placeholder="夜晚的东京街道，霓虹倒映在湿路面上" required autocomplete="off"></textarea>
      ${settingsGrid(d, meta)}
      <p class="apply-line mono" id="will-apply">将请求 …</p>
      <div class="meter" id="forecast">
        <div><span>加载</span><div class="mono" id="fc-load">…</div></div>
        <div><span>生成</span><div class="mono" id="fc-gen">…</div></div>
        <div><span>余额</span><div class="mono" id="fc-bal">…</div></div>
      </div>
      <p class="lede" id="fc-note"></p>
      <div class="actions">
        <button type="submit">开始出图</button>
        <span class="mono" id="job-id"></span>
      </div>
      <div id="applied-banner"></div>
      ${progressBox()}
    </form>
    <p class="lede"><a href="#/cost">查看每一笔 Modal 费用和调用链</a></p>
  `;
  const form = document.getElementById("gen-form");
  const refresh = () => refreshForecast(form);
  form.addEventListener("input", () => {
    updateWillApply(form, meta);
    clearTimeout(state.forecastTimer);
    state.forecastTimer = setTimeout(refresh, 280);
  });
  form.addEventListener("change", (event) => {
    if (event.target && event.target.name === "model") applyModelNative(form, meta);
    updateWillApply(form, meta);
    refresh();
  });
  form.onsubmit = async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const payload = jobPayload(event.target);
      payload.prompt = new FormData(event.target).get("prompt");
      const job = await api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      document.getElementById("job-id").textContent = job.id;
      setProgress(0, job.total_images);
      listenJob(job.id);
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  };
  updateWillApply(form, meta);
  refresh();
}

function updateWillApply(form, meta) {
  const payload = formPayload(form);
  const model = (meta.models || []).find((item) => item.id === payload.model);
  const gpu = (meta.gpus || []).find((item) => item.id === payload.gpu);
  const steps = payload.steps || model?.default_steps || "?";
  const line = document.getElementById("will-apply");
  if (!line) return;
  const runtime = meta.runtime || {};
  const prefer = payload.prefer_deployed !== false;
  const path = !prefer ? "一次性（强制）" : runtime.would_use || "自动";
  const native = model ? `${model.native_width}×${model.native_height}` : "";
  const sizeNote = model && payload.width === model.native_width && payload.height === model.native_height
    ? "原生"
    : native ? `原生 ${native}` : "";
  line.textContent = `将请求 GPU=${payload.gpu}  模型=${payload.model}  ${payload.width}×${payload.height}${sizeNote ? `（${sizeNote}）` : ""}  步数=${steps}  张数=${payload.count}  ·  ${path}`;
  const runtimeLine = document.getElementById("runtime-line");
  if (runtimeLine) runtimeLine.textContent = runtime.note || "";
  if (gpu && model && gpu.vram_gb < (model.min_vram_gb || 0)) {
    line.textContent += `  ·  警告 ${gpu.id} ${gpu.vram_gb}GB < 模型最低 ${model.min_vram_gb}GB`;
    line.classList.add("bad");
  } else {
    line.classList.remove("bad");
  }
}

async function refreshForecast(form) {
  const payload = formPayload(form);
  const query = new URLSearchParams({
    model: payload.model,
    gpu: payload.gpu,
    count: String(payload.count || 1),
    width: String(payload.width || 1024),
    height: String(payload.height || 1024),
    batch_size: String(payload.batch_size || 4),
    workers: String(payload.workers || 1),
    include_ledger: "false",
  });
  if (payload.steps != null) query.set("steps", String(payload.steps));
  try {
    const data = await api(`/api/cost/forecast?${query}`);
    renderForecast(data);
  } catch (error) {
    const load = document.getElementById("fc-load");
    if (load) load.textContent = error.message;
  }
}

function renderForecast(data) {
  const predict = data.predict || {};
  const balance = data.balance || {};
  const load = predict.load || {};
  const generate = predict.generate || {};
  const set = (id, text) => {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  };
  set("fc-load", `${formatUsd(load.usd)}\n${(load.seconds || 0).toFixed(1)}s`);
  set("fc-gen", `${formatUsd(generate.usd)}\n${(generate.seconds || 0).toFixed(1)}s · ${generate.count || 0} 张`);
  if (balance.ok) {
    const remain = balance.remaining_usd == null ? "—" : formatUsd(balance.remaining_usd);
    set(
      "fc-bal",
      `${remain} 剩余\n本月 ${formatUsd(balance.metered_usd)}`,
    );
  } else {
    set("fc-bal", balance.error || "Modal 账单暂不可用");
  }
  const note = document.getElementById("fc-note");
  if (note) {
    note.textContent = [predict.independent, balance.notes].filter(Boolean).join(" ");
  }
}

function renderChain(chain) {
  if (!chain || !chain.length) return "<p class=\"lede\">没有调用链。</p>";
  return `<ol class="chain">${chain.map((step) => `
    <li>
      <span>${step.name || step.kind}</span>
      <span class="mono">${escapeHtml(step.detail || "")}</span>
      <span class="mono">${step.cost_usd != null ? formatUsd(step.cost_usd) : ""}</span>
    </li>`).join("")}</ol>`;
}

function renderCostEvents(items, error) {
  if (!items.length) {
    return `<p class="lede">${error || "这个周期没有 Modal 费用事件。真实生成会写入共享账本。"}</p>`;
  }
  return items.map((item) => `
    <details class="event">
      <summary>
        <span class="mono">${(item.ts || "").replace("T", " ").slice(0, 19)}</span>
        <span>${kindLabel(item.kind)}</span>
        <span class="mono">${item.job_id ? `<a href="#/job/${escapeAttr(item.job_id)}">${escapeHtml(item.job_id)}</a>` : "—"}</span>
        <span>${item.billed_gpu || item.actual_gpu || item.requested_gpu || "—"}</span>
        <span class="mono">${formatRate(item.usd_per_second)}</span>
        <span class="mono">${Number(item.gpu_seconds || 0).toFixed(4)}s</span>
        <span class="mono">${formatUsd(item.cost_usd)}</span>
      </summary>
      <p class="apply-line mono">${escapeHtml(item.formula || "")}</p>
      ${renderChain(item.chain)}
      <dl class="meta-inline">
        <dt>模型</dt><dd>${item.model || "—"}</dd>
        <dt>generation</dt><dd class="mono">${item.generation_id || "—"}</dd>
        <dt>function_call</dt><dd class="mono">${item.modal_function_call_id || "—"}</dd>
        <dt>input</dt><dd class="mono">${item.modal_input_id || "—"}</dd>
        <dt>task</dt><dd class="mono">${item.modal_task_id || "—"}</dd>
        <dt>显存</dt><dd class="mono">${formatVram(item)}</dd>
      </dl>
    </details>`).join("");
}

async function costPage(meta, params) {
  const period = params.get("period") || "day";
  const kind = params.get("kind") || "";
  const job = params.get("job") || "";
  const page = Number(params.get("page") || 1);
  state.ledgerPage = page;
  const query = new URLSearchParams({ period, page: String(page), per_page: "25" });
  if (kind) query.set("kind", kind);
  if (job) query.set("job_id", job);
  let ledger = {};
  let balance = {};
  let jobs = [];
  try {
    [ledger, balance, jobs] = await Promise.all([
      api(`/api/cost/ledger?${query}`),
      api("/api/cost/balance"),
      api("/api/jobs"),
    ]);
  } catch (error) {
    main.innerHTML = `<h1>费用</h1><p class="lede">${escapeHtml(error.message)}</p>`;
    return;
  }
  const grainLabel = { hour: "小时", day: "天", week: "周", month: "月", all: "全部" };
  const snaps = ledger.snapshots || {};
  main.innerHTML = `
    <h1>费用</h1>
    <p class="lede">每一笔都是 Modal GPU 秒 × 公布的每秒单价。点开看调用链和对应任务。发票以 <code>modal billing</code> 为准。</p>
    <section class="panel">
      <div class="check"><span>工作区</span><span class="mono">${balance.workspace || "—"}</span></div>
      <div class="check"><span>本月计量</span><span class="mono">${balance.ok ? formatUsd(balance.metered_usd) : (balance.error || "—")}</span></div>
      <div class="check"><span>剩余（估）</span><span class="mono">${balance.ok ? formatUsd(balance.remaining_usd) : "—"}</span></div>
    </section>
    <div class="snapshots" id="ledger-snaps">
      ${["hour", "day", "week", "month"].map((grain) => {
        const row = snaps[grain] || {};
        return `<div class="snap"><span>${grainLabel[grain]}</span><strong>${formatUsd(row.total_cost_usd)}</strong><small>加载 ${formatUsd(row.load_cost_usd)} · 生成 ${formatUsd(row.generate_cost_usd)}</small></div>`;
      }).join("")}
    </div>
    <h2 class="section">单价</h2>
    <div class="panel">
      <table>
        <thead><tr><th scope="col">GPU</th><th scope="col">$/s</th><th scope="col">$/小时</th><th scope="col">¢ / 10s</th></tr></thead>
        <tbody>
          ${(meta.gpus || []).map((gpu) => `
            <tr>
              <td>${gpu.id}</td>
              <td class="mono">$${Number(gpu.usd_per_second).toFixed(6)}</td>
              <td class="mono">$${Number(gpu.usd_per_hour).toFixed(2)}</td>
              <td class="mono">${(Number(gpu.usd_per_second) * 1000).toFixed(3)}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
    <h2 class="section">每一笔调用</h2>
    <form class="toolbar" id="cost-filters">
      <label class="field" for="cost-period">周期
        <select id="cost-period" name="period">
          ${[["hour", "小时"], ["day", "天"], ["week", "周"], ["month", "月"], ["all", "全部"]].map(([value, label]) => `<option value="${value}" ${value === period ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <label class="field" for="cost-kind">类型
        <select id="cost-kind" name="kind">
          ${[["", "全部"], ["gpu_load", "加载"], ["gpu_generate", "生成"]].map(([value, label]) => `<option value="${value}" ${value === kind ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <label class="field" for="cost-job">任务编号
        <input id="cost-job" name="job" value="${escapeAttr(job)}" autocomplete="off" />
      </label>
      <button type="submit" class="ghost">筛选</button>
    </form>
    <div class="panel events">${renderCostEvents(ledger.items || [], ledger.error)}</div>
    <div class="pager">
      <button type="button" class="ghost" id="ledger-prev" ${page <= 1 ? "disabled" : ""}>上一页</button>
      <span id="ledger-page">第 ${ledger.page || 1} / ${ledger.pages || 1} 页 · ${ledger.total || 0} 条</span>
      <button type="button" class="ghost" id="ledger-next" ${(ledger.page || 1) >= (ledger.pages || 1) ? "disabled" : ""}>下一页</button>
    </div>
    <h2 class="section">本机任务</h2>
    <div class="panel">
      <table>
        <thead><tr><th scope="col">任务</th><th scope="col">状态</th><th scope="col">GPU</th><th scope="col">秒</th><th scope="col">$</th></tr></thead>
        <tbody>
          ${jobs.map((row) => `
            <tr>
              <td class="mono"><a href="#/job/${row.id}">${row.id}</a></td>
              <td><span class="pill ${row.status}">${statusLabel(row.status)}</span></td>
              <td>${row.gpu}</td>
              <td class="mono">${(row.gpu_seconds || 0).toFixed(4)}</td>
              <td class="mono">${formatUsd(row.cost_usd)}</td>
            </tr>`).join("") || `<tr><td colspan="5">还没有任务。</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
  document.getElementById("cost-filters").onsubmit = (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    const next = new URLSearchParams({
      period: data.get("period") || "day",
      kind: data.get("kind") || "",
      job: data.get("job") || "",
      page: "1",
    });
    location.hash = `#/cost?${next}`;
  };
  document.getElementById("ledger-prev").onclick = () => {
    const next = new URLSearchParams(params);
    next.set("page", String(Math.max(1, page - 1)));
    location.hash = `#/cost?${next}`;
  };
  document.getElementById("ledger-next").onclick = () => {
    const next = new URLSearchParams(params);
    next.set("page", String(page + 1));
    location.hash = `#/cost?${next}`;
  };
}

function batchPage(meta) {
  const d = defaultsFrom(meta);
  main.innerHTML = `
    <h1>批量</h1>
    <p class="lede">拖入 txt / jsonl / json / csv，或每行一条提示词。JSONL 是原生协议。</p>
    <form class="sheet" id="batch-form" method="post">
      <div class="drop" id="drop">
        <label for="field-file">把 prompts.txt / prompts.jsonl 拖到这里，或选择文件</label>
        <div style="margin-top:12px"><input id="field-file" type="file" name="file" accept=".txt,.jsonl,.json,.csv" /></div>
      </div>
      <label for="field-text">或者粘贴</label>
      <textarea id="field-text" name="text" placeholder="一片森林&#10;一座未来城市"></textarea>
      ${settingsGrid(d, meta)}
      <div class="actions"><button type="submit">开始批量</button><span class="mono" id="job-id"></span></div>
      ${progressBox()}
    </form>
  `;
  const drop = document.getElementById("drop");
  const fileInput = drop.querySelector("input[type=file]");
  drop.ondragover = (event) => { event.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (event) => {
    event.preventDefault();
    drop.classList.remove("over");
    if (event.dataTransfer.files[0]) fileInput.files = event.dataTransfer.files;
  };
  document.getElementById("batch-form").addEventListener("change", (event) => {
    if (event.target && event.target.name === "model") applyModelNative(event.currentTarget, meta);
  });
  document.getElementById("batch-form").onsubmit = async (event) => {
    event.preventDefault();
    const button = event.target.querySelector("button[type=submit]");
    button.disabled = true;
    try {
      const payload = jobPayload(event.target);
      const file = fileInput.files[0];
      let job;
      if (file) {
        const body = new FormData();
        body.append("file", file);
        const query = new URLSearchParams({
          model: payload.model,
          gpu: payload.gpu,
          count: String(payload.count),
          width: String(payload.width),
          height: String(payload.height),
          batch_size: String(payload.batch_size),
          workers: String(payload.workers),
          dry_run: String(payload.dry_run),
          image_format: payload.image_format,
        });
        if (payload.deployed === false) query.set("deployed", "false");
        job = await api(`/api/jobs/from-file?${query}`, { method: "POST", body });
      } else {
        payload.text = new FormData(event.target).get("text");
        job = await api("/api/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
      }
      document.getElementById("job-id").textContent = job.id;
      setProgress(0, job.total_images);
      listenJob(job.id);
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  };
}

async function jobsPage() {
  const jobs = await api("/api/jobs");
  main.innerHTML = `
    <h1>任务</h1>
    <p class="lede">每一次生成或批量都是一个任务。继续只会重试未完成的帧。</p>
    <div class="panel">
      <table>
        <thead><tr><th scope="col">编号</th><th scope="col">状态</th><th scope="col">图片</th><th scope="col">模型</th><th scope="col">GPU</th><th scope="col">费用</th><th scope="col">操作</th></tr></thead>
        <tbody>
          ${jobs.map((job) => `
            <tr>
              <td class="mono"><a href="#/job/${job.id}">${job.id}</a></td>
              <td><span class="pill ${job.status}">${statusLabel(job.status)}</span></td>
              <td>${job.completed_images}/${job.total_images}</td>
              <td>${job.model}</td>
              <td>${job.gpu}</td>
              <td class="mono">${formatUsd(job.cost_usd)}</td>
              <td>
                <button type="button" class="ghost" data-job="${job.id}">追踪</button>
                <button type="button" class="ghost" data-gallery="${job.id}">图库</button>
                <button type="button" class="ghost" data-resume="${job.id}">继续</button>
              </td>
            </tr>`).join("") || `<tr><td colspan="7">还没有任务。</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
  main.querySelectorAll("[data-job]").forEach((button) => {
    button.onclick = () => { location.hash = `#/job/${button.dataset.job}`; };
  });
  main.querySelectorAll("[data-gallery]").forEach((button) => {
    button.onclick = () => { location.hash = `#/gallery?job=${button.dataset.gallery}`; };
  });
  main.querySelectorAll("[data-resume]").forEach((button) => {
    button.onclick = async () => {
      await api(`/api/jobs/${button.dataset.resume}/resume`, { method: "POST" });
      render("jobs");
    };
  });
}

async function galleryPage(params) {
  const page = Number(params.get("page") || 1);
  const per = Number(params.get("per") || 50);
  const job = params.get("job") || "";
  const q = params.get("q") || "";
  const sort = params.get("sort") || "newest";
  const query = new URLSearchParams({ page, per_page: per, sort });
  if (job) query.set("job_id", job);
  if (q) query.set("q", q);
  const data = await api(`/api/gallery?${query}`);
  const sortLabels = { newest: "最新", oldest: "最旧", fastest: "最快", slowest: "最慢" };
  main.innerHTML = `
    <h1>图库</h1>
    <p class="lede">${data.total} 张图。悬停卡片看提示词、种子、GPU 和耗时。没有匿名帧。</p>
    <form class="toolbar" id="filters" method="get">
      <label class="field" for="filter-q">搜索提示词
        <input id="filter-q" name="q" value="${q}" />
      </label>
      <label class="field" for="filter-job">任务编号
        <input id="filter-job" name="job" value="${job}" autocomplete="off" />
      </label>
      <label class="field" for="filter-sort">排序
        <select id="filter-sort" name="sort">
          ${Object.entries(sortLabels).map(([item, label]) => `<option value="${item}" ${item === sort ? "selected" : ""}>${label}</option>`).join("")}
        </select>
      </label>
      <label class="field" for="filter-per">每页
        <select id="filter-per" name="per">
          ${[50, 100, 200].map((item) => `<option value="${item}" ${item === per ? "selected" : ""}>${item}</option>`).join("")}
        </select>
      </label>
      <button type="submit" class="ghost">筛选</button>
    </form>
    <div class="gallery">
      ${data.items.map((image) => `
        <article class="card" data-id="${image.id}">
          <img src="/api/images/${image.id}/file" alt="${escapeAttr(image.prompt)}" width="${image.width || 1024}" height="${image.height || 1024}" style="aspect-ratio:${image.width || 1024} / ${image.height || 1024}" />
          <div class="cap">${escapeHtml(image.prompt)}</div>
          <div class="hover">
            <div>${escapeHtml(image.prompt)}</div>
            <div class="mono" style="margin-top:10px">种子 ${image.seed}<br>${image.model}<br>${image.gpu}<br>${image.latency_ms ? (image.latency_ms / 1000).toFixed(2) + "s" : ""}<br>${formatUsd(image.cost_usd)}</div>
          </div>
        </article>`).join("") || "<p>还没有图。先去生成一页。</p>"}
    </div>
    <div class="pager">
      <button type="button" class="ghost" ${page <= 1 ? "disabled" : ""} id="prev">上一页</button>
      <span>第 ${data.page} 页 · 每页 ${data.per_page} 张</span>
      <button type="button" class="ghost" ${page * per >= data.total ? "disabled" : ""} id="next">下一页</button>
    </div>
  `;
  document.getElementById("filters").onsubmit = (event) => {
    event.preventDefault();
    const form = new FormData(event.target);
    const next = new URLSearchParams({
      q: form.get("q") || "",
      job: form.get("job") || "",
      sort: form.get("sort"),
      per: form.get("per"),
      page: "1",
    });
    location.hash = `#/gallery?${next}`;
  };
  document.getElementById("prev").onclick = () => {
    params.set("page", String(page - 1));
    location.hash = `#/gallery?${params}`;
  };
  document.getElementById("next").onclick = () => {
    params.set("page", String(page + 1));
    location.hash = `#/gallery?${params}`;
  };
  main.querySelectorAll(".card").forEach((card) => {
    card.onclick = () => openLightbox(card.dataset.id);
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll('"', "&quot;");
}

async function openLightbox(imageId) {
  const image = await api(`/api/images/${imageId}`);
  lightbox.innerHTML = `
    <img src="/api/images/${image.id}/file" alt="${escapeAttr(image.prompt)}" width="${image.width || 1024}" height="${image.height || 1024}" />
    <div class="meta">
      <h2 id="lightbox-title">这一帧</h2>
      <p>${escapeHtml(image.prompt)}</p>
      <dl>
        <dt>种子</dt><dd class="mono">${image.seed}</dd>
        <dt>模型</dt><dd>${image.model}</dd>
        <dt>请求 GPU</dt><dd>${image.gpu}</dd>
        <dt>实际 GPU</dt><dd>${image.actual_gpu || "—"} ${image.actual_device ? `（${image.actual_device}）` : ""}</dd>
        <dt>步数</dt><dd>${image.steps}</dd>
        <dt>尺寸</dt><dd>${image.width} × ${image.height}</dd>
        <dt>耗时</dt><dd>${image.latency_ms ? (image.latency_ms / 1000).toFixed(2) + "s" : "—"}</dd>
        <dt>推理</dt><dd>${image.infer_ms != null ? image.infer_ms.toFixed(0) + " ms" : "—"}</dd>
        <dt>显存</dt><dd class="mono">${formatVram(image)}</dd>
        <dt>费用</dt><dd>${formatUsd(image.cost_usd)}</dd>
        <dt>调用</dt><dd class="mono">${image.modal_function_call_id || "—"}</dd>
        <dt>输入</dt><dd class="mono">${image.modal_input_id || "—"}</dd>
        <dt>任务</dt><dd class="mono"><a href="#/job/${image.job_id}">${image.job_id}</a></dd>
      </dl>
      <div class="actions" style="margin-top:18px">
        <button type="button" id="copy">复制提示词</button>
        <button type="button" class="ghost" id="regen">再生成</button>
        <a class="btn ghost" href="/api/images/${image.id}/file" download>下载</a>
        <button type="button" class="ghost" id="close">关闭</button>
      </div>
    </div>
  `;
  openLightboxDialog();
  document.getElementById("close").onclick = closeLightbox;
  document.getElementById("copy").onclick = async () => {
    await navigator.clipboard.writeText(image.prompt);
  };
  document.getElementById("regen").onclick = async () => {
    const job = await api(`/api/images/${image.id}/regenerate`, { method: "POST" });
    closeLightbox();
    location.hash = "#/jobs";
    alert(`已排队 ${job.id}`);
  };
}

function benchmarkPage(meta) {
  main.innerHTML = `
    <h1>基准</h1>
    <p class="lede">同一提示词对比 GPU。费用用 Modal 公布的每秒标价。</p>
    <div class="panel">
      <p>要真实数字，在终端跑：</p>
      <p class="mono">uv run modal-sana benchmark --gpu L40S,RTX-PRO-6000 --count 8</p>
      <table>
        <thead><tr><th scope="col">GPU</th><th scope="col">$/小时</th><th scope="col">显存</th><th scope="col">批大小</th><th scope="col">备注</th></tr></thead>
        <tbody>
          ${meta.gpus.map((gpu) => `
            <tr>
              <td>${gpu.id}</td>
              <td>$${gpu.usd_per_hour.toFixed(2)}</td>
              <td>${gpu.vram_gb} GB</td>
              <td>${gpu.recommended_batch}</td>
              <td>${gpu.notes}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function settingsPage(meta) {
  const doctor = await api("/api/doctor");
  main.innerHTML = `
    <h1>设置</h1>
    <p class="lede">本地工作台。可执行文件不跑模型，Modal 才跑。</p>
    <div class="panel">
      <div class="check"><span>数据目录</span><span class="mono">${meta.defaults.data_dir}</span></div>
      <div class="check"><span>默认模型</span><span>${meta.defaults.model}</span></div>
      <div class="check"><span>默认 GPU</span><span>${meta.defaults.gpu}</span></div>
      <div class="check"><span>Modal 路径</span><span class="mono">${meta.runtime?.would_use || "自动"} · ${meta.runtime?.app_name || "modal-sana"}</span></div>
      <p class="lede">${meta.runtime?.note || ""}</p>
      <h2 class="section">体检</h2>
      <div class="checks">
        ${doctor.checks.map((check) => `
          <div class="check">
            <span>${check.name}</span>
            <span class="${check.ok ? "ok" : "bad"}">${check.ok ? "✓" : "✗"} ${check.detail}</span>
          </div>`).join("")}
      </div>
    </div>
  `;
}

async function render(forced) {
  const hash = location.hash.replace(/^#\/?/, "") || "generate";
  const [pageName, query] = hash.split("?");
  const page = forced || pageName || "generate";
  const params = new URLSearchParams(query || "");
  document.querySelectorAll("nav a").forEach((link) => {
    const active = link.dataset.page === page
      || (page.startsWith("job/") && link.dataset.page === "jobs")
      || (page.startsWith("cost") && link.dataset.page === "cost");
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  if (!state.meta) state.meta = await api("/api/meta");
  if (page === "generate") generatePage(state.meta);
  else if (page === "batch") batchPage(state.meta);
  else if (page === "jobs") await jobsPage();
  else if (page.startsWith("job/")) await jobDetailPage(page.slice(4));
  else if (page === "gallery") await galleryPage(params);
  else if (page === "cost") await costPage(state.meta, params);
  else if (page === "benchmark") benchmarkPage(state.meta);
  else if (page === "settings") await settingsPage(state.meta);
  else generatePage(state.meta);
}

window.addEventListener("hashchange", () => render());
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) closeLightbox();
});
lightbox.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeLightbox();
});

(async () => {
  try {
    const [doctor, meta] = await Promise.all([api("/api/doctor"), api("/api/meta")]);
    state.meta = meta;
    const ver = meta.version ? `v${meta.version} · ` : "";
    if (!doctor.ready) {
      mastStatus.textContent = `${ver}尚未登录 Modal`;
      mastStatus.className = "mast-status bad";
    } else if (meta.runtime?.available) {
      mastStatus.textContent = `${ver}已部署 · 快照开着`;
      mastStatus.className = "mast-status ok";
    } else {
      mastStatus.textContent = `${ver}先部署 · ${meta.runtime?.deploy_command || "modal deploy"}`;
      mastStatus.className = "mast-status bad";
    }
  } catch {
    mastStatus.textContent = "接口离线";
  }
  await render();
})();
