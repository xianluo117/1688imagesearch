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
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: separate; border-spacing: 0; overflow: hidden; }
th, td { text-align: left; padding: 10px 9px; border-bottom: 1px solid #eef2f7; white-space: nowrap; }
th { background: #f8fafc; font-weight: 600; }
tr:last-child td { border-bottom: 0; }
.badge { display: inline-block; padding: 3px 7px; border-radius: 999px; background: #e2e8f0; }
.badge.queued { background: #fef3c7; }.badge.running { background: #dbeafe; }
.empty { text-align: center; color: #64748b; padding: 28px; }
@media (max-width: 700px) { input[type="password"] { min-width: 180px; flex: 1; } th, td { padding: 8px 6px; } }
</style>
</head>
<body>
<main>
<h1>全局队列</h1>
<div class="toolbar">
  <input id="apiKey" type="password" placeholder="X-API-Key">
  <button id="saveKey">保存密钥</button>
  <select id="kindFilter"><option value="">全部类型</option><option value="upload">upload</option><option value="product">product</option><option value="sku">sku</option></select>
  <button id="refresh" class="secondary">立即刷新</button>
  <button id="cancel" class="danger" disabled>取消选中任务</button>
</div>
<div id="message"></div>
<div class="cards" id="cards"></div>
<div class="status" id="scheduler"></div>
<div class="table-wrap"><table>
<thead><tr><th><input id="selectAll" type="checkbox"></th><th>序号</th><th>类型</th><th>状态</th><th>任务 ID</th><th>依赖任务</th><th>创建时间</th><th>开始时间</th><th>取消请求</th></tr></thead>
<tbody id="tasks"><tr><td colspan="9" class="empty">请输入 API Key 后加载</td></tr></tbody>
</table></div>
</main>
<script>
const $ = id => document.getElementById(id);
const labels = { queued: '排队中', running: '执行中', succeeded: '成功', failed: '失败', cancelled: '已取消' };
const counts = ['queued', 'running', 'succeeded', 'failed', 'cancelled'];
let taskRows = [];
$('apiKey').value = sessionStorage.getItem('queue-api-key') || '';
function headers() { return { 'X-API-Key': $('apiKey').value.trim() }; }
function time(value) { return value ? new Date(value * 1000).toLocaleString() : '-'; }
function showError(error) { $('message').textContent = error; }
function renderSummary(snapshot) {
  const c = snapshot.counts;
  $('cards').innerHTML = counts.map(key => `<div class="card"><small>${labels[key]}</small><strong>${c[key] || 0}</strong></div>`).join('') +
    `<div class="card"><small>容量</small><strong>${c.queued + c.running}/${snapshot.capacity}</strong></div>`;
  const s = snapshot.scheduler;
  $('scheduler').textContent = `调度器：${s.state}${s.reason ? `（${s.reason}）` : ''}　接收新任务：${snapshot.accepting ? '是' : '否'}${s.cooldown_remaining ? `　冷却剩余：${Math.ceil(s.cooldown_remaining)} 秒` : ''}`;
}
function renderTasks() {
  const kind = $('kindFilter').value;
  const rows = taskRows.filter(task => !kind || task.kind === kind);
  $('selectAll').checked = false;
  $('cancel').disabled = !rows.length;
  $('tasks').innerHTML = rows.length ? rows.map(task => `<tr>
    <td><input class="task-check" type="checkbox" value="${task.task_id}"></td><td>${task.seq}</td><td>${task.kind}</td>
    <td><span class="badge ${task.status}">${labels[task.status] || task.status}</span></td>
    <td title="${task.task_id}">${task.task_id.slice(0, 8)}…</td><td>${task.dependency_id ? task.dependency_id.slice(0, 8) + '…' : '-'}</td>
    <td>${time(task.created_at)}</td><td>${time(task.started_at)}</td><td>${task.cancel_requested ? '是' : '否'}</td></tr>`).join('') : '<tr><td colspan="9" class="empty">暂无排队或执行中的任务</td></tr>';
}
async function refresh() {
  if (!$('apiKey').value.trim()) return;
  try {
    const options = { headers: headers() };
    const [summaryResponse, taskResponse] = await Promise.all([fetch('/api/v1/queue', options), fetch('/api/v1/queue/tasks', options)]);
    if (!summaryResponse.ok || !taskResponse.ok) throw new Error(`请求失败（${summaryResponse.status}/${taskResponse.status}）`);
    renderSummary(await summaryResponse.json());
    taskRows = (await taskResponse.json()).tasks;
    renderTasks(); showError('');
  } catch (error) { showError(error.message); }
}
$('saveKey').onclick = () => { sessionStorage.setItem('queue-api-key', $('apiKey').value.trim()); refresh(); };
$('refresh').onclick = refresh;
$('kindFilter').onchange = renderTasks;
$('selectAll').onchange = event => document.querySelectorAll('.task-check').forEach(box => box.checked = event.target.checked);
$('cancel').onclick = async () => {
  const ids = [...document.querySelectorAll('.task-check:checked')].map(box => box.value);
  if (!ids.length || !confirm(`确认取消 ${ids.length} 个任务？`)) return;
  try {
    const response = await fetch('/api/v1/queue/cancel', { method: 'POST', headers: { ...headers(), 'Content-Type': 'application/json' }, body: JSON.stringify({ task_ids: ids }) });
    if (!response.ok) throw new Error(`取消失败（${response.status}）`);
    await refresh();
  } catch (error) { showError(error.message); }
};
setInterval(refresh, 5000);
if ($('apiKey').value) refresh();
</script>
</body>
</html>'''
