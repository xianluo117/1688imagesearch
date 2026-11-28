// Paste once in DevTools Console on an HTTPS detail.1688.com page.
(async () => {
  if (location.origin !== 'https://detail.1688.com') {
    console.error('请在已登录的 1688 商品详情页运行。');
    return;
  }
  const cookies = document.cookie.split(';').flatMap(part => {
    const pair = part.trim();
    const i = pair.indexOf('=');
    return i > 0 ? [{name: pair.slice(0, i), value: pair.slice(i + 1)}] : [];
  });
  const names = new Set(cookies.filter(cookie => cookie.value).map(cookie => cookie.name));
  const missing = [['cookie2', 'cookie1'], ['_m_h5_tk'], ['_m_h5_tk_enc']]
    .filter(group => !group.some(name => names.has(name))).map(group => group.join(' / '));
  if (missing.length) {
    console.warn('未上传：当前页面缺少服务端必需的可读 Cookie：' + missing.join('，'));
    return;
  }
  const address = prompt('请输入你自己管理的独立 HTTPS 测试服务地址。取消则不上传。');
  if (!address) return;
  let endpoint;
  try {
    const service = new URL(address.trim());
    if (service.protocol !== 'https:' || service.username || service.password || service.origin === location.origin) throw new Error();
    endpoint = new URL('/api/v1/cookies', service.origin);
    endpoint.searchParams.set('account_id', 'console-test-' + crypto.randomUUID());
  } catch {
    console.error('未上传：请输入有效的独立 HTTPS 测试服务地址。');
    return;
  }
  const key = prompt(`目标：${endpoint.origin}\n仅使用独立测试服务的 Cookie 上传 Key，不要输入生产密钥。`);
  if (!key?.trim()) return;
  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-API-Key': key.trim()},
      credentials: 'omit',
      redirect: 'error',
      body: JSON.stringify({exportedAt: new Date().toISOString(), cookies}),
      signal: AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      console.error(`上传失败：HTTP ${response.status}。`);
      return;
    }
    const result = await response.json();
    const account = endpoint.searchParams.get('account_id');
    if (result.status !== 'updated' || result.account_id !== account) {
      console.error('服务响应不符合预期，不能确认上传成功。');
      return;
    }
    console.info(`服务端已保存。测试账号：${account}。请指定该账号跑一次 SKU 查询，确认登录态是否有效。`);
  } catch {
    console.error('未确认上传成功。请检查网络、服务端跨域设置或浏览器 CSP 提示；不要关闭浏览器安全设置。');
  }
})();
