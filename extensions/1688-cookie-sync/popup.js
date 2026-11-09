const $ = id => document.getElementById(id);
const stored = await chrome.storage.local.get(['config', 'account', 'lastResult', 'lastTime']);
$('server').value = stored.config?.server || '';
$('key').value = stored.config?.key || '';
$('checkUrl').value = stored.config?.checkUrl || 'https://detail.1688.com/offer/1051566710086.html';
function render(data) {
  $('account').textContent = data.account || '未同步';
  $('result').textContent = [data.lastTime ? new Date(data.lastTime).toLocaleString() : '', data.lastResult || '尚未同步'].filter(Boolean).join('\n');
}
render(stored);
chrome.storage.onChanged.addListener(async () => render(await chrome.storage.local.get(['account', 'lastResult', 'lastTime'])));
$('config').onsubmit = async event => {
  event.preventDefault();
  try {
    const config = {server: $('server').value.trim(), key: $('key').value.trim(), checkUrl: $('checkUrl').value.trim()};
    const page = new URL(config.checkUrl);
    if (page.protocol !== 'https:' || !(page.hostname === '1688.com' || page.hostname.endsWith('.1688.com'))) throw new Error('检测页面须为 1688 HTTPS 页面');
    const allowed = await chrome.permissions.request({origins: [new URL(config.server).origin + '/*']});
    if (!allowed) throw new Error('未获得服务地址访问权限');
    await chrome.storage.local.set({config});
    const result = await chrome.runtime.sendMessage({type: 'configure'});
    if (result.error) throw new Error(result.error);
    $('result').textContent = '已保存，每 30 分钟同步';
  } catch (error) { $('result').textContent = error.message; }
};
$('sync').onclick = async () => {
  $('sync').disabled = true;
  $('result').textContent = '同步中';
  try {
    await chrome.runtime.sendMessage({type: 'sync'});
    render(await chrome.storage.local.get(['account', 'lastResult', 'lastTime']));
  } catch (error) { $('result').textContent = error.message; }
  finally { $('sync').disabled = false; }
};
