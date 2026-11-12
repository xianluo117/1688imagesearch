"""Small dependency-free queue administration page."""

QUEUE_PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>全局队列</title>
<style>
:root { color-scheme: light; font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif; }
body { margin: 0; background: #f4f6f8; color: #1f2937; }
main { max-width: 1180px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { margin: 0 0 18px; font-size: 26px; }
.toolbar, .card, table { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; }
.toolbar { display: flex; gap: 8px; flex-wrap: wrap; padding: 14px; align-items: center; }
input, select, button { font: inherit; padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 6px; }
input[type="password"] { min-width: 280px; }
button { cursor: pointer; background: #2563eb; color: #fff; border-color: #2563eb; }
button.secondary { background: #fff; color: #374151; border-color: #cbd5e1; }
button.danger { background: #dc2626; border-color: #dc2626; }
button:disabled { opacity: .5; cursor: not-allowed; }
#message { color: #b91c1c; min-height: 1.5em; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 10px; margin: 16px 0; }
.card { padding: 14px; }
.card strong { display: block; font-size: 24px; margin-top: 5px; }
.card small { color: #64748b; }
.status { margin: 10px 0; color: #475569; }
.account { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 8px 0; }
.account input { flex: 1; min-width: 240px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; }
th, td { text-align: left; padding: 10px 9px; border-bottom: 1px solid #eef2f7; white-space: nowrap; }
th { background: #f8fafc; font-weight: 600; }
tr:last-child td { border-bottom: 0; }
.badge { display: inline-block; padding: 3px 7px; border-radius: 999px; background: #e2e8f0; }
.badge.queued { background: #fef3c7; }.badge.running { background: #dbeafe; }
.badge.succeeded { background: #dcfce7; color: #166534; }
.badge.failed { background: #fee2e2; color: #991b1b; }
.task-error { color: #b91c1c; max-width: 320px; white-space: normal; margin-top: 6px; }
.task-note { color: #64748b; font-size: 12px; margin-top: 6px; }
.task-detail { margin-top: 8px; }
.attempts { margin-top: 6px; color: #475569; }
.empty { text-align: center; color: #64748b; padding: 28px; }
* { box-sizing: border-box; }
body { background: #f5f7fb; font-size: 14px; line-height: 1.6; }
.sidebar { position: fixed; inset: 0 auto 0 0; width: 220px; background: #fff; border-right: 1px solid #e5eaf2; padding: 30px 18px; display: flex; flex-direction: column; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: #182438; margin-bottom: 48px; }
.brand b { background: #eff5ff; color: #2563eb; padding: 8px; border-radius: 10px; font-size: 21px; }
.brand span { font-size: 11px; font-weight: 750; letter-spacing: 1px; }.brand small { display: block; color: #94a3b8; font-size: 10px; letter-spacing: 0; font-weight: 400; }
.nav-label { font-size: 11px; color: #94a3b8; padding: 0 14px; margin-bottom: 12px; letter-spacing: 2px; }
nav a { display: flex; justify-content: space-between; padding: 12px 14px; margin-bottom: 6px; border-radius: 9px; color: #64748b; text-decoration: none; font-weight: 600; }nav a.active { background: #edf3ff; color: #2563eb; }nav a span { opacity: .5; font-size: 11px; }.sidebar-foot { margin-top: auto; color: #94a3b8; font-size: 11px; padding: 14px; line-height: 1.9; }
main { max-width: 1800px; margin: 0 0 0 220px; padding: 32px 32px 60px; }
.page-head { display: flex; justify-content: space-between; gap: 20px; align-items: center; margin-bottom: 24px; }.eyebrow { color: #8190a6; font-size: 10px; letter-spacing: 1.8px; margin-bottom: 8px; }h1 { font-size: 28px; letter-spacing: -.7px; margin: 0; }p { margin: 5px 0 0; color: #8190a6; font-size: 13px; }h2 { margin: 0; font-size: 16px; font-weight: 650; }.head-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }#updatedAt { font-size: 11px; color: #8190a6; }
button { font-size: 12px; font-weight: 600; padding: 9px 14px; transition: background .15s; }button:hover:not(:disabled) { filter: brightness(.96); }button.danger { color: #be3947; background: #fff4f4; border-color: #f3d8dc; }button:focus-visible, a:focus-visible, summary:focus-visible { outline: 3px solid #93b4fc; outline-offset: 3px; }input, select { font-size: 12px; background: #fff; color: #334155; min-height: 38px; }input:focus,select:focus { outline: 2px solid #bdd2fd; border-color: #709cf7; }input[type=checkbox] { min-height: 0; accent-color: #2563eb; }
.auth-panel { border: 1px solid #e5eaf2; background: #fff; border-radius: 10px; margin-bottom: 18px; }.auth-panel summary { cursor: pointer; padding: 12px 16px; color: #475569; font-size: 12px; }.auth-panel summary span { color: #94a3b8; margin-left: 12px; }.toolbar { border: 0; padding: 0 16px 16px; }.toolbar input { flex: 1; max-width: 520px; }#message { min-height: 0; }#message:not(:empty) { padding: 12px 16px; background: #fff1f2; border: 1px solid #fecdd3; border-radius: 10px; margin-bottom: 16px; }
.cards { grid-template-columns: repeat(6,minmax(0,1fr)); gap: 14px; margin: 0 0 18px; }.card { border-color: #e5eaf2; border-radius: 12px; padding: 18px 20px; }.card strong { font-size: 28px; font-weight: 650; line-height: 1.4; font-variant-numeric: tabular-nums; }.card small { font-size: 12px; }.card.running strong { color: #2563eb; }.card.failed strong { color: #c04450; }.scheduler { background: #edf3ff; border: 1px solid #dfe9fc; color: #46638e; border-radius: 10px; padding: 12px 16px; font-size: 12px; margin-bottom: 24px; }
.panel { background: #fff; border: 1px solid #e5eaf2; border-radius: 12px; margin-bottom: 24px; overflow: hidden; }.section-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 20px 22px; }.subtle { color: #8a98ac; font-size: 11px; font-weight: 400; }.accounts { display: grid; grid-template-columns: repeat(auto-fit,minmax(290px,1fr)); gap: 14px; padding: 0 22px 22px; }.account { margin: 0; padding: 16px; background: #fafbfd; border: 1px solid #e8edf5; border-radius: 10px; gap: 8px; }.account > span:first-child { flex-basis: 100%; font-size: 13px; font-weight: 600; color: #475569; overflow-wrap: anywhere; }.account input { min-width: 0; width: 100%; flex-basis: 100%; font-family: ui-monospace,monospace; }.account > span:not(:first-child) { color: #64748b; font-size: 11px; }.account .danger { margin-left: auto; }
.filters { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 0 22px 18px; }.filters input { min-width: 220px; flex: 1; max-width: 380px; }.filters .subtle { margin-left: auto; }table { border: 0; border-radius: 0; font-size: 12px; }th { color: #8391a5; font-size: 11px; font-weight: 500; padding: 13px 16px; background: #f8fafc; border-top: 1px solid #edf1f7; }td { padding: 16px; vertical-align: top; color: #526079; }tbody tr:hover { background: #fafcff; }.badge { font-size: 11px; font-weight: 600; padding: 3px 9px; }.badge.queued { color: #976b1c; }.badge.running { color: #245ec0; }.task-error { font-size: 11px; max-width: 240px; }.task-note { font-size: 10px; max-width: 220px; white-space: normal; }.task-detail button { border: 0; padding: 0; color: #4f73b5; background: transparent; font-size: 11px; }.attempts { font-size: 11px; max-width: 300px; white-space: normal; }.attempts summary { cursor: pointer; }.empty { padding: 44px 20px; }.table-footer { display: flex; align-items: center; justify-content: space-between; border-top: 1px solid #edf1f7; padding: 14px 22px; color: #94a3b8; font-size: 12px; }
dialog { width: min(900px,92vw); max-height: 85vh; border: 1px solid #e5eaf2; border-radius: 16px; padding: 24px; color: #334155; box-shadow: 0 24px 80px #1e293b26; }dialog::backdrop { background: #1e293b55; backdrop-filter: blur(3px); }.dialog-head { display: flex; justify-content: space-between; gap: 16px; align-items: center; }pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f7f9fc; padding: 18px; border-radius: 10px; font-size: 12px; max-height: 58vh; overflow: auto; }
@media(max-width:1200px) { .cards { grid-template-columns: repeat(3,1fr); }.page-head { align-items: flex-start; flex-direction: column; } }
@media(max-width:760px) { .sidebar { position: static; width: auto; padding: 16px; border-bottom: 1px solid #e5eaf2; }.brand { margin-bottom: 12px; }.nav-label,.sidebar-foot { display: none; }nav { display: flex; gap: 8px; }nav a { flex: 1; margin: 0; }main { margin: 0; padding: 24px 14px; }.cards { gap: 8px; }.card { padding: 12px; }.card strong { font-size: 24px; }.section-head { padding: 16px; }.accounts { padding: 0 16px 16px; }.filters { padding: 0 16px 16px; }.filters .subtle { margin-left: 0; }.head-actions { gap: 6px; }.auth-panel summary span { display: block; margin-left: 16px; } }
</style>
</head>
<body>
<aside class="sidebar"><a class="brand" href="/queue"><b>1688</b><span>WORKSPACE<small>采集管理工作台</small></span></a><div class="nav-label">工作空间</div><nav><a class="active" href="/queue">任务队列 <span>01</span></a><a href="/console">告警设置 <span>02</span></a></nav><div class="sidebar-foot">轻量管理 · 清晰执行<br>队列 / 账号 / 运行状态</div></aside>
<main>
<header class="page-head"><div><div class="eyebrow">WORKSPACE / OVERVIEW</div><h1>任务队列</h1><p>关注执行结果，让任务与账号状态一目了然。</p></div><div class="head-actions"><span id="updatedAt">尚未连接</span><button id="autoRefresh" class="secondary">暂停自动刷新</button><button id="refresh">刷新数据</button></div></header>
<details class="auth-panel"><summary>访问凭据 <span>仅保存在当前浏览器会话</span></summary><div class="toolbar"><input id="apiKey" type="password" placeholder="输入 SEARCH_API_KEY" aria-label="API 密钥" autocomplete="off"><button id="saveKey">保存并连接</button></div></details>
<div id="message" role="status"></div>
<div class="cards" id="cards"><div class="card"><small>运行概览</small><strong>—</strong><small>连接后显示实时统计</small></div></div>
<div class="status scheduler" id="scheduler">等待连接 · 保存访问凭据后加载运行状态</div>
<section class="panel"><div class="section-head"><div><h2>账号与代理</h2><p>独立管理每个账号的连接环境。</p></div><span class="subtle">代理修改需点击保存</span></div><div id="accounts" class="accounts"><div class="empty">连接后显示账号</div></div></section>
<section class="panel"><div class="section-head"><div><h2>任务记录 <span id="taskCount" class="subtle"></span></h2><p>成功、失败与历史尝试分开呈现。</p></div><button id="cancel" class="danger" disabled>取消选中任务</button></div>
<div class="filters"><input id="search" type="search" placeholder="搜索任务 ID 或账号…" aria-label="搜索任务"><select id="kindFilter" aria-label="任务类型"><option value="">全部类型</option><option value="upload">图片上传</option><option value="product">商品搜索</option><option value="sku">SKU 采集</option></select><select id="statusFilter" aria-label="任务状态"><option value="">全部状态</option><option value="queued">排队中</option><option value="running">执行中</option><option value="succeeded">成功</option><option value="failed">失败</option><option value="cancelled">已取消</option></select><span class="subtle">仅排队及执行中的任务可取消</span></div>
<div class="table-wrap"><table>
<thead><tr><th><input id="selectAll" type="checkbox"></th><th>序号</th><th>类型</th><th>状态</th><th>任务 ID</th><th>依赖任务</th><th>创建时间</th><th>开始时间</th><th>取消请求</th></tr></thead>
<tbody id="tasks"><tr><td colspan="9" class="empty">请输入 API Key 后加载</td></tr></tbody>
</table></div><div class="table-footer"><span id="pageInfo">第 1 页</span><div><button id="prevPage" class="secondary">上一页</button> <button id="nextPage" class="secondary">下一页</button></div></div></section>
<dialog id="errorDialog"><div class="dialog-head"><div><h2>任务详情</h2><span class="subtle">执行结果与完整诊断记录</span></div><div><button id="copyDetails" class="secondary">复制 JSON</button> <button id="closeDetails">关闭</button></div></div><pre id="errorDetails"></pre></dialog>
</main>
<script>
const $ = id => document.getElementById(id);
const labels = { queued: '排队中', running: '执行中', succeeded: '成功', failed: '失败', cancelled: '已取消' };
const counts = ['queued', 'running', 'succeeded', 'failed', 'cancelled'];
let taskRows = [];
let pageIndex = 0;
let refreshing = false;
let automatic = true;
const pageSize = 25;
const selected = new Set();
const kindLabels = { upload: '图片上传', product: '商品搜索', sku: 'SKU 采集' };
const accountLabels = { normal: '正常', active: '正常', ready: '就绪', paused: '已暂停', cooldown: '冷却中', deleting: '删除中', disabled: '已禁用' };
$('apiKey').value = sessionStorage.getItem('queue-api-key') || sessionStorage.getItem('console-api-key') || '';
if (!$('apiKey').value) document.querySelector('.auth-panel').open = true;
function headers() { return { 'X-API-Key': $('apiKey').value.trim() }; }
function time(value) { return value ? new Date(value * 1000).toLocaleString() : '-'; }
function showError(error) { $('message').textContent = error; }
function text(value) { const node = document.createElement('span'); node.textContent = value ?? ''; return node.innerHTML; }
function renderSummary(snapshot) {
  const c = snapshot.counts;
  $('cards').innerHTML = counts.map(key => `<div class="card ${key}"><small>${labels[key]}</small><strong>${c[key] || 0}</strong></div>`).join('') +
    `<div class="card"><small>容量</small><strong>${c.queued + c.running}/${snapshot.capacity}</strong></div>`;
  const s = snapshot.scheduler;
  const schedulerLabels = { running: '运行中', normal: '正常运行', paused: '已暂停', stopped: '已停止', cooldown: '冷却中', stopping: '停止中' };
  $('scheduler').textContent = `调度器：${schedulerLabels[s.state] || s.state}${s.reason ? `（${s.reason}）` : ''}　接收新任务：${snapshot.accepting ? '是' : '否'}${s.cooldown_remaining ? `　冷却剩余：${Math.ceil(s.cooldown_remaining)} 秒` : ''}`;
}
function renderTaskStatus(task) {
  const historicalFailure = (task.attempts || []).some(a => a.status === 'failed');
  const succeeded = task.status === 'succeeded';
  const error = task.error_code === 'access_restricted' && task.error_message === 'captcha_required'
    ? '触发安全验证，需要完成验证码验证；未获取到商品数据'
    : [task.error_code, task.error_message].filter(Boolean).join(': ');
  const currentError = !succeeded && (task.status === 'failed' || Boolean(error));
  const label = succeeded && historicalFailure ? '重试后成功' :
    task.status === 'queued' && !task.account_id ? '等待账号' : labels[task.status] || task.status;
  const detailLabel = currentError ? '错误详情' : historicalFailure ? '历史失败详情' : '';
  return `<span class="badge ${text(task.status)}">${text(label)}</span>` +
    (currentError && error ? `<div class="task-error">${text(error)}</div>` : '') +
    (succeeded && historicalFailure ? '<div class="task-note">最终结果成功，历史失败不影响本次结果</div>' : '') +
    (detailLabel ? `<div class="task-detail"><button class="error-detail secondary" data-id="${text(task.task_id)}">${detailLabel}</button></div>` : '');
}
function renderAttempts(task) {
  const attempts = task.attempts || [];
  if (!attempts.length) return '';
  return `<details class="attempts"><summary>执行记录（${attempts.length} 次）</summary>${attempts.map(a =>
    text(`第 ${a.attempt} 次：${a.account_id || '-'} / ${labels[a.status] || a.status} / ${a.error_message || a.error_code || '-'}`)
  ).join('<br>')}</details>`;
}
function cancellable(task) { return ['queued', 'running'].includes(task.status) && !task.cancel_requested; }
function syncSelection() {
  const boxes = [...document.querySelectorAll('.task-check:not(:disabled)')];
  $('selectAll').disabled = !boxes.length;
  $('selectAll').checked = boxes.length > 0 && boxes.every(box => box.checked);
  $('selectAll').indeterminate = boxes.some(box => box.checked) && !$('selectAll').checked;
  $('cancel').disabled = !selected.size;
  $('cancel').textContent = selected.size ? `取消选中任务（${selected.size}）` : '取消选中任务';
}
function renderTasks() {
  const kind = $('kindFilter').value;
  const status = $('statusFilter').value;
  const query = $('search').value.trim().toLowerCase();
  for (const id of selected) if (!taskRows.some(t => t.task_id === id && cancellable(t))) selected.delete(id);
  const filtered = taskRows.filter(task => (!kind || task.kind === kind) && (!status || task.status === status) &&
    (!query || `${task.task_id} ${task.account_id || ''}`.toLowerCase().includes(query)));
  pageIndex = Math.max(0, Math.min(pageIndex, Math.ceil(filtered.length / pageSize) - 1));
  const rows = filtered.slice(pageIndex * pageSize, (pageIndex + 1) * pageSize);
  $('taskCount').textContent = `${filtered.length} 条`;
  $('pageInfo').textContent = `第 ${pageIndex + 1} / ${Math.max(1, Math.ceil(filtered.length / pageSize))} 页 · 每页 ${pageSize} 条`;
  $('prevPage').disabled = pageIndex === 0;
  $('nextPage').disabled = (pageIndex + 1) * pageSize >= filtered.length;
  $('tasks').innerHTML = rows.length ? rows.map(task => `<tr>
    <td><input class="task-check" aria-label="选择任务 ${text(task.task_id)}" type="checkbox" value="${text(task.task_id)}" ${cancellable(task) ? '' : 'disabled'} ${selected.has(task.task_id) ? 'checked' : ''}></td><td>${text(task.seq)}</td><td>${text(kindLabels[task.kind] || task.kind)}</td>
    <td>${renderTaskStatus(task)}</td>
    <td title="${text(task.task_id)}"><button class="error-detail secondary" data-id="${text(task.task_id)}">${text(task.task_id.slice(0, 8))}…</button><br>${text(task.account_id || '待分配账号')} · 第 ${text(task.attempt_count)} 次
    ${renderAttempts(task)}</td><td>${task.dependency_id ? text(task.dependency_id.slice(0, 8)) + '…' : '-'}</td>
    <td>${text(time(task.created_at))}</td><td>${text(time(task.started_at))}</td><td>${task.cancel_requested ? '已请求' : '—'}</td></tr>`).join('') : '<tr><td colspan="9" class="empty">暂无匹配任务<br><span class="subtle">调整筛选条件，或等待新任务进入队列</span></td></tr>';
  syncSelection();
}
const accountEditors = new Map();
function renderAccounts(accounts) {
  if (!accountEditors.size) $('accounts').replaceChildren();
  const ids = new Set(accounts.map(a => a.account_id));
  for (const [id, editor] of accountEditors) {
    if (!ids.has(id)) { editor.row.remove(); accountEditors.delete(id); }
  }
  for (const account of accounts) {
    let editor = accountEditors.get(account.account_id);
    if (!editor) {
      const row = document.createElement('div');
      row.className = 'account';
      const label = document.createElement('span');
      const input = document.createElement('input');
      input.placeholder = 'http:// 或 socks5://，留空直连';
      input.setAttribute('aria-label', `${account.display_name} 代理地址`);
      input.autocomplete = 'off';
      input.spellcheck = false;
      const button = document.createElement('button');
      button.textContent = '保存代理';
      const status = document.createElement('span');
      const remove = document.createElement('button');
      remove.className = 'danger';
      remove.textContent = '删除';
      remove.onclick = async () => {
        if (!confirm(`确认删除账号“${account.display_name}”？该账号的登录凭据将被移除。`)) return;
        remove.disabled = true;
        try {
          const response = await fetch(`/api/v1/accounts/${encodeURIComponent(account.account_id)}`, { method: 'DELETE', headers: headers() });
          if (!response.ok) throw new Error(`删除失败（${response.status}）`);
          await refresh();
        } catch (error) { showError(error.message); remove.disabled = false; }
      };
      editor = { row, label, input, button, remove, status, dirty: false };
      const current = editor;
      input.oninput = () => { current.dirty = true; status.textContent = ''; };
      button.onclick = async () => {
        const value = input.value.trim();
        button.disabled = true;
        status.textContent = '';
        try {
          const response = await fetch(`/api/v1/accounts/${encodeURIComponent(account.account_id)}/proxy`, {
            method: 'PUT', headers: { ...headers(), 'Content-Type': 'application/json' },
            body: JSON.stringify({ proxy_url: value }),
          });
          const result = await response.json();
          if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `保存失败（${response.status}）`);
          if (input.value.trim() === value) { input.value = result.proxy_url; current.dirty = false; }
          status.textContent = '已保存';
        } catch (error) { status.textContent = error.message; }
        finally { button.disabled = false; }
      };
      row.append(label, input, button, remove, status);
      $('accounts').append(row);
      accountEditors.set(account.account_id, editor);
    }
    editor.label.textContent = `${account.display_name}: ${accountLabels[account.state] || account.state}${account.reason ? ' / ' + account.reason : ''}`;
    editor.remove.disabled = account.state === 'deleting';
    editor.remove.textContent = account.state === 'deleting' ? '删除中' : '删除';
    editor.input.disabled = account.state === 'deleting';
    if (account.state === 'deleting') editor.button.disabled = true;
    if (!editor.dirty && !editor.button.disabled) editor.input.value = account.proxy_url || '';
  }
}
async function refresh() {
  if (!$('apiKey').value.trim() || refreshing) return;
  refreshing = true;
  $('refresh').disabled = true;
  try {
    const options = { headers: headers() };
    const [summaryResponse, taskResponse, accountsResponse] = await Promise.all([fetch('/api/v1/queue', options), fetch('/api/v1/queue/tasks?status=all', options), fetch('/api/v1/accounts', options)]);
    if (!summaryResponse.ok || !taskResponse.ok) throw new Error(`请求失败（${summaryResponse.status}/${taskResponse.status}）`);
    renderSummary(await summaryResponse.json());
    taskRows = (await taskResponse.json()).tasks;
    renderTasks();
    $('updatedAt').textContent = `更新于 ${new Date().toLocaleTimeString()}`;
    if (accountsResponse.ok) {
      renderAccounts((await accountsResponse.json()).accounts);
      showError('');
    } else showError(`任务已更新，但账号查询失败（${accountsResponse.status}）`);
  } catch (error) { showError(error.message); }
  finally { refreshing = false; $('refresh').disabled = false; }
}
$('saveKey').onclick = () => { sessionStorage.setItem('queue-api-key', $('apiKey').value.trim()); sessionStorage.setItem('console-api-key', $('apiKey').value.trim()); refresh(); };
$('refresh').onclick = refresh;
for (const id of ['kindFilter', 'statusFilter', 'search']) $(id).addEventListener(id === 'search' ? 'input' : 'change', () => { pageIndex = 0; renderTasks(); });
$('prevPage').onclick = () => { pageIndex--; renderTasks(); };
$('nextPage').onclick = () => { pageIndex++; renderTasks(); };
$('autoRefresh').onclick = () => { automatic = !automatic; $('autoRefresh').textContent = automatic ? '暂停自动刷新' : '恢复自动刷新'; };
$('selectAll').onchange = event => {
  document.querySelectorAll('.task-check:not(:disabled)').forEach(box => { box.checked = event.target.checked; box.checked ? selected.add(box.value) : selected.delete(box.value); });
  syncSelection();
};
$('tasks').onchange = event => { if (event.target.matches('.task-check')) { event.target.checked ? selected.add(event.target.value) : selected.delete(event.target.value); syncSelection(); } };
$('cancel').onclick = async () => {
  const ids = [...selected];
  if (ids.length > 500) { showError('每次最多取消 500 个任务，请减少选择。'); return; }
  if (!ids.length || !confirm(`确认取消 ${ids.length} 个任务？`)) return;
  $('cancel').disabled = true;
  try {
    const response = await fetch('/api/v1/queue/cancel', { method: 'POST', headers: { ...headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ task_ids: ids }) });
    if (!response.ok) throw new Error(`取消失败（${response.status}）`);
    selected.clear();
    await refresh();
  } catch (error) { showError(error.message); }
  finally { syncSelection(); }
};
$('tasks').onclick = async event => {
  const button = event.target.closest('.error-detail');
  if (!button) return;
  try {
    const response = await fetch(`/api/v1/queue/tasks/${encodeURIComponent(button.dataset.id)}/details`, { headers: headers() });
    if (!response.ok) throw new Error(`详情查询失败（${response.status}）`);
    $('errorDetails').textContent = JSON.stringify(await response.json(), null, 2);
    $('errorDialog').showModal();
  } catch (error) { showError(error.message); }
};
$('closeDetails').onclick = () => $('errorDialog').close();
$('copyDetails').onclick = async () => {
  try { await navigator.clipboard.writeText($('errorDetails').textContent); }
  catch (error) { showError(error.message); }
};
setInterval(() => { if (automatic && !document.hidden) refresh(); }, 5000);
if ($('apiKey').value) refresh();
</script>
</body>
</html>'''
