export async function collectCookies(tabId) {
  const stores = await chrome.cookies.getAllCookieStores();
  const store = stores.find(item => item.tabIds.includes(tabId));
  if (!store) throw new Error('无法确定当前页面的 Cookie 存储区');
  const cookies = await chrome.cookies.getAll({domain: '1688.com', storeId: store.id});
  return {exportedAt: new Date().toISOString(), cookies};
}
