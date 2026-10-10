// 轻量 DOM 工具：选择器、事件绑定、委托、动作分发。全部返回解绑函数，便于清理。
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

export function on(el, type, handler, opts) {
  if (!el) return () => {};
  el.addEventListener(type, handler, opts);
  return () => el.removeEventListener(type, handler, opts);
}

export function delegate(root, type, selector, handler, opts) {
  const fn = event => {
    const match = event.target.closest(selector);
    if (match && root.contains(match)) handler(event, match);
  };
  root.addEventListener(type, fn, opts);
  return () => root.removeEventListener(type, fn, opts);
}

// Click dispatcher for [data-action] buttons. Disables the button during async
// work and routes failures to onError so a single toast surfaces the message.
export function bindActions(root, map, onError) {
  return delegate(root, 'click', '[data-action]', async (event, button) => {
    const action = button.dataset.action, fn = map[action];
    if (!fn || button.disabled) return;
    button.disabled = true;
    try { await fn(event, button); }
    catch (error) { if (onError) onError(error); else console.error(error); }
    finally { button.disabled = false; }
  });
}

// Collects several unbind callbacks into one cleanup function.
export function group(...cleanups) {
  return () => cleanups.forEach(fn => { try { fn && fn(); } catch { /* idempotent cleanup */ } });
}
