"""Scheduling settings page; credentials follow the existing workspace pages."""
SETTING_PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>运行设置 · 1688 工作台</title>
<style>
* { box-sizing: border-box; }body { margin: 0; background: #f7f9fc; color: #334155; font: 14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif; }
.sidebar { position: fixed; inset: 0 auto 0 0; width: 224px; padding: 32px 22px; background: white; border-right: 1px solid #e5eaf2; }.brand { display: block; margin-bottom: 36px; color: #2563eb; font-size: 24px; font-weight: 700; text-decoration: none; }.brand small { display: block; color: #94a3b8; font-size: 12px; }nav { display: grid; gap: 8px; }nav a { padding: 12px 16px; border-radius: 12px; color: #64748b; text-decoration: none; font-weight: 600; }nav a:hover,nav a[aria-current] { color: #2563eb; background: #edf3ff; }
main { margin-left: 224px; padding: 40px; max-width: 1200px; }h1 { margin: 0 0 24px; font-size: 28px; }h2 { margin: 0 0 18px; font-size: 17px; }.panel { margin-bottom: 20px; padding: 24px; background: white; border: 1px solid #e5eaf2; border-radius: 16px; }.grid { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 20px; }label { display: grid; gap: 8px; }input { width: 100%; border: 1px solid #dbe2ec; border-radius: 8px; padding: 10px 12px; font: inherit; }input:focus { outline: 2px solid #bfdbfe; border-color: #60a5fa; }button { cursor: pointer; border: 0; border-radius: 8px; padding: 10px 18px; background: #2563eb; color: white; font: inherit; }button:disabled { opacity: .5; cursor: default; }summary { cursor: pointer; }.auth-row { display: flex; gap: 12px; margin-top: 16px; }.auth-row input { flex: 1; min-width: 0; }fieldset { margin: 0; padding: 0; border: 0; }#message { min-height: 24px; margin: 12px 0; white-space: pre-wrap; }#message.error { color: #dc2626; }
@media(max-width:760px) { .sidebar { position: static; width: auto; padding: 16px; }.brand { margin-bottom: 12px; }nav { display: flex; }nav a { flex: 1; text-align: center; padding: 10px; }main { margin: 0; padding: 24px 14px; }.grid { grid-template-columns: 1fr; } }
</style></head>
<body><aside class="sidebar"><a class="brand" href="/queue">1688 <small>采集管理工作台</small></a><nav aria-label="主导航"><a href="/queue">任务队列</a><a href="/console">告警设置</a><a href="/setting" aria-current="page">运行设置</a></nav></aside>
<main><h1>运行设置</h1>
<details class="panel" id="auth"><summary>API Key</summary><div class="auth-row"><input id="apiKey" type="password" placeholder="Search API Key" aria-label="Search API Key" autocomplete="off"><button id="load" type="button">加载设置</button></div></details>
<form id="settingsForm"><fieldset id="fields" disabled>
<section class="panel"><h2>并发数量</h2><div class="grid">
<label>全局并发<input name="global_worker_count" type="number" min="1" max="32" step="1" required></label>
<label>统一单账号并发<input name="account_max_concurrency" type="number" min="1" max="32" step="1" required></label>
</div></section>
<section class="panel"><h2>全局请求间隔（秒）</h2><div class="grid">
<label>最小间隔<input name="request_interval_min_seconds" type="number" min="0" max="3600" step="any" required></label>
<label>最大间隔<input name="request_interval_max_seconds" type="number" min="0" max="3600" step="any" required></label>
</div></section>
<section class="panel"><h2>单账号请求间隔（秒）</h2><div class="grid">
<label>最小间隔<input name="account_request_interval_min_seconds" type="number" min="0" max="3600" step="any" required></label>
<label>最大间隔<input name="account_request_interval_max_seconds" type="number" min="0" max="3600" step="any" required></label>
</div></section><button type="submit">保存设置</button></fieldset></form><p id="message" role="status"></p>
</main><script>
const $ = id => document.getElementById(id);
const inputs = [...document.querySelectorAll('#fields input')];
$('apiKey').value = sessionStorage.getItem('queue-api-key') || sessionStorage.getItem('console-api-key') || '';
function message(text, error=false) { $('message').textContent = text; $('message').className = error ? 'error' : ''; }
async function request(method, values) {
  const key = $('apiKey').value.trim();
  const response = await fetch('/api/v1/settings', { method, headers: {'X-API-Key': key, 'Content-Type': 'application/json'}, ...(values ? {body: JSON.stringify(values)} : {}) });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : Array.isArray(data.detail) ? data.detail.map(item => item.msg).join('\n') : data.detail?.message || '请求失败');
  sessionStorage.setItem('queue-api-key', key);
  return data;
}
async function load() {
  $('load').disabled = true; $('fields').disabled = true; message('');
  try { const data = await request('GET'); inputs.forEach(input => input.value = data[input.name]); $('fields').disabled = false; $('auth').open = false; }
  catch (error) { message(error.message, true); $('auth').open = true; }
  finally { $('load').disabled = false; }
}
$('load').onclick = load;
$('apiKey').oninput = () => { $('fields').disabled = true; };
$('settingsForm').onsubmit = async event => {
  event.preventDefault();
  const values = Object.fromEntries(inputs.map(input => [input.name, Number(input.value)]));
  for (const prefix of ['request_interval_', 'account_request_interval_']) {
    if (values[prefix + 'min_seconds'] <= 0 || values[prefix + 'max_seconds'] <= 0) { message('请求间隔必须大于 0', true); return; }
    if (values[prefix + 'min_seconds'] > values[prefix + 'max_seconds']) { message('最小间隔不能大于最大间隔', true); return; }
  }
  $('fields').disabled = true; $('load').disabled = true;
  try { await request('PUT', values); message('已保存，后续调度和请求使用新设置'); }
  catch (error) { message(error.message, true); }
  finally { $('fields').disabled = false; $('load').disabled = false; }
};
if ($('apiKey').value) load(); else $('auth').open = true;
</script></body></html>'''
