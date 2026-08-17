const main = document.getElementById("main");

function money(value) {
  const n = Number(value || 0);
  return n < 0.01 ? `$${n.toFixed(6)}` : `$${n.toFixed(4)}`;
}

function ms(value) {
  const n = Number(value || 0);
  if (n >= 1000) return `${(n / 1000).toFixed(2)}s`;
  return `${n.toFixed(0)}ms`;
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function number(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return Number(value).toFixed(digits);
}

function diagnosticPanel(report, title = "成本诊断") {
  const phases = report.phases || {};
  const batch = report.batch || {};
  const memory = report.memory || {};
  const findings = report.findings || [];
  const panel = document.createElement("section");
  panel.className = "panel perf-cost-diagnosis";
  panel.dataset.jobId = report.job_id || "";
  panel.innerHTML = `
    <div class="side-card-head">
      <div><span class="eyebrow">Performance diagnosis</span><h2>${title}</h2></div>
      <span class="mono">${report.images || 0} 张</span>
    </div>
    <div class="check"><span>估算总成本</span><strong class="mono">${money(report.total_cost_usd)} · ${money(report.cost_per_100_images_usd)}/100 张</strong></div>
    <div class="check"><span>GPU 秒 / 图</span><strong class="mono">${number(report.gpu_seconds_per_image, 3)}s</strong></div>
    <div class="check"><span>推理</span><strong class="mono">${ms(phases.inference?.ms)} · ${percent(phases.inference?.share)} · ${money(phases.inference?.cost_usd)}</strong></div>
    <div class="check"><span>图片编码（仍占 GPU 容器）</span><strong class="mono">${ms(phases.encode_in_gpu_container?.ms)} · ${percent(phases.encode_in_gpu_container?.share)} · ${money(phases.encode_in_gpu_container?.cost_usd)}</strong></div>
    <div class="check"><span>冷启动 / GPU 搬运</span><strong class="mono">${ms(phases.load?.ms)} · ${percent(phases.load?.share)} · ${money(phases.load?.cost_usd)}</strong></div>
    <div class="check"><span>Batch</span><strong class="mono">请求 ${number(batch.avg_requested)} → 实际 ${number(batch.avg_effective)} · 回退 ${batch.fallback_events || 0} 次</strong></div>
    <div class="check"><span>显存峰值</span><strong class="mono">${memory.peak_gb == null ? "—" : `${number(memory.peak_gb)} GB`}${memory.oom_attempt_peak_gb == null ? "" : ` · OOM 尝试 ${number(memory.oom_attempt_peak_gb)} GB`}</strong></div>
    <div class="diagnosis-findings">
      ${findings.map((item) => `<p class="lede"><strong>${item.title}</strong> · ${item.detail}</p>`).join("")}
    </div>
    ${report.truncated ? '<p class="lede">该任务事件超过 200 条；当前诊断只读取前 200 条，建议后续增加服务端全量聚合。</p>' : ""}
  `;
  return panel;
}

async function getDiagnosis(jobId) {
  const response = await fetch(`/api/cost/diagnose?job_id=${encodeURIComponent(jobId)}`);
  if (!response.ok) throw new Error(`diagnose ${response.status}`);
  return response.json();
}

async function enhanceJob() {
  const match = location.pathname.match(/^\/job\/([^/]+)$/);
  if (!match || document.querySelector(".perf-cost-diagnosis")) return;
  try {
    const report = await getDiagnosis(match[1]);
    if (!main?.isConnected || !location.pathname.startsWith("/job/")) return;
    const anchor = document.getElementById("vram-telemetry") || main.querySelector(":scope > .panel");
    const panel = diagnosticPanel(report);
    if (anchor) anchor.insertAdjacentElement("afterend", panel);
    else main.appendChild(panel);
  } catch {}
}

async function enhanceCost() {
  if (location.pathname !== "/cost" || document.querySelector(".perf-cost-diagnosis")) return;
  try {
    const response = await fetch("/api/jobs");
    if (!response.ok) return;
    const jobs = await response.json();
    const job = jobs.find((item) => item.status === "completed" && Number(item.cost_usd || 0) > 0);
    if (!job) return;
    const report = await getDiagnosis(job.id);
    if (!main?.isConnected || location.pathname !== "/cost") return;
    const panel = diagnosticPanel(report, "最近任务为什么贵");
    const head = main.querySelector(":scope > .page-head");
    if (head) head.insertAdjacentElement("afterend", panel);
    else main.insertBefore(panel, main.firstChild);
  } catch {}
}

function enhance() {
  enhanceJob();
  enhanceCost();
}

if (main) {
  new MutationObserver(() => queueMicrotask(enhance)).observe(main, { childList: true, subtree: false });
  enhance();
}
