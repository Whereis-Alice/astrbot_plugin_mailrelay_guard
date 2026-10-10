// 多信纸主题引擎。应用到 <html data-mail-theme>，持久化到 localStorage，
// 并迁移旧版 light/dark 两档外观。无 import 期 DOM 读取。
const STORAGE_KEY = 'my-mail-theme';

export const THEMES = [
  { id:'sage',     name:'经典信纸', scheme:'light', swatch:['#2b6a56','#f3f5f0','#ffffff'] },
  { id:'ivory',    name:'奶油信笺', scheme:'light', swatch:['#9a6a2f','#f6f1e7','#fffdf7'] },
  { id:'harbor',   name:'晨雾海港', scheme:'light', swatch:['#2d6b85','#eef2f5','#ffffff'] },
  { id:'rose',     name:'玫瑰邮戳', scheme:'light', swatch:['#a24a63','#f7f0f1','#fffbfb'] },
  { id:'ink',      name:'墨色信笺', scheme:'dark',  swatch:['#93c7a9','#141b18','#1c2621'] },
  { id:'nocturne', name:'午夜蓝',   scheme:'dark',  swatch:['#7fb4e6','#121824','#1a2232'] },
  { id:'espresso', name:'复古邮筒', scheme:'dark',  swatch:['#d79a4e','#1a1510','#241d16'] },
];

const BY_ID = new Map(THEMES.map(t => [t.id, t]));
const LIGHT_DEFAULT = 'sage';
const DARK_DEFAULT = 'ink';
const LEGACY = { light: LIGHT_DEFAULT, dark: DARK_DEFAULT };

export function schemeOf(id) { return BY_ID.get(id)?.scheme || 'light'; }
export function isKnown(id) { return BY_ID.has(id); }

function prefersDark() {
  try { return matchMedia('(prefers-color-scheme: dark)').matches; } catch { return false; }
}

export function readStored() {
  let raw;
  try { raw = localStorage.getItem(STORAGE_KEY); } catch { return null; }
  if (!raw) return null;
  if (LEGACY[raw]) return LEGACY[raw];
  return BY_ID.has(raw) ? raw : null;
}

export function resolveInitial() {
  return readStored() || (prefersDark() ? DARK_DEFAULT : LIGHT_DEFAULT);
}

export function apply(id) {
  const theme = BY_ID.has(id) ? id : resolveInitial();
  document.documentElement.setAttribute('data-mail-theme', theme);
  try { localStorage.setItem(STORAGE_KEY, theme); } catch { /* restricted iframe storage */ }
  return theme;
}

// Jump to the opposite scheme's default, keeping the toggle button meaningful.
export function toggleScheme(current) {
  return schemeOf(current) === 'dark' ? LIGHT_DEFAULT : DARK_DEFAULT;
}
