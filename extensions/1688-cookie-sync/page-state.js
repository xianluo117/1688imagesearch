// This function is serialized by scripting.executeScript; keep it self-contained.
export function inspectPage() {
  const visible = node => Boolean(node && node.getClientRects().length);
  // Walk only accessible roots; closed shadow roots cannot provide identity evidence.
  const query = (root, selector) => {
    const matches = [...root.querySelectorAll(selector)];
    for (const node of [root, ...root.querySelectorAll('*')]) {
      if (node.shadowRoot) matches.push(...query(node.shadowRoot, selector));
    }
    return matches;
  };
  const users = query(document, 'ali-bar-user-info');
  const candidates = users.length
    ? users.flatMap(user => query(user, 'span[title]'))
    : query(document, '.nav-title > span[title]');
  const names = candidates.filter(node => visible(node) && node.getAttribute('title').trim()
    && node.getAttribute('title').trim() === node.textContent.trim());
  const nickname = names.length === 1 ? names[0].getAttribute('title').trim() : null;
  const logout = query(document, 'a[href]').some(node => {
    const url = new URL(node.href, location.href);
    return url.hostname === 'login.1688.com' && url.pathname === '/member/signout.htm';
  });
  // Candidate signals, not yet validated against a real verification page.
  const challenge = query(document, '#nc_1_wrapper, #baxia-dialog-content, iframe[src*="captcha"], iframe[src*="punish"]').some(visible);
  const riskText = /请完成安全验证|拖动滑块|访问过于频繁|请完成以下验证/.test(document.body?.innerText || '');
  const risk = challenge || riskText || /\/punish|\/captcha/.test(location.pathname);
  const loginRequired = location.hostname === 'login.1688.com';
  const loginSignal = users.length
    ? users.length === 1 && users[0].hasAttribute('islogin')
    : logout;
  const loggedIn = Boolean(nickname && loginSignal && !loginRequired && !risk);
  return {
    nickname, loggedIn, logout, risk, loginRequired,
    ready: document.readyState === 'complete',
    url: location.origin + location.pathname,
    identitySource: users.length
      ? 'DOM ali-bar-user-info[islogin] span[title] (accessible shadow roots, title/text match)'
      : 'DOM .nav-title > span[title] with signout link (title/text match)'
  };
}

export async function readPage(tabId) {
  const results = await chrome.scripting.executeScript({target: {tabId}, func: inspectPage});
  return results[0].result;
}
