// Hash router with an epoch guard against async races and a dirty guard that
// protects unsaved settings. Each view exports async enter(ctx) -> cleanup().
import { $ } from './dom.js';
import { loading } from './ui.js';
import { setRoute, confirmDialog, toast } from './shell.js';

export function createRouter({ views, fallback, api, store }) {
  let epoch = 0;
  let currentRoute = null;
  let cleanup = null;
  let dirty = false;

  const container = () => $('#view');

  function parseHash() {
    const id = (location.hash.slice(1).split('?')[0]) || fallback;
    return views[id] ? id : fallback;
  }

  function setDirty(value) {
    dirty = !!value;
    const status = $('#save-status');
    if (status && dirty) status.textContent = '有未保存的修改';
  }

  async function guardLeave(nextRoute) {
    if (!dirty || nextRoute === currentRoute) return true;
    return confirmDialog('离开当前设置？', '当前有尚未保存的修改。离开后这些修改不会保留。');
  }

  function makeCtx(myEpoch, route) {
    return {
      route, api, store, toast,
      container: container(),
      confirm: confirmDialog,
      isCurrent: () => myEpoch === epoch,
      navigate: (r) => { location.hash = '#' + r; },
      rerender: () => render(),
      setDirty,
      getDirty: () => dirty,
    };
  }

  async function render() {
    const route = parseHash();
    if (!(await guardLeave(route))) { history.replaceState(null, '', '#' + currentRoute); return; }

    const myEpoch = ++epoch;
    if (cleanup) { try { cleanup(); } catch { /* idempotent */ } cleanup = null; }
    currentRoute = route;
    dirty = false;
    setRoute(route);

    const el = container();
    el.innerHTML = loading('正在打开…');
    el.setAttribute('aria-busy', 'true');

    try {
      const ctx = makeCtx(myEpoch, route);
      const result = await views[route](ctx);
      if (myEpoch !== epoch) { if (typeof result === 'function') { try { result(); } catch { /* stale */ } } return; }
      cleanup = typeof result === 'function' ? result : null;
    } catch (error) {
      if (myEpoch === epoch) {
        el.innerHTML = `<div class="empty error-empty"><h2>暂时无法加载</h2><p>${(error && error.message) || '请求失败，请稍后重试。'}</p><div class="button-row"><button type="button" class="button" data-action="route-retry">重新加载</button></div></div>`;
        el.querySelector('[data-action="route-retry"]')?.addEventListener('click', async () => { try { await api.ready(); } catch { /* surfaced below */ } render(); });
      }
    } finally {
      if (myEpoch === epoch) el.setAttribute('aria-busy', 'false');
    }
  }

  function start() {
    window.addEventListener('hashchange', render);
    window.addEventListener('beforeunload', e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } });
    return render();
  }

  return { start, render, setDirty, getDirty: () => dirty, current: () => currentRoute };
}
