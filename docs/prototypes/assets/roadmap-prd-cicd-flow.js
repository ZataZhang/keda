(() => {
  const policyHotspot = { label: '强制关闭自动修复', left: 78.4, top: 35.5, width: 6.4, height: 4.2, action: 'manual' };
  const flowStates = [
    {
      id: 'waiting', image: './assets/roadmap-prd-cicd-01-waiting.png', alt: '等待 CI/CD 检查状态',
      description: 'PRD 完成后，系统自动等待 GitHub Checks。', hint: '点击图中的“查看 GitHub 检查”模拟检查返回失败。',
      hotspots: [policyHotspot, { label: '查看 GitHub 检查', left: 87.2, top: 65.8, width: 10, height: 4.2, action: 'repairing' }],
    },
    {
      id: 'repairing', image: './assets/roadmap-prd-cicd-02-repairing.png', alt: '第 1 轮失败并自动修复状态',
      description: '检查失败后，修复 Agent 复用当前 PR 与分支继续处理。', hint: '点击图中的“修复中…”区域模拟修复完成；也可点击“强制关闭”。',
      hotspots: [policyHotspot, { label: '修复完成并推送', left: 87.4, top: 83.3, width: 9.2, height: 5, action: 'rechecking' }],
    },
    {
      id: 'rechecking', image: './assets/roadmap-prd-cicd-03-rechecking.png', alt: '第 2 轮重新检查状态',
      description: '修复提交已推送，系统持续轮询第 2 轮 GitHub Checks。', hint: '点击图中的“第 2 轮 · 检查中”模拟检查通过。',
      hotspots: [policyHotspot, { label: '第 2 轮检查通过', left: 87.2, top: 54.8, width: 10.8, height: 10, action: 'passed' }],
    },
    {
      id: 'passed', image: './assets/roadmap-prd-cicd-04-passed.png', alt: '第 2 轮 CI/CD 检查通过状态',
      description: '所有检查通过，PRD 完成并允许继续下游任务。', hint: '点击图中的“继续下游 PRD”模拟解锁下游任务。',
      hotspots: [{ label: '继续下游 PRD', left: 86.8, top: 78.5, width: 10.2, height: 5.5, action: 'downstream' }],
    },
  ];
  const manualState = {
    id: 'manual', image: './assets/roadmap-prd-cicd-auto-repair.png', alt: '自动修复关闭且问题保留状态',
    description: '自动修复关闭后，失败问题保留在详情中等待人工处理。', hint: '点击图中的“立即修复此问题”返回自动修复流程，或点击“跟随全局”恢复策略。',
    hotspots: [
      { label: '恢复跟随全局', left: 67.6, top: 35.5, width: 6.5, height: 4.2, action: 'repairing' },
      { label: '立即修复此问题', left: 87.2, top: 84.5, width: 10, height: 5.4, action: 'repairing' },
    ],
  };
  let selectedStateId = 'waiting';
  const stateImage = document.querySelector('#state-image');
  const stateDescription = document.querySelector('#state-description');
  const flowHint = document.querySelector('#flow-hint');
  const stage = document.querySelector('.flow-stage');
  const hotspotLayer = document.querySelector('#hotspot-layer');
  const previousButton = document.querySelector('#previous-state');
  const stepButtons = [...document.querySelectorAll('[data-state]')];

  const renderHotspots = (hotspots) => {
    hotspotLayer.innerHTML = hotspots.map((hotspot) => `<button class="image-hotspot" type="button" aria-label="${hotspot.label}" data-action="${hotspot.action}" style="left:${hotspot.left}%;top:${hotspot.top}%;width:${hotspot.width}%;height:${hotspot.height}%"></button>`).join('');
  };
  const renderState = () => {
    const mainIndex = flowStates.findIndex((flowState) => flowState.id === selectedStateId);
    const selectedState = mainIndex >= 0 ? flowStates[mainIndex] : manualState;
    stage.classList.add('is-changing');
    stateImage.src = selectedState.image;
    stateImage.alt = selectedState.alt;
    stateDescription.textContent = selectedState.description;
    flowHint.textContent = selectedState.hint;
    previousButton.disabled = selectedStateId === 'waiting';
    stepButtons.forEach((button) => button.classList.toggle('is-active', button.dataset.state === selectedStateId));
    renderHotspots(selectedState.hotspots);
    window.setTimeout(() => stage.classList.remove('is-changing'), 140);
  };
  const selectState = (stateId) => { selectedStateId = stateId; renderState(); };
  const runHotspotAction = (action) => {
    if (action === 'downstream') {
      flowHint.textContent = '已模拟解锁下游 PRD；真实产品中将继续 Autopilot 调度。';
      return;
    }
    selectState(action);
  };

  stepButtons.forEach((button) => button.addEventListener('click', () => selectState(button.dataset.state)));
  hotspotLayer.addEventListener('click', (event) => {
    const hotspot = event.target.closest('[data-action]');
    if (hotspot) runHotspotAction(hotspot.dataset.action);
  });
  previousButton.addEventListener('click', () => {
    if (selectedStateId === 'manual') selectState('repairing');
    else selectState(flowStates[Math.max(0, flowStates.findIndex((flowState) => flowState.id === selectedStateId) - 1)].id);
  });
  renderState();
})();
