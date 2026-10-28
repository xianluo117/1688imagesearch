"""Small dependency-free DingTalk alert console page."""

CONSOLE_PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>告警控制台</title>
<style>
:root { font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif; color: #1f2937; }
body { margin: 0; background: #f4f6f8; } main { max-width: 900px; margin: auto; padding: 24px 16px 48px; }
section { background: #fff; border: 1px solid #e5e7eb; border-radius: 10px; padding: 18px; margin: 14px 0; }
h1 { margin: 0 0 18px; } h2 { font-size: 18px; margin: 0 0 14px; }
label { display: block; margin: 10px 0 5px; color: #475569; } input, textarea, button { box-sizing: border-box; font: inherit; padding: 9px 10px; border: 1px solid #cbd5e1; border-radius: 6px; width: 100%; }
textarea { min-height: 90px; resize: vertical; } button { width: auto; cursor: pointer; background: #2563eb; color: #fff; border-color: #2563eb; margin-top: 12px; } button.secondary { background: #fff; color: #374151; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; } #message { min-height: 1.5em; color: #b91c1c; } table { width: 100%; border-collapse: collapse; } th, td { text-align: left; padding: 9px; border-bottom: 1px solid #eef2f7; }
@media (max-width: 650px) { .row { grid-template-columns: 1fr; } }
</style>
</head>
<body><main>
<h1>告警控制台</h1>
<section><h2>访问密钥</h2><label>SEARCH_API_KEY</label><input id="apiKey" type="password" placeholder="输入后保存到当前浏览器"><button id="saveKey">保存密钥</button></section>
<section><h2>钉钉机器人配置</h2>
<div class="row"><div><label>Webhook access_token</label><input id="token" type="password"></div><div><label>加签 Secret</label><input id="secret" type="password"></div></div>
<button id="save">保存配置</button> <button id="test" class="secondary">测试发送</button>
<label>测试消息</label><textarea id="text">1688 告警机器人测试消息</textarea>
</section>
<section><h2>告警状态</h2><div id="status">加载中...</div><table><thead><tr><th>原因</th><th>最近发送时间</th><th>下次允许发送</th></tr></thead><tbody id="alerts"></tbody></table></section>
<div id="message"></div>
<script>
const $ = id => document.getElementById(id); $('apiKey').value = sessionStorage.getItem('console-api-key') || sessionStorage.getItem('queue-api-key') || '';
function key() { return $('apiKey').value.trim(); }
function headers(json=false) { return { 'X-API-Key': key(), ...(json ? {'Content-Type':'application/json'} : {}) }; }
function time(v) { return v ? new Date(v * 1000).toLocaleString() : '-'; }
function show(v, error=false) { $('message').textContent = v; $('message').style.color = error ? '#b91c1c' : '#166534'; }
async function load() { const r = await fetch('/api/v1/console/alerts', {headers: headers()}); if (!r.ok) throw Error('读取告警状态失败'); const p = await r.json(); $('status').textContent = p.configured ? '钉钉机器人已配置' : '尚未配置钉钉机器人'; $('alerts').innerHTML = p.alerts.length ? p.alerts.map(a => `<tr><td>${a.reason}</td><td>${time(a.last_sent_at)}</td><td>${time(a.next_allowed_at)}</td></tr>`).join('') : '<tr><td colspan="3">暂无自动告警记录</td></tr>'; }
$('saveKey').onclick = () => { sessionStorage.setItem('console-api-key', key()); load().catch(e => show(e.message, true)); };
$('save').onclick = async () => { try { const r = await fetch('/api/v1/console/config', {method:'PUT', headers:headers(true), body:JSON.stringify({access_token:$('token').value, secret:$('secret').value})}); if (!r.ok) throw Error('保存失败'); $('token').value=''; $('secret').value=''; show('配置已保存'); await load(); } catch(e) { show(e.message, true); } };
$('test').onclick = async () => { try { const r = await fetch('/api/v1/console/test-dingtalk', {method:'POST', headers:headers(true), body:JSON.stringify({content:$('text').value})}); if (!r.ok) throw Error('测试发送失败'); show('测试消息已发送'); } catch(e) { show(e.message, true); } };
load().catch(e => show(e.message, true)); setInterval(() => load().catch(e => show(e.message, true)), 30000);
</script></main></body></html>'''
