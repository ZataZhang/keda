(async () => {
  async function readMarkup(relativePath) {
    const response = await fetch(relativePath);
    if (!response.ok) throw new Error(`原型资源读取失败 ${relativePath}`);
    return { markup: await response.text(), url: response.url };
  }

  try {
    const [shellResource, frameResource, graphsResource, sheetResource] = await Promise.all([
      readMarkup('../assets/console-page-shell.html'),
      readMarkup('./assets/lifecycle-settings-frame.html'),
      readMarkup('./assets/actual-workflow-graphs.html'),
      readMarkup('./assets/actual-sheet-header.html'),
    ]);
    const shellDocument = new DOMParser().parseFromString(shellResource.markup, 'text/html');
    document.documentElement.className = shellDocument.documentElement.className;
    const stylesheetLoads = [...shellDocument.querySelectorAll('link[rel="stylesheet"]')].map((sourceLink) => {
      const link = document.createElement('link');
      link.rel = 'stylesheet';
      link.href = new URL(sourceLink.getAttribute('href'), shellResource.url).href;
      const loaded = new Promise((resolve, reject) => {
        link.addEventListener('load', resolve, { once: true });
        link.addEventListener('error', () => reject(new Error('原页面样式加载失败')), { once: true });
      });
      document.head.append(link);
      return loaded;
    });
    await Promise.all(stylesheetLoads);

    const shell = shellDocument.body.firstElementChild;
    for (const image of shell.querySelectorAll('img')) {
      image.src = new URL(image.getAttribute('src'), shellResource.url).href;
    }
    shell.querySelector('[data-prototype-page-slot]').innerHTML = frameResource.markup;
    for (const button of shell.querySelectorAll('button')) {
      button.disabled = true;
      button.title = '实际页面的背景控件，此原型演示生命周期工作流';
    }
    const destinations = {
      '/app/dashboard': 'prd-lifecycle-observability',
      '/app/repositories': 'configurable-workflows',
      '/app/stats': 'issue-cost-stats',
    };
    for (const anchor of shell.querySelectorAll('a[href]')) {
      const route = new URL(anchor.getAttribute('href'), location.href).pathname.replace(/\/$/, '');
      const target = window.KEDA_PROTOTYPE_REGISTRY.find((entry) => entry.id === destinations[route] && entry.availability === 'available');
      if (target) {
        anchor.href = new URL(target.entry, new URL('../hub.html', location.href)).href;
        if (target.id !== 'configurable-workflows') {
          anchor.target = '_blank';
          anchor.rel = 'noopener';
          anchor.title = '打开关联原型';
          anchor.append(' ↗');
        }
      } else {
        anchor.removeAttribute('href');
        anchor.setAttribute('aria-disabled', 'true');
        anchor.title = '实际页面的导航背景';
      }
    }

    const content = document.getElementById('workflow-content-template').content.cloneNode(true);
    shell.querySelector('[data-workflow-mount]').append(content);
    document.getElementById('loading-note').replaceWith(shell);
    const sheet = document.getElementById('node-dialog');
    sheet.querySelector('[data-sheet-heading]').innerHTML = sheetResource.markup;
    sheet.querySelector('[data-slot="sheet-title"]').id = 'selected-title';
    sheet.querySelector('[data-slot="sheet-description"]').textContent = '查看 CLI 定义、实际执行配置与工具恢复方式。';
    window.KEDA_ACTUAL_WORKFLOW_GRAPHS = graphsResource.markup;
    const script = document.createElement('script');
    script.src = './prototype.js';
    script.onload = async () => {
      await document.fonts.ready;
      document.body.dataset.prototypeReady = 'true';
    };
    document.body.append(script);
  } catch (error) {
    document.getElementById('loading-note').textContent = `${error.message}。请通过原型预览服务器打开。`;
  }
})();
