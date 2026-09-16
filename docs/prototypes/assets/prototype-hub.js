(() => {
  const prototypeRegistry = [
    {
      id: 'roadmap-cicd-repair', title: 'CI/CD 监控与自动修复', project: 'keda', module: 'Roadmap',
      form: 'image-state', version: 'v1.0', updatedAt: '2026-09-16', availability: 'available', validationLevel: '概念原型',
      primaryFlow: '查看全局默认与单 PRD 覆盖的界面方案', description: 'Roadmap 页面中 CI/CD 等待、问题呈现与自动修复控制的高保真界面草图。',
      preview: './assets/roadmap-prd-cicd-01-waiting.png', entry: './roadmap-prd-cicd-auto-repair.html', source: './roadmap-prd-cicd-auto-repair/',
    },
    {
      id: 'roadmap-controls-evidence', title: '单 PRD 控制与验收证据', project: 'keda', module: 'Roadmap',
      form: 'image-state', version: 'v1.0', updatedAt: '2026-09-16', availability: 'available', validationLevel: '概念原型',
      primaryFlow: '查看单 PRD 控制和验收证据界面方案', description: '单 PRD 控制、证据浏览和 Autopilot 相关页面的高保真界面草图。',
      preview: './assets/roadmap-prd-controls-evidence-autopilot.png', entry: './assets/roadmap-prd-controls-evidence-autopilot.png', source: './roadmap-prd-controls-evidence-autopilot/',
    },
    {
      id: 'worktree-dependency-demo', title: 'Worktree 前端依赖策略', project: 'keda', module: 'Developer Experience',
      form: 'code-native', version: 'v1.0', updatedAt: '2026-07-07', availability: 'available', validationLevel: '交互原型',
      primaryFlow: '比较独立安装与复用主工程依赖的反馈', description: '用于比较两种 Worktree 前端依赖策略的可交互演示。',
      preview: '', entry: './worktree-frontend-demo.html', source: './',
    },
  ];

  const formLabels = { 'image-state': '图片原型', 'code-native': '交互原型' };
  const availabilityLabels = { available: '可用', archived: '已归档' };
  const state = { query: '', project: 'all', module: 'all', form: 'all', availability: 'all', selectedId: prototypeRegistry[0].id };
  const elements = {
    list: document.querySelector('#prototype-list'), detail: document.querySelector('#prototype-detail'), empty: document.querySelector('#empty-state'),
    visibleCount: document.querySelector('#visible-count'), totalCount: document.querySelector('[data-total-count]'), search: document.querySelector('#prototype-search'),
    project: document.querySelector('#project-filter'), module: document.querySelector('#module-filter'), form: document.querySelector('#form-filter'),
    availability: document.querySelector('#availability-filter'), clear: document.querySelector('#clear-filters'), navItems: [...document.querySelectorAll('[data-nav-filter]')],
  };

  const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' })[character]);
  const uniqueValues = (key) => [...new Set(prototypeRegistry.map((prototype) => prototype[key]))].sort();
  const appendOptions = (select, values) => values.forEach((value) => select.add(new Option(value, value)));
  const matchesFilters = (prototype) => {
    const searchableText = `${prototype.title} ${prototype.description} ${prototype.project} ${prototype.module}`.toLowerCase();
    return (!state.query || searchableText.includes(state.query))
      && (state.project === 'all' || prototype.project === state.project)
      && (state.module === 'all' || prototype.module === state.module)
      && (state.form === 'all' || prototype.form === state.form)
      && (state.availability === 'all' || prototype.availability === state.availability);
  };
  const previewMarkup = (prototype) => prototype.preview
    ? `<img class="catalog-thumb" src="${escapeHtml(prototype.preview)}" alt="${escapeHtml(prototype.title)}预览" />`
    : '<span class="catalog-thumb catalog-thumb-placeholder" aria-hidden="true">HTML</span>';

  const renderDetail = () => {
    const prototype = prototypeRegistry.find((candidate) => candidate.id === state.selectedId);
    if (!prototype) {
      elements.detail.innerHTML = '<div class="empty-state"><strong>请选择一个原型</strong><span>详情将在这里显示。</span></div>';
      return;
    }
    const largePreview = prototype.preview
      ? `<img src="${escapeHtml(prototype.preview)}" alt="${escapeHtml(prototype.title)}大图预览" />`
      : '<span class="detail-placeholder">Interactive HTML</span>';
    elements.detail.innerHTML = `
      <a class="detail-preview" href="${escapeHtml(prototype.entry)}">${largePreview}</a>
      <div class="detail-heading"><h2>${escapeHtml(prototype.title)}</h2><p>${escapeHtml(prototype.description)}</p></div>
      <dl class="detail-meta">
        <div><dt>所属项目</dt><dd>${escapeHtml(prototype.project)}</dd></div><div><dt>所属模块</dt><dd>${escapeHtml(prototype.module)}</dd></div>
        <div><dt>原型形式</dt><dd>${formLabels[prototype.form]}</dd></div><div><dt>验证层级</dt><dd>${escapeHtml(prototype.validationLevel)}</dd></div>
        <div><dt>主要流程</dt><dd>${escapeHtml(prototype.primaryFlow)}</dd></div><div><dt>版本</dt><dd>${escapeHtml(prototype.version)}</dd></div>
        <div><dt>更新时间</dt><dd>${escapeHtml(prototype.updatedAt)}</dd></div><div><dt>可用状态</dt><dd>${availabilityLabels[prototype.availability]}</dd></div>
      </dl>
      <div class="detail-actions"><a class="detail-action is-secondary" href="${escapeHtml(prototype.source)}">查看说明</a><a class="detail-action" href="${escapeHtml(prototype.entry)}">打开原型</a></div>`;
  };

  const renderList = () => {
    const visiblePrototypes = prototypeRegistry.filter(matchesFilters);
    if (!visiblePrototypes.some((prototype) => prototype.id === state.selectedId)) state.selectedId = visiblePrototypes[0]?.id ?? '';
    elements.visibleCount.textContent = String(visiblePrototypes.length);
    elements.empty.hidden = visiblePrototypes.length > 0;
    elements.list.innerHTML = visiblePrototypes.map((prototype) => `
      <tr class="catalog-row${prototype.id === state.selectedId ? ' is-selected' : ''}" data-prototype-id="${prototype.id}">
        <td><a class="catalog-preview-link" href="${escapeHtml(prototype.entry)}" aria-label="打开${escapeHtml(prototype.title)}">${previewMarkup(prototype)}</a></td>
        <td><a class="catalog-title-link" href="${escapeHtml(prototype.entry)}">${escapeHtml(prototype.title)}</a></td>
        <td>${escapeHtml(prototype.project)}</td><td>${escapeHtml(prototype.module)}</td><td><span class="type-pill">${formLabels[prototype.form]}</span></td>
        <td>${escapeHtml(prototype.version)}</td><td>${escapeHtml(prototype.updatedAt)}</td>
        <td><span class="availability-pill${prototype.availability === 'archived' ? ' is-archived' : ''}">● ${availabilityLabels[prototype.availability]}</span></td>
      </tr>`).join('');
    renderDetail();
  };

  const resetFilters = () => {
    Object.assign(state, { query: '', project: 'all', module: 'all', form: 'all', availability: 'all' });
    elements.search.value = '';
    [elements.project, elements.module, elements.form, elements.availability].forEach((select) => { select.value = 'all'; });
    elements.navItems.forEach((button) => button.classList.toggle('is-active', button.dataset.navFilter === 'all'));
    renderList();
  };

  appendOptions(elements.project, uniqueValues('project'));
  appendOptions(elements.module, uniqueValues('module'));
  elements.totalCount.textContent = String(prototypeRegistry.length);
  elements.search.addEventListener('input', (event) => { state.query = event.target.value.trim().toLowerCase(); renderList(); });
  [['project', elements.project], ['module', elements.module], ['form', elements.form], ['availability', elements.availability]].forEach(([key, select]) => {
    select.addEventListener('change', (event) => { state[key] = event.target.value; renderList(); });
  });
  elements.clear.addEventListener('click', resetFilters);
  elements.list.addEventListener('click', (event) => {
    if (event.target.closest('a, button')) return;
    const selectedRow = event.target.closest('[data-prototype-id]');
    if (!selectedRow) return;
    state.selectedId = selectedRow.dataset.prototypeId;
    renderList();
  });
  elements.navItems.forEach((button) => button.addEventListener('click', () => {
    const filter = button.dataset.navFilter;
    state.form = ['image-state', 'code-native'].includes(filter) ? filter : 'all';
    state.availability = filter === 'available' ? 'available' : 'all';
    elements.form.value = state.form;
    elements.availability.value = state.availability;
    elements.navItems.forEach((candidate) => candidate.classList.toggle('is-active', candidate === button));
    renderList();
  }));
  renderList();
})();
