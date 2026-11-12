"""Small dependency-free DingTalk alert console page."""

CONSOLE_PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>告警设置 · 1688 WORKSPACE</title>
<style>
:root { font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif; color: #17243b; background: #f3f6fb; font-size: 14px; }
* { box-sizing: border-box; }
body { margin: 0; }
.sidebar { position: fixed; inset: 0 auto 0 0; width: 220px; padding: 32px 20px; background: #fff; border-right: 1px solid #e2e8f0; display: flex; flex-direction: column; }
.brand { color: #17243b; font-size: 15px; font-weight: 800; letter-spacing: .04em; margin-bottom: 8px; }
.brand span { color: #2563eb; }
.brand-caption { color: #8491a5; font-size: 12px; margin-bottom: 36px; }
nav { display: grid; gap: 8px; }
nav a { padding: 12px 16px; border-radius: 12px; color: #64748b; text-decoration: none; font-weight: 600; }
nav a:hover { background: #f3f6fb; color: #2563eb; }
nav a[aria-current="page"] { color: #2563eb; background: #edf3ff; }
.sidebar-note { margin-top: auto; color: #94a3b8; font-size: 12px; line-height: 1.8; }
main { margin-left: 220px; padding: 32px; }
.content { max-width: 1120px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin-bottom: 28px; }
.eyebrow { color: #64748b; font-size: 12px; margin: 0 0 8px; letter-spacing: .06em; }
h1 { font-size: 28px; letter-spacing: -.03em; margin: 0 0 8px; }
.subtitle, .hint { color: #64748b; line-height: 1.7; margin: 0; }
.header-tag { white-space: nowrap; background: #eaf1ff; color: #2563eb; padding: 8px 12px; border-radius: 12px; font-size: 12px; font-weight: 600; }
section { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 3px 12px #1e3a5f03; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 20px; }
h2 { font-size: 17px; margin: 0 0 6px; }
.step { color: #2563eb; background: #edf3ff; border-radius: 8px; font-size: 12px; padding: 5px 8px; flex-shrink: 0; }
label { display: block; margin-bottom: 8px; color: #334155; font-weight: 600; font-size: 13px; }
input, textarea, button { font: inherit; border: 1px solid #dbe3ed; border-radius: 12px; }
input, textarea { display: block; width: 100%; padding: 12px 14px; background: #fff; color: #17243b; transition: border-color .15s, box-shadow .15s; }
input::placeholder, textarea::placeholder { color: #94a3b8; }
input:focus, textarea:focus { outline: none; border-color: #6999f4; box-shadow: 0 0 0 3px #2563eb14; }
button:focus-visible, a:focus-visible { outline: 3px solid #93b4f5; outline-offset: 3px; }
textarea { min-height: 104px; resize: vertical; line-height: 1.7; }
button { padding: 11px 18px; cursor: pointer; background: #2563eb; color: #fff; border-color: #2563eb; font-weight: 600; white-space: nowrap; transition: background .15s; }
button:hover { background: #1d4ed8; }
button.secondary { background: #fff; color: #475569; border-color: #dbe3ed; }
button.secondary:hover { background: #f8fafc; }
button:disabled { cursor: wait; opacity: .55; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.key-row { display: flex; align-items: flex-end; gap: 12px; }
.key-row .field { flex: 1; min-width: 0; }
.hint { font-size: 12px; margin-top: 8px; }
.actions { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin-top: 20px; }
.test-group { border-top: 1px solid #eef2f7; padding-top: 24px; margin-top: 24px; }
.test-group h3 { font-size: 14px; margin: 0 0 16px; }
#message { display: none; border-radius: 12px; padding: 13px 16px; margin-bottom: 20px; line-height: 1.6; overflow-wrap: anywhere; }
#message.success { display: block; color: #166534; background: #edf9f0; border: 1px solid #ccebd4; }
#message.error { display: block; color: #b42318; background: #fff1f0; border: 1px solid #ffd5d1; }
.status-summary { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 14px 16px; background: #f8fafc; border: 1px solid #edf1f7; border-radius: 12px; margin-bottom: 20px; }
#status { color: #64748b; line-height: 1.6; overflow-wrap: anywhere; }
#status[data-state="ready"] { color: #15803d; }
#status[data-state="error"] { color: #b42318; }
#updated { color: #8491a5; font-size: 12px; flex-shrink: 0; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { color: #64748b; background: #f8fafc; font-size: 12px; font-weight: 600; }
th, td { text-align: left; padding: 14px 16px; border-bottom: 1px solid #edf1f7; vertical-align: top; }
td { white-space: pre-wrap; overflow-wrap: anywhere; }
th:first-child { width: 34%; } th:nth-child(2) { width: 25%; }
.empty { text-align: center; padding: 36px 16px; color: #94a3b8; }
.footer-note { text-align: center; color: #94a3b8; font-size: 12px; margin: 24px 0 0; }
@media (max-width: 850px) { main { padding: 24px; } .row { grid-template-columns: 1fr; } .status-summary { align-items: flex-start; flex-direction: column; gap: 8px; } }
@media (max-width: 650px) {
  .sidebar { position: static; width: auto; padding: 20px; border-right: 0; border-bottom: 1px solid #e2e8f0; }
  .brand { margin-bottom: 16px; } .brand-caption, .sidebar-note { display: none; }
  nav { display: flex; gap: 8px; } nav a { flex: 1; text-align: center; padding: 10px; }
  main { margin-left: 0; padding: 24px 16px; } h1 { font-size: 24px; }
  .header-tag { display: none; } section { padding: 20px; } .key-row { align-items: stretch; flex-direction: column; }
  .section-heading { gap: 12px; } th, td { padding: 12px 10px; } table { min-width: 500px; }
}
</style>
</head>
<body>
<aside class="sidebar">
  <div class="brand"><span>1688</span> WORKSPACE</div>
  <div class="brand-caption">图搜任务管理工作台</div>
  <nav aria-label="主导航"><a href="/queue">任务队列</a><a href="/console" aria-current="page">告警设置</a></nav>
  <div class="sidebar-note">工作台 / 通知管理<br>安全连接 · 按需配置</div>
</aside>
<main><div class="content">
<header class="page-header"><div><p class="eyebrow">WORKSPACE / ALERTS</p><h1>告警设置</h1><p class="subtitle">管理钉钉机器人通知，及时了解任务运行状态。</p></div><span class="header-tag">通知管理</span></header>
<div id="message" role="status" aria-live="polite" aria-atomic="true"></div>
<section aria-labelledby="accessHeading">
  <div class="section-heading"><div><h2 id="accessHeading">访问密钥</h2><p class="subtitle">验证身份后，即可查看告警状态与管理通知配置。</p></div><span class="step">01</span></div>
  <div class="key-row"><div class="field"><label for="apiKey">SEARCH_API_KEY</label><input id="apiKey" type="password" autocomplete="off" placeholder="输入服务端配置的搜索密钥" aria-describedby="keyHint"></div><button id="saveKey" type="button">验证并保存</button></div>
  <p class="hint" id="keyHint">密钥仅保存在当前浏览器会话中；请勿使用 Cookie 上传密钥。</p>
</section>
<section aria-labelledby="configHeading">
  <div class="section-heading"><div><h2 id="configHeading">钉钉机器人配置</h2><p class="subtitle">填写机器人凭据，配置任务告警的通知渠道。</p></div><span class="step">02</span></div>
  <div class="row"><div><label for="token">Webhook access_token</label><input id="token" type="password" autocomplete="new-password" placeholder="输入机器人 access_token" aria-describedby="credentialHint"></div><div><label for="secret">加签 Secret</label><input id="secret" type="password" autocomplete="new-password" placeholder="输入机器人加签 Secret" aria-describedby="credentialHint"></div></div>
  <p class="hint" id="credentialHint">已保存的凭据不会回显。保存成功后，输入框会自动清空。</p>
  <div class="actions"><button id="save" type="button">保存配置</button></div>
  <div class="test-group"><h3>测试通知</h3><label for="text">测试消息</label><textarea id="text" aria-describedby="testHint">1688 告警机器人测试消息</textarea><p class="hint" id="testHint">点击后将向已配置的钉钉机器人发送一条真实消息。</p><div class="actions"><button id="test" type="button" class="secondary">发送测试消息</button></div></div>
</section>
<section aria-labelledby="alertsHeading">
  <div class="section-heading"><div><h2 id="alertsHeading">告警状态</h2><p class="subtitle">查看机器人配置状态与最近的通知记录。</p></div><button id="refresh" type="button" class="secondary">刷新状态</button></div>
  <div class="status-summary"><div id="status" role="status" aria-live="polite">正在读取告警状态…</div><span id="updated">尚未更新</span></div>
  <div class="table-wrap"><table aria-label="最近告警记录"><thead><tr><th scope="col">告警原因</th><th scope="col">发送时间</th><th scope="col">状态 / 失败原因</th></tr></thead><tbody id="alerts"><tr><td colspan="3" class="empty">正在加载告警记录…</td></tr></tbody></table></div>
  <p class="hint">状态每 30 秒自动刷新，也可手动刷新。</p>
</section>
<p class="footer-note">1688 WORKSPACE · 告警与通知</p>
</div></main>
<script>
const $ = id => document.getElementById(id);
$('apiKey').value = sessionStorage.getItem('console-api-key') || sessionStorage.getItem('queue-api-key') || '';
let loading = false;
let acting = false;
function key() { return $('apiKey').value.trim(); }
function headers(json=false) { return { 'X-API-Key': key(), ...(json ? {'Content-Type':'application/json'} : {}) }; }
function time(v) { return v ? new Date(v * 1000).toLocaleString() : '—'; }
function show(v, error=false) { $('message').textContent = v; $('message').className = error ? 'error' : 'success'; }
function status(v, state='') { $('status').textContent = v; $('status').dataset.state = state; }
function empty(v) {
  const row = document.createElement('tr');
  const cell = document.createElement('td');
  cell.colSpan = 3; cell.className = 'empty'; cell.textContent = v;
  row.append(cell); $('alerts').replaceChildren(row);
}
async function request(path, method='GET', body) {
  if (!key()) throw Error('请先输入 SEARCH_API_KEY（不是 Cookie 上传密钥）');
  const response = await fetch(path, {
    method, headers: headers(body !== undefined),
    ...(body !== undefined ? {body: JSON.stringify(body)} : {})
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = payload && payload.detail;
    const message = response.status === 401 ? 'SEARCH_API_KEY 无效，请输入服务端配置的搜索密钥' :
      (typeof detail === 'string' ? detail : detail && detail.message);
    throw Error(`${message || response.statusText || '请求失败'}（HTTP ${response.status}）`);
  }
  return response.json();
}
async function load() {
  if (!key()) {
    status('等待验证 · 请先输入 SEARCH_API_KEY');
    empty('验证访问密钥后，即可查看告警记录');
    $('updated').textContent = '尚未更新';
    return;
  }
  status('正在更新告警状态…');
  const p = await request('/api/v1/console/alerts');
  status(p.configured ? '已配置 · 钉钉机器人已就绪' : '未配置 · 请先保存钉钉机器人凭据', p.configured ? 'ready' : '');
  $('alerts').replaceChildren();
  for (const a of p.alerts) {
    const row = document.createElement('tr');
    for (const value of [a.reason, time(a.sent_at), `${a.status}${a.error_details ? '\n' + a.error_details : ''}`]) {
      const cell = document.createElement('td'); cell.textContent = value; row.append(cell);
    }
    $('alerts').append(row);
  }
  if (!p.alerts.length) empty('暂无告警记录，后续通知将在这里展示');
  $('updated').textContent = `更新于 ${new Date().toLocaleTimeString()}`;
}
async function action(button, text, work) {
  if (acting || loading) return;
  acting = true;
  const buttons = [...document.querySelectorAll('button')];
  const label = button.textContent;
  buttons.forEach(b => b.disabled = true); button.textContent = text;
  try { await work(); }
  catch(e) { show(e.message || '操作失败，请稍后重试', true); }
  finally { buttons.forEach(b => b.disabled = false); button.textContent = label; acting = false; }
}
$('saveKey').onclick = () => action($('saveKey'), '正在验证…', async () => {
  await request('/api/v1/console/config');
  sessionStorage.setItem('console-api-key', key());
  sessionStorage.setItem('queue-api-key', key());
  show('访问密钥已验证，并保存到当前浏览器会话');
  await refreshStatus();
});
$('save').onclick = () => action($('save'), '正在保存…', async () => {
  await request('/api/v1/console/config', 'PUT', {access_token:$('token').value, secret:$('secret').value});
  $('token').value = ''; $('secret').value = '';
  show('机器人配置已保存，凭据输入已清空');
  await refreshStatus();
});
$('test').onclick = () => action($('test'), '正在发送…', async () => {
  await request('/api/v1/console/test-dingtalk', 'POST', {content:$('text').value});
  show('测试消息已发送，请到钉钉群查看');
});
async function refreshStatus() {
  if (loading) return false;
  loading = true;
  try { await load(); return true; }
  catch(e) {
    status(`更新失败 · ${e.message || '请稍后重试'}`, 'error');
    if ($('updated').textContent === '尚未更新') empty('暂时无法读取告警记录，请检查密钥后重试');
    return false;
  }
  finally { loading = false; }
}
$('refresh').onclick = () => action($('refresh'), '正在刷新…', async () => {
  if (await refreshStatus()) show(key() ? '告警状态已更新' : '请先输入并验证访问密钥', !key());
  else show('状态更新失败，请查看告警状态中的错误信息', true);
});
refreshStatus();
setInterval(() => { if (!acting) refreshStatus(); }, 30000);
</script></body></html>'''
