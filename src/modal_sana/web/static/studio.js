const studioMain = document.getElementById("main");

function el(tag, className = "", html = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html) node.innerHTML = html;
  return node;
}

function pageHead(kicker, title, copy, actions = "") {
  const head = el("div", "page-head");
  head.innerHTML = `
    <div class="page-head-copy">
      <span class="eyebrow">${kicker}</span>
      <h1>${title}</h1>
      <p class="lede">${copy}</p>
    </div>
    ${actions ? `<div class="page-actions">${actions}</div>` : ""}`;
  return head;
}

function textNumber(node, pattern) {
  const match = String(node?.textContent || "").match(pattern);
  return match ? Number(match[1]) : 0;
}

function enhanceGenerate() {
  const form = document.getElementById("gen-form");
  if (!form || form.classList.contains("composer")) return;

  const oldTitle = studioMain.querySelector(":scope > h1");
  const oldLead = studioMain.querySelector(":scope > .lede:not(#runtime-line)");
  const head = pageHead(
    "Create",
    "生成图片",
    "把注意力放在画面上。模型、GPU 和费用在需要时再介入。",
    '<a class="btn ghost" href="/batch">批量生成</a><a class="btn ghost" href="/gallery">打开图库</a>',
  );
  studioMain.insertBefore(head, studioMain.firstChild);
  oldTitle?.remove();
  oldLead?.remove();

  const layout = el("div", "generate-layout");
  form.parentNode.insertBefore(layout, form);
  layout.appendChild(form);
  form.classList.add("composer");

  const prompt = form.querySelector("#field-prompt");
  const promptLabel = form.querySelector('label[for="field-prompt"]');
  const promptArea = el("div", "prompt-area");
  const top = el("div", "prompt-topline");
  if (promptLabel) {
    promptLabel.textContent = "描述你想生成的画面";
    top.appendChild(promptLabel);
  }
  top.insertAdjacentHTML("beforeend", '<span class="shortcut">Ctrl / ⌘ + Enter</span>');
  promptArea.appendChild(top);
  if (prompt) {
    prompt.placeholder = "例如：雨后的东京巷口，电影感夜景，霓虹倒映在湿润路面，低机位，35mm…";
    promptArea.appendChild(prompt);
  }
  const hint = el("div", "prompt-hint", '<span>主体、光线、镜头和风格越明确，结果通常越稳定。</span><span id="prompt-count">0 字</span>');
  promptArea.appendChild(hint);
  form.insertBefore(promptArea, form.firstChild);

  const quick = el("div", "quick-config");
  [form.querySelector("fieldset.settings"), form.querySelector("details.advanced"), form.querySelector("#will-apply")]
    .filter(Boolean).forEach((node) => quick.appendChild(node));
  const legend = quick.querySelector("fieldset.settings > legend");
  if (legend) legend.textContent = "模型与算力";
  const summary = quick.querySelector("details.advanced > summary");
  if (summary) summary.textContent = "高级参数";
  form.appendChild(quick);

  const meta = el("div", "composer-meta");
  [form.querySelector("#forecast"), form.querySelector("#fc-note"), studioMain.querySelector("#runtime-line"), form.querySelector("#applied-banner"), form.querySelector("#progress")]
    .filter(Boolean).forEach((node) => meta.appendChild(node));
  form.appendChild(meta);

  const actions = form.querySelector(".actions");
  const button = actions?.querySelector('button[type="submit"]');
  const jobId = actions?.querySelector("#job-id");
  const footer = el("div", "composer-footer", '<div class="forecast-summary"><span>预计本次</span><strong id="studio-total">计算中…</strong><small id="studio-time"></small></div>');
  if (button) {
    button.textContent = "生成图片";
    button.classList.add("generate-button");
    footer.appendChild(button);
  }
  if (jobId) {
    jobId.classList.add("visually-hidden");
    footer.appendChild(jobId);
  }
  actions?.remove();
  form.appendChild(footer);

  const output = el("aside", "output-rail");
  output.setAttribute("aria-label", "最近结果和当前配置");
  output.innerHTML = `
    <section class="side-card">
      <div class="side-card-head"><div><span class="eyebrow">Recent</span><h2>最近生成</h2></div><a href="/gallery">查看全部</a></div>
      <div class="recent-grid" id="studio-recents"><div class="recent-empty">正在读取最近结果…</div></div>
    </section>
    <section class="side-card runtime-card">
      <div class="side-card-head"><div><span class="eyebrow">Current</span><h2>当前配置</h2></div><a href="/settings">设置</a></div>
      <div class="runtime-row"><span>模型</span><strong id="studio-model">—</strong></div>
      <div class="runtime-row"><span>GPU</span><strong id="studio-gpu">—</strong></div>
      <div class="runtime-row"><span>状态</span><strong id="studio-status">${document.getElementById("mast-status")?.textContent || "检查中"}</strong></div>
    </section>`;
  layout.appendChild(output);

  const legacyCostLink = [...studioMain.querySelectorAll(":scope > .lede")].find((node) => node.querySelector('a[href="/cost"]'));
  legacyCostLink?.remove();

  const count = document.getElementById("prompt-count");
  const updateCount = () => { if (count && prompt) count.textContent = `${prompt.value.trim().length} 字`; };
  prompt?.addEventListener("input", updateCount);
  form.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  updateCount();

  const model = form.querySelector('[name="model"]');
  const gpu = form.querySelector('[name="gpu"]');
  const syncConfig = () => {
    const modelOut = document.getElementById("studio-model");
    const gpuOut = document.getElementById("studio-gpu");
    const statusOut = document.getElementById("studio-status");
    if (modelOut) modelOut.textContent = model?.selectedOptions?.[0]?.textContent?.split(" · ")[0] || model?.value || "—";
    if (gpuOut) gpuOut.textContent = gpu?.value || "—";
    if (statusOut) statusOut.textContent = document.getElementById("mast-status")?.textContent || "检查中";
  };
  form.addEventListener("change", syncConfig);
  syncConfig();

  const load = document.getElementById("fc-load");
  const gen = document.getElementById("fc-gen");
  const syncForecast = () => {
    const usd = textNumber(load, /\$([0-9.]+)/) + textNumber(gen, /\$([0-9.]+)/);
    const seconds = textNumber(load, /([0-9.]+)s/) + textNumber(gen, /([0-9.]+)s/);
    const total = document.getElementById("studio-total");
    const time = document.getElementById("studio-time");
    if (total) total.textContent = usd ? `$${usd.toFixed(4)}` : "计算中…";
    if (time) time.textContent = seconds ? `约 ${seconds.toFixed(seconds >= 10 ? 0 : 1)}s` : "";
  };
  if (load && gen) {
    new MutationObserver(syncForecast).observe(load, { childList: true, subtree: true, characterData: true });
    new MutationObserver(syncForecast).observe(gen, { childList: true, subtree: true, characterData: true });
    syncForecast();
  }

  loadRecents();
}

async function loadRecents() {
  const root = document.getElementById("studio-recents");
  if (!root) return;
  try {
    const response = await fetch("/api/gallery?page=1&per_page=4&sort=newest");
    if (!response.ok) throw new Error("gallery");
    const data = await response.json();
    if (!root.isConnected) return;
    if (!data.items?.length) {
      root.innerHTML = '<div class="recent-empty">还没有图片。生成完成后，最近结果会出现在这里。</div>';
      return;
    }
    root.innerHTML = data.items.slice(0, 4).map((image) => `
      <a class="recent-tile" href="/gallery" title="${escapeStudio(image.prompt)}">
        <img src="/api/images/${encodeURIComponent(image.id)}/file" alt="${escapeStudio(image.prompt)}" width="${image.width || 1024}" height="${image.height || 1024}" />
        <span>${escapeStudio(image.prompt)}</span>
      </a>`).join("");
  } catch {
    root.innerHTML = '<div class="recent-empty">图库暂时不可用，不影响直接生成。</div>';
  }
}

function enhanceBatch() {
  const form = document.getElementById("batch-form");
  if (!form || form.classList.contains("batch-layout")) return;
  const oldTitle = studioMain.querySelector(":scope > h1");
  const oldLead = studioMain.querySelector(":scope > .lede");
  studioMain.insertBefore(pageHead("Batch", "批量生成", "文件和粘贴二选一。配置一次，按同一套参数持续生成。", '<a class="btn ghost" href="/generate">单张生成</a><a class="btn ghost" href="/gallery">图库</a>'), studioMain.firstChild);
  oldTitle?.remove(); oldLead?.remove();

  form.classList.remove("sheet");
  form.classList.add("batch-layout");
  const source = el("section", "sheet source-card", '<span class="eyebrow">Input</span><h2 class="section">导入提示词</h2>');
  const settings = el("section", "sheet batch-settings", '<span class="eyebrow">Settings</span><h2 class="section">生成配置</h2>');
  form.insertBefore(source, form.firstChild);
  form.appendChild(settings);

  const drop = form.querySelector("#drop");
  if (drop) {
    const label = drop.querySelector("label");
    if (label) label.textContent = "拖入提示词文件，或点击选择";
    label?.insertAdjacentHTML("afterend", '<small class="drop-sub">支持 TXT / JSONL / JSON / CSV · JSONL 是原生批量协议</small>');
    source.appendChild(drop);
  }
  const textLabel = form.querySelector('label[for="field-text"]');
  const text = form.querySelector("#field-text");
  const divider = el("div", "source-divider", "或者直接粘贴");
  source.appendChild(divider);
  if (textLabel) { textLabel.textContent = "每行一条提示词"; source.appendChild(textLabel); }
  if (text) source.appendChild(text);
  const hint = el("div", "prompt-hint", '<span id="studio-batch-count">0 条</span><span>文件存在时优先使用文件。</span>');
  source.appendChild(hint);

  [form.querySelector("fieldset.settings"), form.querySelector("details.advanced")].filter(Boolean).forEach((node) => settings.appendChild(node));
  const batchLegend = settings.querySelector("fieldset.settings > legend");
  if (batchLegend) batchLegend.textContent = "模型与算力";
  const advanced = settings.querySelector("details.advanced > summary");
  if (advanced) advanced.textContent = "高级参数";
  const actions = form.querySelector(".actions");
  const button = actions?.querySelector('button[type="submit"]');
  if (button) { button.textContent = "开始批量"; button.classList.add("generate-button"); }
  if (actions) settings.appendChild(actions);
  const progress = form.querySelector("#progress");
  if (progress) settings.appendChild(progress);

  const counter = document.getElementById("studio-batch-count");
  const update = () => {
    if (!counter || !text) return;
    counter.textContent = `${text.value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).length} 条`;
  };
  text?.addEventListener("input", update); update();
}

function enhanceGallery() {
  if (studioMain.querySelector(":scope > .page-head")) return;
  const title = studioMain.querySelector(":scope > h1");
  if (!title || title.textContent.trim() !== "图库") return;
  const lead = studioMain.querySelector(":scope > .lede");
  const totalText = lead?.textContent?.split("。")[0] || "浏览生成结果";
  studioMain.insertBefore(pageHead("Library", "图库", `${totalText}。搜索提示词、排序并打开图片查看完整信息。`, '<a class="btn" href="/generate">生成新图片</a>'), studioMain.firstChild);
  title.remove(); lead?.remove();
}

function escapeStudio(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
}

function enhance() {
  const h1 = studioMain?.querySelector(":scope > h1");
  const text = h1?.textContent?.trim();
  if (document.getElementById("gen-form")) enhanceGenerate();
  else if (document.getElementById("batch-form")) enhanceBatch();
  else if (text === "图库") enhanceGallery();
}

if (studioMain) {
  new MutationObserver(() => queueMicrotask(enhance)).observe(studioMain, { childList: true, subtree: false });
  enhance();
}
