// 入口：应用主题 → 渲染外壳 → 连接后端（或演示） → 启动哈希路由。
import * as api from './api.js';
import { createStore } from './store.js';
import { resolveInitial, apply as applyTheme } from './theme.js';
import { renderShell, setConnection, setConnectionFailed } from './shell.js';
import { createRouter } from './router.js';
import { overviewView } from './overview.js';
import { mailboxView } from './mailbox.js';
import { monitorView } from './monitor.js';
import { settingsView } from './settings.js';

applyTheme(resolveInitial());

const store = createStore({});
const router = createRouter({
  views: {
    overview: overviewView,
    mailbox: mailboxView,
    monitor: monitorView,
    settings: settingsView,
  },
  fallback: 'overview',
  api,
  store,
});

// 刷新按钮：有未保存修改时由 shell 拦截并提示；否则重渲染当前页。
renderShell({ onRefresh: () => router.render(), canLeave: () => !router.getDirty() });

try {
  await api.ready();
  setConnection(api.demo);
} catch {
  setConnectionFailed();
}

await router.start();
