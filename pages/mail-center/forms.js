// 表单工具：收集字段值、与快照对比只提交变化项、保存并给出重载提示。
// monitor.js 与 settings.js 共用，保持与后端 webui/settings 契约一致。

export const saveBar = () => '<div class="save-bar"><span id="save-status">修改设置后，点击保存即可生效。</span><button class="button" type="submit">保存设置</button></div>';

// 读取表单内所有具名控件：复选框→布尔，数字→Number，data-type=list 的文本域→按行/逗号/分号拆分去空，其余→字符串。
export function formValues(form) {
  const values = {};
  form.querySelectorAll('[name]').forEach(el => {
    values[el.name] = el.type === 'checkbox'
      ? el.checked
      : el.type === 'number'
        ? Number(el.value)
        : el.dataset.type === 'list'
          ? el.value.split(/[\n,;]+/).map(item => item.trim()).filter(Boolean)
          : el.value;
  });
  return values;
}

// 仅提交与 snapshot 不同的字段。成功返回新的 settings 快照；无变化或失败返回 null。
export async function saveSettings(ctx, form, snapshot) {
  if (!form.reportValidity()) return null;
  const values = formValues(form);
  const changed = Object.fromEntries(
    Object.entries(values).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(snapshot[key]))
  );
  if (!Object.keys(changed).length) { ctx.setDirty(false); ctx.toast('设置没有变化。'); return null; }

  const submit = form.querySelector('[type=submit]');
  if (submit) submit.disabled = true;
  try {
    const result = await ctx.api.post('webui/settings', { settings: changed });
    ctx.setDirty(false);
    ctx.toast(result.restart_required ? '设置已保存。HTML 工具开关需要重载插件。' : '设置已保存，已立即生效。');
    return result.settings.settings;
  } catch (error) {
    ctx.toast(error.message || '保存失败');
    return null;
  } finally {
    if (submit) submit.disabled = false;
  }
}
