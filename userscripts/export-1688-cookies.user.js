// ==UserScript==
// @name         1688 Cookie 远程同步器
// @namespace    local.1688-image-search
// @version      2.0.0
// @description  将 1688 完整 Cookie 加密上传到远程图片搜索 API
// @match        https://*.1688.com/*
// @noframes
// @grant        GM_registerMenuCommand
// @grant        GM_cookie
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      1688.com
// @connect      *.1688.com
// @connect      *
// ==/UserScript==

(function () {
  "use strict";

  if (window.top !== window.self) return;
  if (window.__1688CookieExporterLoaded) return;
  window.__1688CookieExporterLoaded = true;

  function parseVisibleCookies() {
    return document.cookie
      .split(";")
      .map((part) => part.trim())
      .filter(Boolean)
      .map((part) => {
        const separator = part.indexOf("=");
        const name = separator >= 0 ? part.slice(0, separator) : part;
        const value = separator >= 0 ? part.slice(separator + 1) : "";
        return {
          name,
          value,
          domain: ".1688.com",
          path: "/",
          expires: null,
          secure: true,
          httpOnly: false,
          sameSite: "None",
        };
      });
  }

  function normalizeCookie(cookie) {
    return {
      name: cookie.name,
      value: cookie.value,
      domain: cookie.domain || ".1688.com",
      path: cookie.path || "/",
      expires: cookie.expirationDate ?? cookie.expires ?? null,
      secure: Boolean(cookie.secure),
      httpOnly: Boolean(cookie.httpOnly),
      sameSite: cookie.sameSite || "None",
    };
  }

  function listCookies(details) {
    return new Promise((resolve) => {
      GM_cookie.list(details, (cookies, error) => {
        resolve({
          details,
          cookies: cookies || [],
          error: error ? String(error) : "",
        });
      });
    });
  }

  async function listExtensionCookies() {
    if (
      typeof GM_cookie === "undefined" ||
      typeof GM_cookie.list !== "function"
    ) {
      throw new Error("当前脚本管理器不支持 GM_cookie.list");
    }

    const queries = [
      {},
      { domain: ".1688.com" },
      { domain: "1688.com" },
      { url: location.href },
      { url: "https://www.1688.com/" },
      { url: "https://air.1688.com/" },
      { url: "https://h5api.m.1688.com/" },
    ];
    const batches = await Promise.all(queries.map(listCookies));
    const merged = new Map();
    for (const batch of batches) {
      for (const cookie of batch.cookies) {
        const domain = String(cookie.domain || "").replace(/^\./, "");
        if (domain !== "1688.com" && !domain.endsWith(".1688.com")) continue;
        const key = `${cookie.name}|${cookie.domain || ""}|${cookie.path || "/"}`;
        merged.set(key, normalizeCookie(cookie));
      }
    }
    return {
      cookies: Array.from(merged.values()),
      diagnostics: batches.map((batch) => ({
        query: batch.details,
        count: batch.cookies.length,
        error: batch.error,
      })),
    };
  }

  function downloadJson(filename, payload) {
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  function validateCookies(cookies) {
    const names = new Set(cookies.map((cookie) => cookie.name));
    const missing = [];
    if (!names.has("_m_h5_tk")) missing.push("_m_h5_tk");
    if (!names.has("_m_h5_tk_enc")) missing.push("_m_h5_tk_enc");
    if (!names.has("cookie1") && !names.has("cookie2"))
      missing.push("cookie1/cookie2");
    return { names, missing };
  }

  async function collectCookiePayload() {
    let cookies;
    let mode;
    try {
      const result = await listExtensionCookies();
      cookies = result.cookies;
      mode = "GM_cookie";
      console.table(result.diagnostics);
    } catch (error) {
      cookies = parseVisibleCookies();
      mode = "document.cookie-fallback";
      console.warn(
        "完整 Cookie API 不可用或权限未授权，已回退到 document.cookie",
        error,
      );
    }

    const { names, missing } = validateCookies(cookies);
    if (missing.length) {
      window.alert(
        `导出结果缺少 ${missing.join(", ")}。\n` +
          `当前模式：${mode}\n` +
          "请打开开发者工具 Console 查看 GM_cookie 查询统计，并在 Tampermonkey 扩展详情中将网站访问权限设为“在所有网站上”。",
      );
      return;
    }

    return {
      exportedAt: new Date().toISOString(),
      source: location.origin,
      mode,
      cookieCount: cookies.length,
      hasHttpOnly: cookies.some((cookie) => cookie.httpOnly),
      hasLoginCookie: names.has("cookie1") || names.has("cookie2"),
      cookies,
    };
  }

  function normalizeApiBase(value) {
    return String(value || "")
      .trim()
      .replace(/\/+$/, "");
  }

  function validateApiBase(value) {
    try {
      const parsed = new URL(value);
      if (parsed.protocol === "https:") return true;
      return (
        parsed.protocol === "http:" &&
        ["127.0.0.1", "localhost"].includes(parsed.hostname)
      );
    } catch (_error) {
      return false;
    }
  }

  function configureRemoteApi() {
    const currentBase = GM_getValue("apiBase", "");
    const apiBase = normalizeApiBase(
      window.prompt(
        "API 服务地址，例如 https://search.example.com",
        currentBase,
      ),
    );
    if (!validateApiBase(apiBase)) {
      window.alert("服务地址必须使用 HTTPS；仅本机调试允许 localhost HTTP。");
      return;
    }
    const currentKey = GM_getValue("uploadApiKey", "");
    const apiKey = String(
      window.prompt("COOKIE_UPLOAD_API_KEY", currentKey) || "",
    ).trim();
    if (apiKey.length < 24) {
      window.alert("上传 API Key 长度不足 24 个字符。");
      return;
    }
    GM_setValue("apiBase", apiBase);
    GM_setValue("uploadApiKey", apiKey);
    window.alert(
      "远程 Cookie API 配置已保存。密钥仅保存在 Tampermonkey 存储中。",
    );
  }

  function postCookiePayload(apiBase, apiKey, payload) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: "POST",
        url: `${apiBase}/api/v1/cookies`,
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": apiKey,
        },
        data: JSON.stringify(payload),
        timeout: 30000,
        onload(response) {
          let body = null;
          try {
            body = JSON.parse(response.responseText || "{}");
          } catch (_error) {
            body = null;
          }
          if (response.status >= 200 && response.status < 300) {
            resolve(body || {});
            return;
          }
          reject(
            new Error(
              body?.detail?.message ||
                `Cookie API 返回 HTTP ${response.status}`,
            ),
          );
        },
        ontimeout() {
          reject(new Error("Cookie API 请求超时"));
        },
        onerror() {
          reject(new Error("Cookie API 网络请求失败"));
        },
      });
    });
  }

  async function uploadCookies() {
    const apiBase = normalizeApiBase(GM_getValue("apiBase", ""));
    const apiKey = String(GM_getValue("uploadApiKey", "")).trim();
    if (!validateApiBase(apiBase) || apiKey.length < 24) {
      window.alert("请先执行“配置远程 Cookie API”。");
      return;
    }
    const payload = await collectCookiePayload();
    if (!payload) return;
    try {
      const result = await postCookiePayload(apiBase, apiKey, payload);
      window.alert(
        `Cookie 上传成功。版本：${result.version ?? "-"}，数量：${result.cookie_count ?? payload.cookieCount}`,
      );
    } catch (error) {
      window.alert(`Cookie 上传失败：${error.message || error}`);
    }
  }

  async function exportCookies() {
    const payload = await collectCookiePayload();
    if (!payload) return;
    const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadJson(`1688-cookies-${timestamp}.json`, payload);
    window.alert("完整 Cookie JSON 已下载。文件包含账号登录凭据，请安全保存。");
  }

  GM_registerMenuCommand("配置远程 Cookie API", configureRemoteApi);
  GM_registerMenuCommand("上传完整 Cookie 到 API", uploadCookies);
  GM_registerMenuCommand("下载完整 Cookie JSON 备份", exportCookies);
})();
