(() => {
  const state = { current: "running", timer: null };
  const card = document.querySelector("#verifier-card");
  const title = document.querySelector("#verifier-title");
  const icon = document.querySelector("#verifier-icon");
  const pill = document.querySelector("#verifier-state-pill");
  const description = document.querySelector("#verifier-description");
  const scopeNote = document.querySelector("#scope-note");
  const actionRow = document.querySelector("#action-row");
  const takeoverButton = document.querySelector("#takeover-button");
  const progress = document.querySelector("#handoff-progress");
  const result = document.querySelector("#handoff-result");
  const dialog = document.querySelector("#takeover-dialog");
  const scenarioMenu = document.querySelector("#scenario-menu");
  const scenarioToggle = document.querySelector("#scenario-toggle");
  const demoCompleteButton = document.querySelector("[data-demo-complete]");
  const timerText = document.querySelector("#timeline-age");
  const timelineTitle = document.querySelector("#timeline-title");
  const timelineNote = document.querySelector("#timeline-note");
  const prNote = document.querySelector("#pr-note");

  const scenarioCopy = {
    running: {
      title: "Verifier 正在运行", icon: "◌", pill: "进行中",
      description: "验证器正在检查当前 Draft PR。接管只会停止这个 Issue 的 verifier。",
    },
    pending: {
      title: "正在停止 verifier…", icon: "◌", pill: "处理中",
      description: "请求已发送。Runner 尚未确认进程退出，因此还没有完成交接。",
    },
    success: {
      title: "已交给你审查", icon: "✓", pill: "人工接管",
      description: "目标 verifier 已停止并回收。此次取消没有验证结论，PR 仍保持 Draft。",
    },
    completed: {
      title: "Verifier 已先完成", icon: "!", pill: "状态已变化",
      description: "接管请求到达时，这一轮 verifier 已结束。请刷新 Issue；已记录的验证结果不会被覆盖。",
    },
    stale: {
      title: "PR HEAD 已变化", icon: "!", pill: "需要复核",
      description: "当前 PR 已不再指向 verifier 启动时的提交。旧结果不会生效，自动处理保持暂停。",
    },
    failure: {
      title: "未能停止 verifier", icon: "!", pill: "未完成",
      description: "Runner 未能确认目标子进程已退出。Issue 尚未转交；你可以刷新状态后重试。",
    },
  };

  function clearTimer() {
    if (state.timer !== null) window.clearTimeout(state.timer);
    state.timer = null;
  }

  function setState(nextState) {
    clearTimer();
    state.current = nextState;
    const copy = scenarioCopy[nextState];
    card.dataset.state = nextState;
    title.textContent = copy.title;
    icon.textContent = copy.icon;
    pill.querySelector("b").textContent = copy.pill;
    description.textContent = copy.description;
    progress.hidden = nextState !== "pending";
    demoCompleteButton.hidden = nextState !== "pending";
    result.hidden = !["success", "completed", "stale", "failure"].includes(nextState);
    actionRow.hidden = nextState !== "running" && nextState !== "failure";
    scopeNote.hidden = nextState !== "running";
    takeoverButton.textContent = nextState === "failure" ? "重试接管 →" : "接管此 Issue →";
    takeoverButton.classList.toggle("failure-retry", nextState === "failure");
    renderResult(nextState);
    renderTimeline(nextState);
    renderPr(nextState);
    scenarioMenu.querySelectorAll("[data-scenario]").forEach((button) => {
      button.classList.toggle("is-selected", button.dataset.scenario === nextState);
      button.setAttribute("aria-pressed", String(button.dataset.scenario === nextState));
    });
  }

  function renderResult(nextState) {
    if (nextState === "success") {
      result.innerHTML = '<div class="result-banner"><span class="result-symbol">✓</span><div class="result-copy"><strong>已交给你审查 · verifier 未完成</strong><p>验证器已停止。没有 PASS、RED 或 YELLOW；自动处理暂停，等待你检查当前 HEAD。</p></div><a class="result-link hotspot-target" href="#pr-context">打开 Draft PR ↗</a></div>';
      return;
    }
    const messages = {
      completed: ["接管未执行", "verifier 已先结束。原有验证结果保留，未启动人工接管。"],
      stale: ["需要检查新的 HEAD", "旧验证结果不适用于当前提交。请在 GitHub 检查 PR #58 的最新变更。"],
      failure: ["交接尚未完成", "Runner 没有确认进程退出。刷新后可再次尝试；当前 Issue 仍由 Runner 管理。"],
    };
    if (!messages[nextState]) {
      result.replaceChildren();
      return;
    }
    const [heading, body] = messages[nextState];
    const retry = nextState === "failure" ? '<div class="result-actions"><button class="outline-button hotspot-target" type="button" data-retry>再次尝试</button></div>' : "";
    result.innerHTML = `<div class="result-banner is-warning"><span class="result-symbol">!</span><div class="result-copy"><strong>${heading}</strong><p>${body}</p></div></div>${retry}`;
    result.querySelector("[data-retry]")?.addEventListener("click", () => beginTakeover());
  }

  function renderTimeline(nextState) {
    const timelineCopy = {
      running: ["独立 verifier 开始运行", "目标 HEAD 4b19de2 · 14:31:08", "现在", "is-active"],
      pending: ["收到人工接管请求", "等待 Runner 确认 verifier 进程退出", "处理中", "is-active"],
      success: ["Issue 已交给人工审查", "verifier 已停止 · 无验证结论", "刚刚", "is-done"],
      completed: ["Verifier 已先完成", "接管请求未改变既有验证结果", "刚刚", "is-done"],
      stale: ["PR HEAD 与验证输入不一致", "旧 verdict 无效 · 自动处理暂停", "刚刚", "is-active"],
      failure: ["未确认 verifier 进程退出", "Issue 仍由 Runner 管理 · 可以重试", "刚刚", "is-active"],
    };
    const [heading, note, age, markClass] = timelineCopy[nextState];
    timelineTitle.textContent = heading;
    timelineNote.textContent = note;
    timerText.textContent = age;
    document.querySelector("#timeline-current .timeline-mark").className = `timeline-mark ${markClass}`;
  }

  function renderPr(nextState) {
    const copy = {
      running: "验证尚未完成。PR 保持 Draft，当前没有 verifier PASS。",
      pending: "接管请求处理中。PR 保持 Draft；尚未确认 verifier 已停止。",
      success: "人工接管完成。PR 保持 Draft；自动处理暂停，等待当前 HEAD 的新 verifier GREEN。",
      completed: "本轮 verifier 已结束。请刷新 Issue 状态后查看现有验证结论。",
      stale: "HEAD 已变化。旧 verifier 结果不适用；请检查当前 PR 提交。",
      failure: "进程退出尚未确认。PR 保持 Draft；Issue 仍由 Runner 管理。",
    };
    prNote.textContent = copy[nextState];
  }

  function beginTakeover() {
    dialog.close();
    setState("pending");
    if (scenarioMenu.dataset.manualPending === "true") return;
    state.timer = window.setTimeout(() => setState("success"), 2100);
  }

  takeoverButton.addEventListener("click", () => dialog.showModal());
  document.querySelector("#takeover-form").addEventListener("submit", (event) => {
    event.preventDefault();
    beginTakeover();
  });
  document.querySelector("#cancel-takeover").addEventListener("click", () => dialog.close());
  document.querySelector("#dialog-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
  document.querySelector("#open-pr-button").addEventListener("click", () => document.querySelector("#pr-context").scrollIntoView({ behavior: "smooth", block: "center" }));
  const refreshButton = document.querySelector(".refresh-button");
  refreshButton.addEventListener("click", () => {
    refreshButton.textContent = "✓ 已刷新";
    window.setTimeout(() => { refreshButton.textContent = "↻ 刷新"; }, 1100);
  });

  scenarioToggle.addEventListener("click", () => {
    const isOpen = !scenarioMenu.hidden;
    scenarioMenu.hidden = isOpen;
    scenarioToggle.setAttribute("aria-expanded", String(!isOpen));
  });
  scenarioMenu.querySelectorAll("[data-scenario]").forEach((button) => button.addEventListener("click", () => {
    scenarioMenu.dataset.manualPending = String(button.dataset.scenario === "pending");
    setState(button.dataset.scenario);
    if (!scenarioMenu.hidden) {
      scenarioMenu.hidden = true;
      scenarioToggle.setAttribute("aria-expanded", "false");
    }
  }));
  demoCompleteButton.addEventListener("click", () => {
    scenarioMenu.dataset.manualPending = "false";
    setState("success");
    scenarioMenu.hidden = true;
    scenarioToggle.setAttribute("aria-expanded", "false");
  });
  document.addEventListener("prototype:reset", () => {
    clearTimer();
    scenarioMenu.dataset.manualPending = "false";
    scenarioMenu.hidden = true;
    scenarioToggle.setAttribute("aria-expanded", "false");
    if (dialog.open) dialog.close();
    setState("running");
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !scenarioMenu.hidden) {
      scenarioMenu.hidden = true;
      scenarioToggle.setAttribute("aria-expanded", "false");
    }
  });
  setState("running");
})();
