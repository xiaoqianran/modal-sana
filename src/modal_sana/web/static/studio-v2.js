const v2Main = document.getElementById("main");

function v2El(tag, className = "", html = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html) node.innerHTML = html;
  return node;
}

function v2Escape(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function v2Money(text) {
  const match = String(text || "").match(/\$\s*([0-9.]+)/);
  return match ? Number(match[1]) : 0;
}

function v2Number(text) {
  const match = String(text || "").match(/([0-9]+(?:\.[0-9]+)?)/);
  return match ? Number(match[1]) : 0;
}

function v2Head(kicker, title, copy, actions = "") {
  const current = v2Main.querySelector(":scope > .page-head");
  if (current) {
    const eyebrow = current.querySelector(".eyebrow");
    const heading = current.querySelector("h1");
    const lead = current.querySelector(".lede");
    if (eyebrow) eyebrow.textContent = kicker;
    if (heading) heading.textContent = title;
    if (lead) lead.textContent = copy;
    if (actions) {
      let holder = current.querySelector(".page-actions");
      if (!holder) {
        holder = v2El("div", "page-actions");
        current.appendChild(holder);
      }
      holder.innerHTML = actions;
    }
    return current;
  }

  const oldTitle = v2Main.querySelector(":scope > h1");
  const oldLead = v2Main.querySelector(":scope > .lede");
  const head = v2El("div", "page-head");
  head.innerHTML = `
    <div class="page-head-copy">
      <span class="eyebrow">${kicker}</span>
      <h1>${title}</h1>
      <p class="lede">${copy}</p>
    </div>
    ${actions ? `<div class="page-actions">${actions}</div>` : ""}`;
  v2Main.insertBefore(head, v2Main.firstChild);
  oldTitle?.remove();
  oldLead?.remove();
  return head;
}

function v2Metric(label, value, note = "", tone = "") {
  return `<div class="v2-metric ${tone}"><span>${label}</span><strong>${value}</strong>${note ? `<small>${note}</small>` : ""}</div>`;
}

function v2StatusClass(text) {
  if (/完成/.test(text)) return "is-complete";
  if (/进行中|运行|等待/.test(text)) return "is-running";
  if (/失败|取消/.test(text)) return "is-problem";
  return "";
}

function v2Page() {
  const name = location.pathname.replace(/^\/+|\/+$/g, "") || "generate";
  return name;
}

function enhanceGalleryV2() {
  if (v2Main.dataset.v2Page === "gallery") return;
  const gallery = v2Main.querySelector(":scope > .gallery");
  const toolbar = v2Main.querySelector("#filters");
  if (!gallery || !toolbar) return;
  v2Main.dataset.v2Page = "gallery";

  const cards = [...gallery.querySelectorAll(".card")];
  const totalLead = v2Main.querySelector(":scope > .page-head .lede") || v2Main.querySelector(":scope > .lede");
  const total = totalLead ? v2Number(totalLead.textContent) : cards.length;
  v2Head(
    "Library",
    "图库",
    "把生成结果当作可搜索的视觉资产，而不是一次性输出。",
    '<a class="btn" href="/generate">生成新图片</a><a class="btn ghost" href="/batch">批量生成</a>',
  );

  const search = toolbar.querySelector("#filter-q");
  const job = toolbar.querySelector("#filter-job");
  const sort = toolbar.querySelector("#filter-sort");
  const per = toolbar.querySelector("#filter-per");
  if (search) search.placeholder = "搜索提示词，例如 portrait / Tokyo / product…";
  if (job) job.placeholder = "按任务 ID 精确筛选";

  toolbar.classList.add("v2-filterbar");
  const filterTop = v2El("div", "v2-filter-top", `
    <div>
      <span class="eyebrow">Browse</span>
      <strong>${total || cards.length} 张资产</strong>
    </div>
    <button type="button" class="ghost quiet" id="v2-toggle-gallery-meta">隐藏图片信息</button>`);
  toolbar.parentNode.insertBefore(filterTop, toolbar);

  const active = v2El("div", "v2-filter-state");
  const syncActive = () => {
    const pieces = [];
    if (search?.value) pieces.push(`关键词：${v2Escape(search.value)}`);
    if (job?.value) pieces.push(`任务：${v2Escape(job.value)}`);
    if (sort?.selectedOptions?.[0]) pieces.push(`排序：${v2Escape(sort.selectedOptions[0].textContent)}`);
    if (per?.value) pieces.push(`每页 ${v2Escape(per.value)}`);
    active.innerHTML = pieces.map((item) => `<span>${item}</span>`).join("");
  };
  toolbar.insertAdjacentElement("afterend", active);
  toolbar.addEventListener("input", syncActive);
  toolbar.addEventListener("change", syncActive);
  syncActive();

  cards.forEach((card, index) => {
    card.classList.add("v2-asset-card");
    card.style.setProperty("--asset-delay", `${Math.min(index, 12) * 18}ms`);
    const cap = card.querySelector(".cap");
    const img = card.querySelector("img");
    if (cap && !cap.querySelector(".v2-card-open")) {
      const open = v2El("span", "v2-card-open", "查看详情 ↗");
      cap.appendChild(open);
    }
    if (img) img.loading = index > 8 ? "lazy" : "eager";
  });

  const toggle = document.getElementById("v2-toggle-gallery-meta");
  toggle?.addEventListener("click", () => {
    const compact = gallery.classList.toggle("is-image-only");
    toggle.textContent = compact ? "显示图片信息" : "隐藏图片信息";
  });

  const pager = v2Main.querySelector(":scope > .pager");
  pager?.classList.add("v2-pager");
}

function enhanceJobsV2() {
  if (v2Main.dataset.v2Page === "jobs") return;
  const table = v2Main.querySelector(":scope > .panel table");
  if (!table) return;
  v2Main.dataset.v2Page = "jobs";

  v2Head(
    "Operations",
    "任务中心",
    "先看异常和正在运行的任务，再处理历史记录。",
    '<a class="btn" href="/generate">新建生成</a><a class="btn ghost" href="/batch">新建批量</a>',
  );

  const rows = [...table.querySelectorAll("tbody tr")].filter((row) => row.querySelector("td"));
  const stats = { total: 0, running: 0, complete: 0, problem: 0, images: 0, cost: 0 };
  rows.forEach((row) => {
    const cells = row.querySelectorAll("td");
    if (cells.length < 6) return;
    stats.total += 1;
    const status = cells[1].textContent.trim();
    if (/完成/.test(status)) stats.complete += 1;
    else if (/失败|取消/.test(status)) stats.problem += 1;
    else stats.running += 1;
    const imagePair = cells[2].textContent.match(/(\d+)\s*\/\s*(\d+)/);
    if (imagePair) stats.images += Number(imagePair[1]);
    stats.cost += v2Money(cells[5].textContent);
  });

  const overview = v2El("section", "v2-overview v2-jobs-overview", `
    ${v2Metric("任务总数", stats.total, `${stats.images} 张已完成`)}
    ${v2Metric("正在处理", stats.running, "等待 / 运行", stats.running ? "is-active" : "")}
    ${v2Metric("已完成", stats.complete, "可直接进入图库", "is-success")}
    ${v2Metric("需关注", stats.problem, stats.problem ? "失败或已取消" : "当前无异常", stats.problem ? "is-danger" : "")}
    ${v2Metric("累计费用", `$${stats.cost.toFixed(stats.cost < 1 ? 4 : 2)}`, "本机任务记录")}`);
  const panel = table.closest(".panel");
  panel.parentNode.insertBefore(overview, panel);

  const controls = v2El("div", "v2-section-bar", `
    <div><span class="eyebrow">Queue</span><strong>全部任务</strong><small>按状态快速聚焦</small></div>
    <div class="v2-segments" role="group" aria-label="任务状态筛选">
      <button type="button" class="is-selected" data-v2-job-filter="all">全部</button>
      <button type="button" data-v2-job-filter="running">进行中</button>
      <button type="button" data-v2-job-filter="complete">完成</button>
      <button type="button" data-v2-job-filter="problem">异常</button>
    </div>`);
  panel.parentNode.insertBefore(controls, panel);
  panel.classList.add("v2-table-panel", "v2-jobs-table");
  table.classList.add("v2-responsive-table");

  rows.forEach((row) => {
    const status = row.querySelectorAll("td")[1]?.textContent.trim() || "";
    row.dataset.v2Status = /完成/.test(status) ? "complete" : /失败|取消/.test(status) ? "problem" : "running";
    row.classList.add(v2StatusClass(status));
  });

  controls.querySelectorAll("[data-v2-job-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      controls.querySelectorAll("button").forEach((item) => item.classList.remove("is-selected"));
      button.classList.add("is-selected");
      const filter = button.dataset.v2JobFilter;
      rows.forEach((row) => { row.hidden = filter !== "all" && row.dataset.v2Status !== filter; });
    });
  });
}

function enhanceJobDetailV2() {
  if (v2Main.dataset.v2Page === "job-detail") return;
  const summary = v2Main.querySelector("#job-summary")?.closest(".panel");
  if (!summary) return;
  v2Main.dataset.v2Page = "job-detail";

  const pairs = [...summary.querySelectorAll(":scope > .check")];
  const find = (name) => pairs.find((row) => row.firstElementChild?.textContent.trim() === name)?.lastElementChild?.textContent.trim() || "—";
  const status = find("状态");
  const images = find("图片");
  const gpuSeconds = find("GPU 秒");
  const cost = find("估算费用");
  const gpu = find("请求的 GPU / 模型");

  v2Head(
    "Job",
    "任务详情",
    "从结果、耗时和费用三个角度判断这次生成是否值得复用。",
    '<a class="btn ghost" href="/jobs">返回任务</a><a class="btn" href="/gallery">查看图库</a>',
  );

  const overview = v2El("section", "v2-overview v2-job-detail-overview", `
    ${v2Metric("状态", v2Escape(status), gpu, v2StatusClass(status))}
    ${v2Metric("图片", v2Escape(images), "完成 / 总数")}
    ${v2Metric("GPU 时间", v2Escape(gpuSeconds), "计费 GPU 秒")}
    ${v2Metric("估算费用", v2Escape(cost), "对应本次任务")}`);
  summary.parentNode.insertBefore(overview, summary);
  summary.classList.add("v2-detail-panel");

  const timeline = v2Main.querySelector(".timeline");
  timeline?.classList.add("v2-timeline");
  const tables = v2Main.querySelectorAll(".table-wrap table");
  tables.forEach((table) => table.classList.add("v2-responsive-table"));
}

function enhanceCostV2() {
  if (v2Main.dataset.v2Page === "cost") return;
  const snapshots = v2Main.querySelector("#ledger-snaps");
  const filters = v2Main.querySelector("#cost-filters");
  if (!snapshots || !filters) return;
  v2Main.dataset.v2Page = "cost";

  v2Head(
    "Spend",
    "费用与预算",
    "先判断本月烧了多少，再追到具体 GPU 调用。不要让计费细节挡住预算判断。",
    '<a class="btn" href="/generate">继续生成</a><a class="btn ghost" href="/benchmark">比较 GPU</a>',
  );

  const firstPanel = v2Main.querySelector(":scope > .panel");
  const checks = firstPanel ? [...firstPanel.querySelectorAll(":scope > .check")] : [];
  const valueFor = (label) => checks.find((row) => row.firstElementChild?.textContent.trim() === label)?.lastElementChild?.textContent.trim() || "—";
  const month = valueFor("本月计量");
  const remain = valueFor("剩余（估）");
  const workspace = valueFor("工作区");
  const snapCards = [...snapshots.querySelectorAll(".snap")];
  const snapValue = (name) => {
    const card = snapCards.find((item) => item.querySelector("span")?.textContent.trim() === name);
    return card?.querySelector("strong")?.textContent.trim() || "—";
  };

  const budget = v2El("section", "v2-cost-hero", `
    <div class="v2-cost-primary">
      <span class="eyebrow">This month</span>
      <span class="v2-cost-label">本月已计量</span>
      <strong>${v2Escape(month)}</strong>
      <small>${v2Escape(workspace)} · Modal 账单仍以官方 billing 为准</small>
    </div>
    <div class="v2-cost-secondary">
      ${v2Metric("预计剩余", v2Escape(remain), "按当前账单估算")}
      ${v2Metric("今天", v2Escape(snapValue("天")), "日视图账本")}
      ${v2Metric("本周", v2Escape(snapValue("周")), "周视图账本")}
    </div>`);
  firstPanel?.parentNode.insertBefore(budget, firstPanel);
  firstPanel?.classList.add("v2-cost-account");
  snapshots.classList.add("v2-snapshots");

  const sections = [...v2Main.querySelectorAll(":scope > h2.section")];
  sections.forEach((heading) => {
    const text = heading.textContent.trim();
    if (text === "单价") {
      heading.textContent = "GPU 单价参考";
      const panel = heading.nextElementSibling;
      panel?.classList.add("v2-price-panel");
    }
    if (text === "每一笔调用") heading.textContent = "调用明细";
    if (text === "本机任务") heading.textContent = "任务费用汇总";
  });
  filters.classList.add("v2-filterbar", "v2-cost-filters");
  const events = v2Main.querySelector(".panel.events");
  events?.classList.add("v2-events");
  v2Main.querySelectorAll(".event").forEach((event) => event.classList.add("v2-event"));
  v2Main.querySelectorAll(".table-wrap table").forEach((table) => table.classList.add("v2-responsive-table"));
}

function enhanceBenchmarkV2() {
  if (v2Main.dataset.v2Page === "benchmark") return;
  const table = v2Main.querySelector(":scope > .panel table");
  if (!table) return;
  v2Main.dataset.v2Page = "benchmark";

  v2Head(
    "Compare",
    "GPU 选择器",
    "先看价格、显存和推荐批大小；真实性能用同一提示词跑 benchmark 再决定。",
    '<a class="btn" href="/generate">去生成</a><a class="btn ghost" href="/cost">查看费用</a>',
  );

  const panel = table.closest(".panel");
  const command = [...panel.querySelectorAll("p")].find((item) => item.classList.contains("mono"));
  if (command) {
    const commandBox = v2El("section", "v2-command-card", `
      <div><span class="eyebrow">Real benchmark</span><strong>跑一组真实对照</strong><small>同模型、同提示词、同张数，GPU 才有可比性。</small></div>
      <code>${v2Escape(command.textContent.trim())}</code>
      <button type="button" class="ghost quiet" id="v2-copy-benchmark">复制命令</button>`);
    panel.parentNode.insertBefore(commandBox, panel);
    const intro = command.previousElementSibling;
    intro?.remove();
    command.remove();
    document.getElementById("v2-copy-benchmark")?.addEventListener("click", async (event) => {
      const text = commandBox.querySelector("code")?.textContent || "";
      try {
        await navigator.clipboard.writeText(text);
        event.currentTarget.textContent = "已复制";
      } catch {
        event.currentTarget.textContent = "复制失败";
      }
    });
  }

  const rows = [...table.querySelectorAll("tbody tr")];
  const gpuCards = rows.map((row) => {
    const cells = row.querySelectorAll("td");
    if (cells.length < 5) return "";
    const gpu = cells[0].textContent.trim();
    const price = cells[1].textContent.trim();
    const vram = cells[2].textContent.trim();
    const batch = cells[3].textContent.trim();
    const notes = cells[4].textContent.trim();
    return `<article class="v2-gpu-card">
      <div class="v2-gpu-name"><span class="eyebrow">GPU</span><h2>${v2Escape(gpu)}</h2></div>
      <div class="v2-gpu-spec"><span>小时单价</span><strong>${v2Escape(price)}</strong></div>
      <div class="v2-gpu-spec"><span>显存</span><strong>${v2Escape(vram)}</strong></div>
      <div class="v2-gpu-spec"><span>推荐批量</span><strong>${v2Escape(batch)}</strong></div>
      <p>${v2Escape(notes)}</p>
      <a href="/generate" class="btn ghost quiet">开始生成</a>
    </article>`;
  }).join("");
  const cards = v2El("section", "v2-gpu-grid", gpuCards);
  panel.parentNode.insertBefore(cards, panel);
  panel.classList.add("v2-raw-benchmark");
  const rawTitle = v2El("button", "ghost quiet v2-raw-toggle", "查看原始规格表");
  panel.parentNode.insertBefore(rawTitle, panel);
  panel.hidden = true;
  rawTitle.addEventListener("click", () => {
    panel.hidden = !panel.hidden;
    rawTitle.textContent = panel.hidden ? "查看原始规格表" : "收起原始规格表";
  });
}

function enhanceSettingsV2() {
  if (v2Main.dataset.v2Page === "settings") return;
  const panel = v2Main.querySelector(":scope > .panel");
  const checksRoot = panel?.querySelector(".checks");
  if (!panel || !checksRoot) return;
  v2Main.dataset.v2Page = "settings";

  v2Head(
    "Environment",
    "环境与默认配置",
    "这里不是参数堆放区：先确认环境健康，再看默认模型、GPU 和 Modal 路径。",
    '<a class="btn" href="/generate">返回生成</a>',
  );

  const configRows = [...panel.querySelectorAll(":scope > .check")];
  const valueFor = (label) => configRows.find((row) => row.firstElementChild?.textContent.trim() === label)?.lastElementChild?.textContent.trim() || "—";
  const checks = [...checksRoot.querySelectorAll(".check")];
  const okCount = checks.filter((row) => row.querySelector(".ok")).length;
  const failed = checks.length - okCount;
  const runtime = valueFor("Modal 路径");
  const model = valueFor("默认模型");
  const gpu = valueFor("默认 GPU");
  const dataDir = valueFor("数据目录");

  const health = v2El("section", "v2-settings-hero", `
    <div class="v2-health-score ${failed ? "has-problem" : "is-healthy"}">
      <span class="v2-health-icon">${failed ? "!" : "✓"}</span>
      <div><span class="eyebrow">Health</span><strong>${failed ? `${failed} 项需要处理` : "环境正常"}</strong><small>${okCount} / ${checks.length} 项检查通过</small></div>
    </div>
    <div class="v2-settings-summary">
      ${v2Metric("默认模型", v2Escape(model), "新任务默认值")}
      ${v2Metric("默认 GPU", v2Escape(gpu), "新任务默认值")}
      ${v2Metric("Modal 路径", v2Escape(runtime), "实际运行策略")}
      ${v2Metric("数据目录", v2Escape(dataDir), "本地结果与任务记录")}
    </div>`);
  panel.parentNode.insertBefore(health, panel);

  panel.classList.add("v2-settings-panel");
  const heading = panel.querySelector("h2.section");
  if (heading) heading.textContent = "环境体检";
  checksRoot.classList.add("v2-health-checks");
  checks.forEach((row) => row.classList.add(row.querySelector(".ok") ? "is-ok" : "is-bad"));
}

function enhanceLightboxV2() {
  const lightbox = document.getElementById("lightbox");
  if (!lightbox?.open || lightbox.dataset.v2Enhanced === "1") return;
  const meta = lightbox.querySelector(".meta");
  const stage = lightbox.querySelector(".lightbox-stage");
  if (!meta || !stage) return;
  lightbox.dataset.v2Enhanced = "1";
  lightbox.classList.add("v2-lightbox");
  const title = meta.querySelector("#lightbox-title");
  if (title) title.textContent = "图片详情";
  const prompt = meta.querySelector(":scope > p");
  if (prompt) {
    const block = v2El("div", "v2-prompt-block", `<span class="eyebrow">Prompt</span><p>${prompt.innerHTML}</p>`);
    prompt.replaceWith(block);
  }
  const dl = meta.querySelector("dl");
  dl?.classList.add("v2-meta-grid");
  stage.insertAdjacentHTML("afterbegin", '<span class="v2-stage-label">Generated asset</span>');
}

function enhanceV2() {
  if (!v2Main) return;
  const page = v2Page();
  if (page === "gallery") enhanceGalleryV2();
  else if (page === "jobs") enhanceJobsV2();
  else if (page.startsWith("job/")) enhanceJobDetailV2();
  else if (page === "cost") enhanceCostV2();
  else if (page === "benchmark") enhanceBenchmarkV2();
  else if (page === "settings") enhanceSettingsV2();
  enhanceLightboxV2();
}

if (v2Main) {
  const schedule = () => queueMicrotask(enhanceV2);
  new MutationObserver(schedule).observe(v2Main, { childList: true, subtree: false });
  const lightbox = document.getElementById("lightbox");
  if (lightbox) new MutationObserver(schedule).observe(lightbox, { childList: true, subtree: true, attributes: true, attributeFilter: ["open"] });
  schedule();
}
