(() => {
  const wizard = document.querySelector('#wizard-dialog');
  const confirmDialog = document.querySelector('#confirm-dialog');
  const toast = document.querySelector('#toast');
  const screens = [...document.querySelectorAll('[data-screen]')];
  let step = 1;
  let confirmKind = '';
  let toastTimer;

  function showToast(message) {
    toast.textContent = message;
    toast.classList.add('is-visible');
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove('is-visible'), 2500);
  }

  function setScreen(name, machine = 'office-macbook') {
    screens.forEach((screen) => { screen.hidden = screen.dataset.screen !== name; });
    const title = document.querySelector('#page-title');
    const description = document.querySelector('#page-description');
    if (name === 'machines') {
      title.textContent = '机器管理';
      description.textContent = '把自己的电脑加入同一安全网络。Agent 负责安装检测、连接检查和后续配置；你只在目标电脑完成登录与系统授权。';
    } else if (name === 'files') {
      title.textContent = '远程文档';
      description.textContent = '查看另一台电脑上已授权的文档和图片；远程文件保持只读。';
      document.querySelector('#files-machine-name').textContent = machine;
    } else if (name === 'webview') {
      title.textContent = '项目预览';
      description.textContent = '只查看目标电脑上由 KedaCode 管理的项目预览；通过私有 tailnet 分享。';
      document.querySelector('#web-machine-name').textContent = machine;
    } else {
      title.textContent = 'Agent Runner';
      description.textContent = '查看并管理目标电脑上的 Keda Agent Runner。';
      document.querySelector('#runner-machine-name').textContent = machine;
    }
  }

  function setStep(next) {
    step = Math.max(1, Math.min(3, next));
    document.querySelectorAll('[data-step]').forEach((section) => {
      section.hidden = Number(section.dataset.step) !== step;
    });
    document.querySelectorAll('[data-progress]').forEach((item) => {
      const value = Number(item.dataset.progress);
      item.classList.toggle('is-current', value === step);
      item.classList.toggle('is-done', value < step);
    });
  }

  document.addEventListener('click', (event) => {
    const actionButton = event.target.closest('[data-action]');
    if (actionButton) {
      const action = actionButton.dataset.action;
      if (action === 'open-wizard') { setStep(1); wizard.showModal(); }
      if (action === 'close-wizard') wizard.close();
      if (action === 'wizard-next') setStep(step + 1);
      if (action === 'wizard-back') setStep(step - 1);
      if (action === 'wizard-finish') {
        wizard.close();
        showToast('连接设置已保存。文件范围为只读，可随时撤销。');
      }
      if (action === 'files') setScreen('files', actionButton.dataset.machine);
      if (action === 'web') setScreen('webview', actionButton.dataset.machine);
      if (action === 'runner') setScreen('runner', actionButton.dataset.machine);
      if (action === 'back-machines') setScreen('machines');
      if (action === 'toast') showToast('演示中：此处将打开目标电脑的授权文件夹列表。');
      if (action === 'confirm-runner') {
        confirmKind = actionButton.dataset.kind;
        document.querySelector('#confirm-title').textContent = confirmKind === 'stop' ? '停止 office-macbook 上的 Runner？' : '重启 office-macbook 上的 Runner？';
        document.querySelector('#confirm-copy').textContent = '当前仓库为 zata / keda。停止会中断正在运行的 Agent；不会删除文件或关闭电脑。';
        confirmDialog.showModal();
      }
      if (action === 'cancel-confirm') confirmDialog.close();
      if (action === 'apply-confirm') {
        confirmDialog.close();
        showToast(confirmKind === 'stop' ? '已发送停止请求（演示）。' : '已发送重启请求（演示）。');
      }
    }

    const fileButton = event.target.closest('[data-file]');
    if (fileButton) {
      document.querySelectorAll('[data-file]').forEach((item) => item.classList.toggle('is-selected', item === fileButton));
      const name = fileButton.dataset.file;
      document.querySelector('#preview-title').textContent = name;
      const preview = document.querySelector('.fake-document');
      preview.innerHTML = name.endsWith('.png')
        ? '<div class="image-placeholder"><span>▧</span><strong>产品截图.png</strong><small>图片预览 · 目标电脑上的原文件没有被复制或修改</small></div>'
        : name.endsWith('.pdf')
          ? '<div class="pdf-placeholder"><span>PDF</span><strong>会议纪要</strong><p>周一项目同步 · 决策摘要</p><p>下一步：完成设备连接和只读文件验证。</p></div>'
          : '<div class="doc-kicker">KEDA PROJECT</div><h4>项目计划</h4><p>本周目标</p><ul><li>完成机器连接向导</li><li>验证文档只读浏览</li><li>整理下次评审问题</li></ul><div class="doc-rule"></div><p class="doc-muted">文件内容在目标电脑上保持原样。Keda 不会修改或删除远程文件。</p>';
    }

    const folderButton = event.target.closest('[data-folder]');
    if (folderButton) {
      document.querySelectorAll('[data-folder]').forEach((item) => item.classList.toggle('is-selected', item === folderButton));
      showToast(`正在查看 ${folderButton.dataset.folder}（演示数据）。`);
    }
  });

  document.querySelector('#prototype-reset')?.addEventListener('click', () => {});
  document.addEventListener('prototype:reset', () => {
    wizard.close();
    confirmDialog.close();
    setScreen('machines');
    setStep(1);
  });
})();
