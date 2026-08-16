const main = document.getElementById("main");
const lightbox = document.getElementById("lightbox");
const railStatus = document.getElementById("rail-status");

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
    workers: 2,
    format: meta?.defaults?.image_format || "png",
    dry_run: false,
    prefer_deployed: meta?.defaults?.prefer_deployed !== false,
  };
}

function field(name, label, value, type = "text") {
  if (type === "checkbox") {
    return `<label class="check"><span>${label}</span><input type="checkbox" name="${name}" ${value ? "checked" : ""} /></label>`;
  }
  return `<div><label>${label}</label><input name="${name}" type="${type}" value="${value ?? ""}" /></div>`;
}

function select(name, label, options, value) {
  const opts = options.map((item) => {
    const id = item.id || item;
    const title = item.name || item.id || item;
    return `<option value="${id}" ${id === value ? "selected" : ""}>${title}</option>`;
  }).join("");
  return `<div><label>${label}</label><select name="${name}">${opts}</select></div>`;
}

function gpuChoices(meta) {
  return (meta.gpus || []).map((gpu) => ({
    id: gpu.id,
    name: `${gpu.id} · $${Number(gpu.usd_per_hour).toFixed(2)}/hr · ${gpu.vram_gb}GB`,
  }));
}

function modelChoices(meta) {
  return (meta.models || []).map((model) => ({
    id: model.id,
    name: `${model.name || model.id} · ${model.native_width || 1024}×${model.native_height || 1024}`,
  }));
}

function applyModelNative(form, meta) {
  const model = (meta.models || []).find((item) => item.id === form.model?.value);
  if (!model) return;
  if (form.width) form.width.value = model.native_width || 1024;
  if (form.height) form.height.value = model.native_height || 1024;
  if (form.batch_size && model.recommended_batch) form.batch_size.value = model.recommended_batch;
}

function settingsGrid(d, meta) {
  return `
    <div class="grid">
      ${select("model", "Model（换模型会改到该权重的原生分辨率）", modelChoices(meta), d.model)}
      ${select("gpu", "GPU（必须单独选；默认 L40S）", gpuChoices(meta), d.gpu)}
      ${field("count", "Count", d.count, "number")}
      ${field("width", "Width", d.width, "number")}
      ${field("height", "Height", d.height, "number")}
      ${field("steps", "Steps (blank = model default)", d.steps)}
      ${field("guidance", "Guidance", d.guidance)}
      ${field("seed", "Seed", d.seed)}
      ${field("batch_size", "GPU batch", d.batch_size, "number")}
      ${field("workers", "Workers", d.workers, "number")}
      ${select("image_format", "Format", ["png", "jpg", "webp"], d.format)}
    </div>
    ${field("dry_run", "Dry run (no Modal / no GPU)", d.dry_run, "checkbox")}
    ${field("prefer_deployed", "Prefer deployed Modal app (memory snapshots)", d.prefer_deployed !== false, "checkbox")}
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
    workers: Number(data.get("workers") || 2),
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

async function jobDetailPage(jobId) {
  const detail = await api(`/api/jobs/${jobId}`);
  const job = detail.job;
  const tree = detail.trace_tree || [];
  main.innerHTML = `
    <h1>Job</h1>
    <p class="lede">Localize every Modal call and every estimated cent. Times are wall / GPU; $ is list price × charged GPU seconds.</p>
    <div class="panel">
      <div class="check"><span>ID</span><span class="mono">${job.id}</span></div>
      <div class="check"><span>Status</span><span class="pill ${job.status}">${job.status}</span></div>
      <div class="check"><span>Requested GPU / model</span><span>${job.gpu} · ${job.model}</span></div>
      <div class="check"><span>Actual GPU</span><span>${(detail.generations || []).find((item) => item.actual_gpu)?.actual_gpu || "—"} ${(detail.generations || []).find((item) => item.actual_device)?.actual_device || ""}</span></div>
      <div class="check"><span>Images</span><span>${job.completed_images}/${job.total_images}</span></div>
      <div class="check"><span>GPU seconds</span><span class="mono">${(job.gpu_seconds || 0).toFixed(4)}</span></div>
      <div class="check"><span>Estimated cost</span><span class="mono">${formatUsd(job.cost_usd)}</span></div>
      <div class="check"><span>Modal path</span><span class="mono">${job.config?.deployed === true ? "deployed (required)" : job.config?.deployed === false ? "ephemeral (forced)" : "auto"}</span></div>
      <div class="check"><span>Modal app</span><span class="mono">${job.modal_app_id || "—"}</span></div>
      <div class="check"><span>Run</span>${job.modal_run_url ? `<a href="${job.modal_run_url}" target="_blank" rel="noreferrer">${job.modal_run_url}</a>` : "<span>—</span>"}</div>
      <p class="lede" style="margin:16px 0 0">${detail.cost?.notes || ""}</p>
      <div class="actions" style="margin-top:16px">
        <button class="ghost" id="to-gallery">Gallery</button>
        <button class="ghost" id="to-jobs">All jobs</button>
      </div>
    </div>
    <h2 class="section">Call chain</h2>
    <div class="panel timeline">${renderTree(tree)}</div>
    <h2 class="section">Generations</h2>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>STATUS</th><th>LOAD</th><th>INFER</th><th>ENCODE</th><th>GPU-S</th><th>$</th><th>INPUT</th></tr></thead>
        <tbody>
          ${(detail.generations || []).map((item) => `
            <tr>
              <td class="mono">${item.id}</td>
              <td><span class="pill ${item.status}">${item.status}</span></td>
              <td>${item.load_ms != null ? item.load_ms.toFixed(0) + "ms" : "—"}</td>
              <td>${item.infer_ms != null ? item.infer_ms.toFixed(0) + "ms" : "—"}</td>
              <td>${item.encode_ms != null ? item.encode_ms.toFixed(0) + "ms" : "—"}</td>
              <td class="mono">${(item.gpu_seconds || 0).toFixed(4)}</td>
              <td class="mono">${formatUsd(item.cost_usd)}</td>
              <td class="mono">${item.modal_input_id || "—"}</td>
            </tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
  document.getElementById("to-gallery").onclick = () => { location.hash = `#/gallery?job=${job.id}`; };
  document.getElementById("to-jobs").onclick = () => { location.hash = "#/jobs"; };
}

function renderTree(nodes, depth = 0) {
  if (!nodes || !nodes.length) return "<p class=\"lede\">No spans yet.</p>";
  return nodes.map((node) => `
    <div class="span" style="padding-left:${depth * 18}px">
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
  return `<div class="progress" id="progress"><div>0 / 0</div><div class="bar"><span></span></div></div>`;
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
  const pathBit = path ? ` · path=${path}` : "";
  root.innerHTML = `<div class="apply-line ${cls}">container GPU=${actual || "?"} (${device || "no cuda name"}) · requested ${requested || "?"} · model ${model || "?"} · match=${match == null ? "?" : match} · snapshot=${snap == null ? "?" : snap}${pathBit}</div>`;
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
      setProgress(progress.completed || 0, progress.total || 0, event.type === "image.failed" ? "failed" : "");
      showApplied(event.payload || {});
    }
    if (["job.completed", "job.failed", "job.cancelled"].includes(event.type)) {
      setProgress(event.payload.completed_images || 0, event.payload.total_images || 0, event.payload.status);
      state.source.close();
      if (event.type === "job.failed") alert(event.payload.error || "job failed");
      if (event.type === "job.completed") render("gallery");
    }
  };
}

function generatePage(meta) {
  const d = defaultsFrom(meta);
  main.innerHTML = `
    <h1>Generate</h1>
    <p class="lede">One prompt. This local web is <strong>not</strong> <code>modal serve</code>. Generate prefers the app from <code>modal deploy</code> (snapshots on). No deploy → one-off ephemeral <code>app.run()</code>.</p>
    <p class="lede mono" id="runtime-line"></p>
    <form class="panel" id="gen-form">
      <label>Prompt</label>
      <textarea name="prompt" placeholder="a futuristic Tokyo street at night" required></textarea>
      ${settingsGrid(d, meta)}
      <p class="apply-line mono" id="will-apply">will request …</p>
      <div class="forecast" id="forecast">
        <div class="forecast-card"><h3>纯 GPU 加载</h3><div class="mono" id="fc-load">…</div></div>
        <div class="forecast-card"><h3>GPU 实际生成</h3><div class="mono" id="fc-gen">…</div></div>
        <div class="forecast-card"><h3>Modal 还剩多少钱</h3><div class="mono" id="fc-bal">…</div></div>
      </div>
      <p class="lede" id="fc-note"></p>
      <div class="actions">
        <button type="submit">Generate</button>
        <span class="mono" id="job-id"></span>
      </div>
      <div id="applied-banner"></div>
      ${progressBox()}
    </form>
    <h2 class="section">Shared Modal cost ledger</h2>
    <p class="lede">Every device that runs this app against the same Modal workspace writes here. Periods are UTC.</p>
    <div class="panel" id="ledger-panel">
      <div class="snapshots" id="ledger-snaps"></div>
      <div class="toolbar">
        <select id="ledger-period">
          ${["hour", "day", "week", "month", "all"].map((item) => `<option value="${item}" ${item === "day" ? "selected" : ""}>${item}</option>`).join("")}
        </select>
        <button type="button" class="ghost" id="ledger-refresh">Refresh</button>
      </div>
      <table>
        <thead><tr><th>PERIOD</th><th>LOAD $</th><th>GENERATE $</th><th>TOTAL</th><th>EVENTS</th></tr></thead>
        <tbody id="ledger-periods"><tr><td colspan="5">loading…</td></tr></tbody>
      </table>
      <h3 class="section">Events</h3>
      <table>
        <thead><tr><th>TIME</th><th>KIND</th><th>MODEL</th><th>GPU</th><th>SEC</th><th>$</th></tr></thead>
        <tbody id="ledger-events"><tr><td colspan="6">loading…</td></tr></tbody>
      </table>
      <div class="pager">
        <button type="button" class="ghost" id="ledger-prev">Prev</button>
        <span id="ledger-page">page 1</span>
        <button type="button" class="ghost" id="ledger-next">Next</button>
      </div>
    </div>
  `;
  const form = document.getElementById("gen-form");
  state.ledgerPage = 1;
  const refresh = () => refreshForecast(form, state.ledgerPage);
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
  document.getElementById("ledger-period").onchange = () => {
    state.ledgerPage = 1;
    refresh();
  };
  document.getElementById("ledger-refresh").onclick = refresh;
  document.getElementById("ledger-prev").onclick = () => {
    state.ledgerPage = Math.max(1, (state.ledgerPage || 1) - 1);
    refresh();
  };
  document.getElementById("ledger-next").onclick = () => {
    state.ledgerPage = (state.ledgerPage || 1) + 1;
    refresh();
  };
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
  const path = !prefer ? "ephemeral (forced)" : runtime.would_use || "auto";
  const native = model ? `${model.native_width}×${model.native_height}` : "";
  const sizeNote = model && payload.width === model.native_width && payload.height === model.native_height
    ? "native"
    : native ? `native ${native}` : "";
  line.textContent = `will request GPU=${payload.gpu}  model=${payload.model}  ${payload.width}×${payload.height}${sizeNote ? ` (${sizeNote})` : ""}  steps=${steps}  count=${payload.count}  ·  ${path}`;
  const runtimeLine = document.getElementById("runtime-line");
  if (runtimeLine) runtimeLine.textContent = runtime.note || "";
  if (gpu && model && gpu.vram_gb < (model.min_vram_gb || 0)) {
    line.textContent += `  ·  WARN ${gpu.id} ${gpu.vram_gb}GB < model min ${model.min_vram_gb}GB`;
    line.classList.add("bad");
  } else {
    line.classList.remove("bad");
  }
}

async function refreshForecast(form, page = 1) {
  const payload = formPayload(form);
  const period = document.getElementById("ledger-period")?.value || "day";
  const query = new URLSearchParams({
    model: payload.model,
    gpu: payload.gpu,
    count: String(payload.count || 1),
    width: String(payload.width || 1024),
    height: String(payload.height || 1024),
    batch_size: String(payload.batch_size || 4),
    workers: String(payload.workers || 2),
    period,
    page: String(page || 1),
    per_page: "15",
  });
  if (payload.steps != null) query.set("steps", String(payload.steps));
  try {
    const data = await api(`/api/cost/forecast?${query}`);
    renderForecast(data);
    renderLedger(data.ledger);
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
  set("fc-load", `${formatUsd(load.usd)}\n${(load.seconds || 0).toFixed(2)}s · ${load.containers || 1} container\n${load.source || ""}`);
  set("fc-gen", `${formatUsd(generate.usd)}\n${(generate.seconds || 0).toFixed(2)}s · ${generate.count || 0} image\n${generate.source || ""}`);
  if (balance.ok) {
    const remain = balance.remaining_usd == null ? "—" : formatUsd(balance.remaining_usd);
    set(
      "fc-bal",
      `${remain} left (est.)\nthis month metered ${formatUsd(balance.metered_usd)}\ncredits used ${formatUsd(balance.credits_applied_usd)} · billed ${formatUsd(balance.billed_usd)}`,
    );
  } else {
    set("fc-bal", balance.error || "Modal billing unavailable");
  }
  const note = document.getElementById("fc-note");
  if (note) {
    note.textContent = [predict.independent, balance.notes].filter(Boolean).join(" ");
  }
}

function renderLedger(ledger) {
  if (!ledger) return;
  const snaps = document.getElementById("ledger-snaps");
  const snapshots = ledger.snapshots || {};
  if (snaps) {
    snaps.innerHTML = ["hour", "day", "week", "month"].map((grain) => {
      const row = snapshots[grain] || {};
      return `<div class="snap"><span>${grain}</span><strong>${formatUsd(row.total_cost_usd)}</strong><small>load ${formatUsd(row.load_cost_usd)} · gen ${formatUsd(row.generate_cost_usd)}</small></div>`;
    }).join("");
  }
  const periods = document.getElementById("ledger-periods");
  if (periods) {
    const rows = ledger.periods || [];
    periods.innerHTML = rows.length
      ? rows.map((row) => `
          <tr>
            <td class="mono">${row.period}</td>
            <td class="mono">${formatUsd(row.load_cost_usd)}</td>
            <td class="mono">${formatUsd(row.generate_cost_usd)}</td>
            <td class="mono">${formatUsd(row.total_cost_usd)}</td>
            <td>${row.count}</td>
          </tr>`).join("")
      : `<tr><td colspan="5">${ledger.error || "No shared events yet. First real Modal generate writes the ledger."}</td></tr>`;
  }
  const events = document.getElementById("ledger-events");
  if (events) {
    const rows = ledger.items || [];
    events.innerHTML = rows.length
      ? rows.map((item) => `
          <tr>
            <td class="mono">${(item.ts || "").replace("T", " ").slice(0, 19)}</td>
            <td>${item.kind}</td>
            <td>${item.model || "—"}</td>
            <td>${item.actual_gpu || item.requested_gpu || "—"}</td>
            <td class="mono">${Number(item.gpu_seconds || 0).toFixed(3)}</td>
            <td class="mono">${formatUsd(item.cost_usd)}</td>
          </tr>`).join("")
      : `<tr><td colspan="6">${ledger.error || "No events in this period."}</td></tr>`;
  }
  const page = document.getElementById("ledger-page");
  if (page) page.textContent = `page ${ledger.page || 1} / ${ledger.pages || 1} · ${ledger.total || 0} events`;
  const prev = document.getElementById("ledger-prev");
  const next = document.getElementById("ledger-next");
  if (prev) prev.disabled = (ledger.page || 1) <= 1;
  if (next) next.disabled = (ledger.page || 1) >= (ledger.pages || 1);
}

function batchPage(meta) {
  const d = defaultsFrom(meta);
  main.innerHTML = `
    <h1>Batch</h1>
    <p class="lede">Drop a txt / jsonl / json / csv, or paste one prompt per line. JSONL is the native protocol.</p>
    <form class="panel" id="batch-form">
      <div class="drop" id="drop">Drop prompts.txt / prompts.jsonl here, or choose a file
        <div style="margin-top:12px"><input type="file" name="file" accept=".txt,.jsonl,.json,.csv" /></div>
      </div>
      <label>Or paste</label>
      <textarea name="text" placeholder="a beautiful forest&#10;a futuristic city"></textarea>
      ${settingsGrid(d, meta)}
      <div class="actions"><button type="submit">Run batch</button><span class="mono" id="job-id"></span></div>
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
    const button = event.target.querySelector("button");
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
    <h1>Jobs</h1>
    <p class="lede">Every generate or batch is a Job. Resume only retries the unfinished frames.</p>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>STATUS</th><th>IMAGES</th><th>MODEL</th><th>GPU</th><th>COST</th><th></th></tr></thead>
        <tbody>
          ${jobs.map((job) => `
            <tr>
              <td class="mono"><a href="#/job/${job.id}">${job.id}</a></td>
              <td><span class="pill ${job.status}">${job.status}</span></td>
              <td>${job.completed_images}/${job.total_images}</td>
              <td>${job.model}</td>
              <td>${job.gpu}</td>
              <td class="mono">${formatUsd(job.cost_usd)}</td>
              <td>
                <button class="ghost" data-job="${job.id}">Trace</button>
                <button class="ghost" data-gallery="${job.id}">Gallery</button>
                <button class="ghost" data-resume="${job.id}">Resume</button>
              </td>
            </tr>`).join("") || `<tr><td colspan="7">No jobs yet.</td></tr>`}
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
  main.innerHTML = `
    <h1>Gallery</h1>
    <p class="lede">${data.total} images. Hover a card for prompt, seed, GPU, and time. Nothing is anonymous.</p>
    <form class="toolbar" id="filters">
      <input name="q" placeholder="search prompt" value="${q}" />
      <input name="job" placeholder="job id" value="${job}" />
      <select name="sort">
        ${["newest", "oldest", "fastest", "slowest"].map((item) => `<option ${item === sort ? "selected" : ""}>${item}</option>`).join("")}
      </select>
      <select name="per">
        ${[50, 100, 200].map((item) => `<option ${item === per ? "selected" : ""}>${item}</option>`).join("")}
      </select>
      <button type="submit" class="ghost">Filter</button>
    </form>
    <div class="gallery">
      ${data.items.map((image) => `
        <article class="card" data-id="${image.id}">
          <img src="/api/images/${image.id}/file" alt="" />
          <div class="cap">${image.prompt}</div>
          <div class="hover">
            <div>${image.prompt}</div>
            <div class="mono" style="margin-top:10px">seed ${image.seed}<br>${image.model}<br>${image.gpu}<br>${image.latency_ms ? (image.latency_ms / 1000).toFixed(2) + "s" : ""}<br>${formatUsd(image.cost_usd)}</div>
          </div>
        </article>`).join("") || "<p>No images yet. Generate something.</p>"}
    </div>
    <div class="pager">
      <button class="ghost" ${page <= 1 ? "disabled" : ""} id="prev">Prev</button>
      <span>page ${data.page} · ${data.per_page}/page</span>
      <button class="ghost" ${page * per >= data.total ? "disabled" : ""} id="next">Next</button>
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

async function openLightbox(imageId) {
  const image = await api(`/api/images/${imageId}`);
  lightbox.classList.remove("hidden");
  lightbox.innerHTML = `
    <img src="/api/images/${image.id}/file" alt="" />
    <div class="meta">
      <h2>Frame</h2>
      <p>${image.prompt}</p>
      <dl>
        <dt>Seed</dt><dd class="mono">${image.seed}</dd>
        <dt>Model</dt><dd>${image.model}</dd>
        <dt>Requested GPU</dt><dd>${image.gpu}</dd>
        <dt>Actual GPU</dt><dd>${image.actual_gpu || "—"} ${image.actual_device ? `(${image.actual_device})` : ""}</dd>
        <dt>Steps</dt><dd>${image.steps}</dd>
        <dt>Size</dt><dd>${image.width} × ${image.height}</dd>
        <dt>Time</dt><dd>${image.latency_ms ? (image.latency_ms / 1000).toFixed(2) + "s" : "—"}</dd>
        <dt>Infer</dt><dd>${image.infer_ms != null ? image.infer_ms.toFixed(0) + " ms" : "—"}</dd>
        <dt>Cost</dt><dd>${formatUsd(image.cost_usd)}</dd>
        <dt>Call</dt><dd class="mono">${image.modal_function_call_id || "—"}</dd>
        <dt>Input</dt><dd class="mono">${image.modal_input_id || "—"}</dd>
        <dt>Job</dt><dd class="mono"><a href="#/job/${image.job_id}">${image.job_id}</a></dd>
      </dl>
      <div class="actions" style="margin-top:18px">
        <button id="copy">Copy prompt</button>
        <button class="ghost" id="regen">Regenerate</button>
        <a class="btn ghost" href="/api/images/${image.id}/file" download>Download</a>
        <button class="ghost" id="close">Close</button>
      </div>
    </div>
  `;
  document.getElementById("close").onclick = () => lightbox.classList.add("hidden");
  document.getElementById("copy").onclick = async () => {
    await navigator.clipboard.writeText(image.prompt);
  };
  document.getElementById("regen").onclick = async () => {
    const job = await api(`/api/images/${image.id}/regenerate`, { method: "POST" });
    lightbox.classList.add("hidden");
    location.hash = `#/jobs`;
    alert(`queued ${job.id}`);
  };
}

function benchmarkPage(meta) {
  main.innerHTML = `
    <h1>Benchmark</h1>
    <p class="lede">Compare GPUs with the same prompt. Cost uses Modal's published $/second list.</p>
    <div class="panel">
      <p>Run this from the CLI for real numbers:</p>
      <p class="mono">uv run modal-sana benchmark --gpu L40S,RTX-PRO-6000 --count 8</p>
      <table>
        <thead><tr><th>GPU</th><th>$/hour</th><th>VRAM</th><th>Batch</th><th>Notes</th></tr></thead>
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
    <h1>Settings</h1>
    <p class="lede">Local workbench. The EXE never runs the model — Modal does.</p>
    <div class="panel">
      <div class="check"><span>Data dir</span><span class="mono">${meta.defaults.data_dir}</span></div>
      <div class="check"><span>Default model</span><span>${meta.defaults.model}</span></div>
      <div class="check"><span>Default GPU</span><span>${meta.defaults.gpu}</span></div>
      <div class="check"><span>Modal path</span><span class="mono">${meta.runtime?.would_use || "auto"} · ${meta.runtime?.app_name || "modal-sana"}</span></div>
      <p class="lede">${meta.runtime?.note || ""}</p>
      <h3>Doctor</h3>
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
    link.classList.toggle("active", link.dataset.page === page || (page.startsWith("job/") && link.dataset.page === "jobs"));
  });
  if (!state.meta) state.meta = await api("/api/meta");
  if (page === "generate") generatePage(state.meta);
  else if (page === "batch") batchPage(state.meta);
  else if (page === "jobs") await jobsPage();
  else if (page.startsWith("job/")) await jobDetailPage(page.slice(4));
  else if (page === "gallery") await galleryPage(params);
  else if (page === "benchmark") benchmarkPage(state.meta);
  else if (page === "settings") await settingsPage(state.meta);
  else generatePage(state.meta);
}

window.addEventListener("hashchange", () => render());
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) lightbox.classList.add("hidden");
});

(async () => {
  try {
    const [doctor, meta] = await Promise.all([api("/api/doctor"), api("/api/meta")]);
    state.meta = meta;
    const ver = meta.version ? `v${meta.version} · ` : "";
    if (!doctor.ready) {
      railStatus.textContent = `${ver}modal not signed in`;
      railStatus.className = "rail-foot bad";
    } else if (meta.runtime?.available) {
      railStatus.textContent = `${ver}deployed · snapshots on`;
      railStatus.className = "rail-foot ok";
    } else {
      railStatus.textContent = `${ver}deploy first · ${meta.runtime?.deploy_command || "modal deploy"}`;
      railStatus.className = "rail-foot bad";
    }
  } catch {
    railStatus.textContent = "api offline";
  }
  await render();
})();
