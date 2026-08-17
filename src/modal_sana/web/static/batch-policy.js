const GPU_BATCH_CAP = Object.freeze({
  T4: 2, L4: 4, A10: 4, L40S: 8, A100: 8, "A100-80GB": 12,
  "RTX-PRO-6000": 16, H100: 16, H200: 16, B200: 16, B300: 16,
});
const GPU_VRAM_GB = Object.freeze({
  T4: 16, L4: 24, A10: 24, L40S: 48, A100: 40, "A100-80GB": 80,
  "RTX-PRO-6000": 96, H100: 80, H200: 141, B200: 180, B300: 288,
});
const MODEL_BATCH_CAP = Object.freeze({
  "sana-sprint-1.6b": 16, "sana-sprint-0.6b": 16,
  "sana-1.6b": 4, "sana-1.5-1.6b": 4, "sana-1.5-4.8b": 2,
  "sana-1.6b-2k": 1, "sana-1.6b-4k": 1,
});

function recommendedBatch(model, gpu) {
  return Math.max(1, Math.min(MODEL_BATCH_CAP[model] ?? 4, GPU_BATCH_CAP[gpu] ?? 4));
}

function bindBatchPolicy(form) {
  if (!form || form.dataset.batchPolicy === "1") return;
  const model = form.querySelector('[name="model"]');
  const gpu = form.querySelector('[name="gpu"]');
  const batch = form.querySelector('[name="batch_size"]');
  if (!model || !gpu || !batch) return;
  form.dataset.batchPolicy = "1";
  const label = form.querySelector('label[for="field-batch_size"]');
  if (label) label.textContent = "GPU 批大小（自动推荐）";
  const applyAuto = () => {
    batch.value = String(recommendedBatch(model.value, gpu.value));
    batch.dataset.autoBatch = "1";
    batch.dispatchEvent(new Event("input", { bubbles: true }));
  };
  batch.addEventListener("input", () => {
    if (document.activeElement === batch) batch.dataset.autoBatch = "0";
  });
  model.addEventListener("change", applyAuto);
  gpu.addEventListener("change", applyAuto);
  applyAuto();
}

async function addJobVramSummary() {
  const match = location.pathname.match(/^\/job\/([^/]+)$/);
  if (!match || document.getElementById("vram-telemetry")) return;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(match[1])}`);
    if (!response.ok) return;
    const detail = await response.json();
    if (!location.pathname.startsWith("/job/")) return;
    const rows = detail.generations || [];
    const peaks = rows.map((row) => Number(row.vram_peak_mb || 0)).filter(Boolean);
    if (!peaks.length) return;
    const peakMb = Math.max(...peaks);
    const gpu = detail.job?.gpu || "";
    const totalGb = GPU_VRAM_GB[gpu] || 0;
    const peakGb = peakMb / 1024;
    const percent = totalGb ? (peakGb / totalGb) * 100 : 0;
    const panel = document.createElement("section");
    panel.id = "vram-telemetry";
    panel.className = "panel";
    panel.innerHTML = `
      <div class="side-card-head"><div><span class="eyebrow">Memory telemetry</span><h2>显存记录</h2></div></div>
      <div class="check"><span>成功批次峰值</span><strong class="mono">${peakGb.toFixed(2)} GB${totalGb ? ` / ${totalGb} GB · ${percent.toFixed(1)}%` : ""}</strong></div>
      <div class="check"><span>请求 batch</span><strong class="mono">${detail.job?.config?.batch_size ?? "自动"}</strong></div>
      <p class="lede">峰值按每次 CUDA batch 单独重置后记录；若大 batch OOM 并自动降档，这里展示成功批次峰值。</p>`;
    const firstPanel = document.querySelector("main > .panel");
    if (firstPanel) firstPanel.insertAdjacentElement("afterend", panel);
  } catch {}
}

function enhanceBatchPolicy() {
  bindBatchPolicy(document.getElementById("gen-form"));
  bindBatchPolicy(document.getElementById("batch-form"));
  addJobVramSummary();
}
const main = document.getElementById("main");
if (main) new MutationObserver(() => queueMicrotask(enhanceBatchPolicy)).observe(main, { childList: true, subtree: true });
enhanceBatchPolicy();
