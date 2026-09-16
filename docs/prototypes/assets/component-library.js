(() => {
  const searchInput = document.querySelector('#component-search');
  const entries = [...document.querySelectorAll('.component-entry')];
  const categoryButtons = [...document.querySelectorAll('.library-sidebar [data-category]')];
  const emptyState = document.querySelector('#library-empty');
  const tabButtons = [...document.querySelectorAll('[data-tab]')];
  const tabPanels = [...document.querySelectorAll('[data-panel]')];
  const dialogBackdrop = document.querySelector('#dialog-backdrop');
  const toast = document.querySelector('#toast');
  let toastTimer;
  let selectedCategory = 'all';

  const showToast = (message) => {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.hidden = false;
    toastTimer = window.setTimeout(() => { toast.hidden = true; }, 2200);
  };
  const filterEntries = () => {
    const query = searchInput.value.trim().toLowerCase();
    let visibleCount = 0;
    entries.forEach((entry) => {
      const matchesQuery = !query || `${entry.dataset.search} ${entry.textContent}`.toLowerCase().includes(query);
      const matchesCategory = selectedCategory === 'all' || entry.dataset.category === selectedCategory;
      const isVisible = matchesQuery && matchesCategory;
      entry.hidden = !isVisible;
      if (isVisible) visibleCount += 1;
    });
    emptyState.hidden = visibleCount > 0;
  };
  searchInput.addEventListener('input', filterEntries);
  categoryButtons.forEach((button) => button.addEventListener('click', () => {
    selectedCategory = button.dataset.category;
    categoryButtons.forEach((candidate) => candidate.classList.toggle('is-active', candidate === button));
    filterEntries();
  }));
  tabButtons.forEach((button) => button.addEventListener('click', () => {
    tabButtons.forEach((candidate) => candidate.classList.toggle('is-active', candidate === button));
    tabPanels.forEach((panel) => { panel.hidden = panel.dataset.panel !== button.dataset.tab; });
  }));
  document.querySelector('.ui-table tbody').addEventListener('click', (event) => {
    const selectedRow = event.target.closest('tr');
    if (!selectedRow) return;
    selectedRow.parentElement.querySelectorAll('tr').forEach((row) => row.classList.toggle('is-selected', row === selectedRow));
  });
  document.querySelector('#open-dialog').addEventListener('click', () => { dialogBackdrop.hidden = false; document.querySelector('#close-dialog').focus(); });
  document.querySelector('#close-dialog').addEventListener('click', () => { dialogBackdrop.hidden = true; });
  dialogBackdrop.addEventListener('click', (event) => { if (event.target === dialogBackdrop) dialogBackdrop.hidden = true; });
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape') dialogBackdrop.hidden = true; });
  document.querySelectorAll('[data-toast]').forEach((button) => button.addEventListener('click', () => { dialogBackdrop.hidden = true; showToast(button.dataset.toast); }));
})();
