import {readPage} from './page-state.js';
import {collectCookies} from './cookies.js';

const ALARM = 'account-sync';
let running;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function post(config, path, body) {
  const response = await fetch(config.server.replace(/\/$/, '') + path, {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-API-Key': config.key},
    body: JSON.stringify(body), signal: AbortSignal.timeout(20000)
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`${response.status}: ${text}`);
  return JSON.parse(text);
}

async function notify(tabId, message) {
  // A generated PNG avoids a build-time binary asset.
  const canvas = new OffscreenCanvas(64, 64);
  const context = canvas.getContext('2d');
  context.fillStyle = '#ff7300'; context.fillRect(0, 0, 64, 64);
  context.fillStyle = 'white'; context.font = 'bold 44px sans-serif'; context.fillText('!', 23, 49);
  const bytes = new Uint8Array(await (await canvas.convertToBlob()).arrayBuffer());
  const iconUrl = 'data:image/png;base64,' + btoa(String.fromCharCode(...bytes));
  await chrome.notifications.create(String(tabId), {type: 'basic', iconUrl, title: '1688 账号同步', message});
}

async function getPage(url, key = 'pendingTab') {
  const stored = await chrome.storage.local.get(key);
  const pendingTab = stored[key];
  if (pendingTab !== undefined) {
    const tabs = await chrome.tabs.query({});
    if (tabs.some(tab => tab.id === pendingTab)) return pendingTab;
  }
  const tab = await chrome.tabs.create({url, active: false});
  await chrome.storage.local.set({[key]: tab.id});
  return tab.id;
}

async function synchronize() {
  const {config} = await chrome.storage.local.get('config');
  if (!config?.server || !config?.key) throw new Error('请先保存服务地址和上传密钥');
  const tabId = await getPage(config.checkUrl);
  const identityTab = await getPage('https://work.1688.com/home/page/index.htm?#app/accountmanagement/', 'identityTab');
  let state, identity;
  for (let i = 0; i < 30; i++) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === 'complete' && tab.url?.startsWith('https://')) {
      state = await readPage(tabId);
      const accountPage = await chrome.tabs.get(identityTab);
      if (accountPage.status === 'complete') identity = await readPage(identityTab);
      if (state.risk || state.loginRequired || identity?.risk || identity?.loginRequired
        || (state.ready && identity?.ready && identity.loggedIn)) break;
    }
    await pause(500);
  }
  if (state?.risk || identity?.risk) {
    await notify(state?.risk ? tabId : identityTab, '检测到验证页面，请点击完成验证后重新同步');
    if (identity?.nickname) await post(config, '/api/v1/accounts/risk', {account_id: identity.nickname});
    throw new Error('页面存在风控信号，未上传，已保留页面');
  }
  if (!identity?.nickname) {
    await notify(identityTab, '未识别到唯一昵称，请检查账号页面');
    throw new Error('昵称缺失或不唯一，未上传，已保留页面');
  }
  if (!identity.loggedIn || identity.loginRequired || !identity.ready || !state?.ready || state.loginRequired) {
    await notify(identity.loginRequired || !identity.loggedIn ? identityTab : tabId, '登录未确认，请检查页面登录状态');
    throw new Error('登录未确认，未上传，已保留页面');
  }
  const payload = await collectCookies(tabId);
  const verified = await readPage(identityTab);
  const checked = await readPage(tabId);
  if (verified.risk || checked.risk || verified.loginRequired || checked.loginRequired
    || !verified.ready || !checked.ready || !verified.loggedIn || verified.nickname !== identity.nickname) {
    throw new Error('采集期间页面状态改变，未上传');
  }
  await post(config, '/api/v1/accounts/sync', {
    account_id: identity.nickname, display_name: identity.nickname,
    identity_source: identity.identitySource, login_confirmed: true, cookie_payload: payload
  });
  await chrome.storage.local.set({account: identity.nickname, lastResult: '同步成功', lastTime: Date.now()});
  await chrome.tabs.remove([tabId, identityTab]);
  await chrome.storage.local.remove(['pendingTab', 'identityTab']);
}

function sync() {
  if (!running) running = synchronize().catch(async error => {
    await chrome.storage.local.set({lastResult: error.message, lastTime: Date.now()});
    return {error: error.message};
  }).finally(() => { running = null; });
  return running;
}

async function ensureAlarm() {
  const {config} = await chrome.storage.local.get('config');
  if (config?.server && config?.key && !await chrome.alarms.get(ALARM)) {
    await chrome.alarms.create(ALARM, {periodInMinutes: 30, delayInMinutes: 30});
  }
}
chrome.runtime.onInstalled.addListener(ensureAlarm);
chrome.runtime.onStartup.addListener(ensureAlarm);
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) sync(); });
chrome.notifications.onClicked.addListener(async id => {
  const tab = await chrome.tabs.get(Number(id));
  await chrome.windows.update(tab.windowId, {focused: true});
  await chrome.tabs.update(tab.id, {active: true});
});
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  const action = message.type === 'sync' ? sync() : ensureAlarm();
  action.then(result => reply(result || {ok: true})).catch(error => reply({error: error.message}));
  return true;
});
