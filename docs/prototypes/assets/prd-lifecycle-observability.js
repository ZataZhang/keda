(() => {
  const timelines = {
    normal: [
      { time: "13:42", title: "进入执行队列", detail: "由 Roadmap 手动启动，创建稳定的 PRD run 标识。", duration: "等待 12m", type: "wait", actor: "operator", event: "prd.queued" },
      { time: "13:54", title: "Agent 开始实现", detail: "Issue #417 进入 agent/running，首次 attempt 使用 codex。", duration: "执行 48m", actor: "codex", event: "phase.started" },
      { time: "14:42", title: "首次验证未通过", detail: "前端真实入口断言失败，保留失败原因并进入自动修复。", duration: "7m", type: "failure", actor: "verifier", event: "attempt.failed" },
      { time: "14:49", title: "修复后重新验证", detail: "第二次 attempt 复用同一 PRD run，重新收集最终代码树证据。", duration: "35m", actor: "codex", event: "attempt.recovered" },
      { time: "15:24", title: "等待独立审查", detail: "机器证据已齐，正在等待 verifier 返回结论。", duration: "进行中", type: "wait", actor: "verifier", event: "phase.waiting" },
    ],
    failure: [
      { time: "13:42", title: "进入执行队列", detail: "由 Roadmap 手动启动，创建稳定的 PRD run 标识。", duration: "等待 12m", type: "wait", actor: "operator", event: "prd.queued" },
      { time: "13:54", title: "Agent 开始实现", detail: "Issue #417 进入 agent/running，首次 attempt 使用 codex。", duration: "执行 48m", actor: "codex", event: "phase.started" },
      { time: "14:42", title: "连续验证失败", detail: "三次尝试均未跨过真实浏览器入口，PRD 标记为失败但历史完整保留。", duration: "29m", type: "failure", actor: "verifier", event: "prd.failed" },
      { time: "15:11", title: "人工触发重试", detail: "从失败点新建 attempt，不覆盖旧失败事件与耗时。", duration: "执行 21m", actor: "operator", event: "attempt.retried" },
      { time: "15:32", title: "等待外部依赖", detail: "依赖的 CI 服务不可用，阻塞时间单独累计，不计入有效执行时间。", duration: "阻塞中", type: "wait", actor: "system", event: "prd.blocked" },
    ],
  };
  const uiState = { view: "roadmap", scenario: "normal" };
  const timeline = document.querySelector("#timeline");
  const inspector = document.querySelector("#event-inspector");
  const pageTitle = document.querySelector("#page-title");

  function renderTimeline() {
    timeline.innerHTML = timelines[uiState.scenario].map((entry, index) => `
      <li class="timeline-event hotspot-target${entry.type ? ` is-${entry.type}` : ""}" tabindex="0" role="button" data-event-index="${index}" aria-label="查看事件：${entry.title}">
        <time>${entry.time}</time><span class="event-marker">${entry.type === "failure" ? "!" : entry.type === "wait" ? "◷" : "✓"}</span>
        <span class="timeline-copy"><strong>${entry.title}</strong><small>${entry.detail}</small></span><span class="event-duration">${entry.duration}</span>
      </li>`).join("");
    document.querySelector("#detail-status").textContent = uiState.scenario === "failure" ? "已阻塞" : "执行中";
    document.querySelector("#detail-status").className = `state ${uiState.scenario === "failure" ? "failed" : "running"}`;
    document.querySelector("#current-phase").textContent = uiState.scenario === "failure" ? "等待外部依赖" : "实现与验证";
    document.querySelector("#attempt-count").textContent = uiState.scenario === "failure" ? "4 次" : "3 次";
    document.querySelector("#attempt-note").textContent = uiState.scenario === "failure" ? "3 次失败，1 次重试" : "1 次失败后恢复";
  }

  function showView(view) {
    uiState.view = view;
    document.querySelectorAll("[data-view]").forEach((page) => page.classList.toggle("is-visible", page.dataset.view === view));
    document.querySelectorAll(".product-sidebar [data-view-link]").forEach((link) => link.classList.toggle("is-active", link.dataset.viewLink === view));
    pageTitle.textContent = view === "stats" ? "统计" : "Roadmap";
    window.location.hash = view;
  }

  function openInspector(index) {
    const entry = timelines[uiState.scenario][index];
    if (!entry) return;
    document.querySelector("#inspector-title").textContent = entry.title;
    document.querySelector("#inspector-detail").textContent = entry.detail;
    document.querySelector("#inspector-meta").innerHTML = `<div><dt>事件类型</dt><dd>${entry.event}</dd></div><div><dt>发生时间</dt><dd>2026-09-21 ${entry.time}:00 +08:00</dd></div><div><dt>执行者</dt><dd>${entry.actor}</dd></div><div><dt>持续时间</dt><dd>${entry.duration}</dd></div><div><dt>关联对象</dt><dd>keda-main / PRD run prdrun_01K5R4</dd></div>`;
    inspector.hidden = false;
  }

  document.addEventListener("click", (event) => {
    const viewLink = event.target.closest("[data-view-link]");
    if (viewLink) { event.preventDefault(); showView(viewLink.dataset.viewLink); return; }
    const scenarioButton = event.target.closest("[data-scenario]");
    if (scenarioButton) {
      uiState.scenario = scenarioButton.dataset.scenario;
      document.querySelectorAll("[data-scenario]").forEach((button) => button.classList.toggle("is-active", button === scenarioButton));
      inspector.hidden = true; renderTimeline(); return;
    }
    const eventRow = event.target.closest("[data-event-index]");
    if (eventRow) openInspector(Number(eventRow.dataset.eventIndex));
    if (event.target.closest("[data-close-inspector]")) inspector.hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    const eventRow = event.target.closest("[data-event-index]");
    if (eventRow && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); openInspector(Number(eventRow.dataset.eventIndex)); }
    if (event.key === "Escape") inspector.hidden = true;
  });
  document.addEventListener("prototype:reset", () => {
    uiState.scenario = "normal";
    document.querySelectorAll("[data-scenario]").forEach((button) => button.classList.toggle("is-active", button.dataset.scenario === "normal"));
    inspector.hidden = true; renderTimeline(); showView("roadmap");
  });
  renderTimeline();
  showView(window.location.hash === "#stats" ? "stats" : "roadmap");
})();
