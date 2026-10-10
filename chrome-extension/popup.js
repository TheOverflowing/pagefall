"use strict";

const HOST = "local.novelist.connect";
const MAX_COOKIES = 256;
const MAX_BYTES = 1024 * 1024;
const connectButton = document.getElementById("connect");
const status = document.getElementById("status");
class ConnectionError extends Error {}

function nativeConnectionError(error) {
  // Only map Chrome's fixed transport errors. Never display arbitrary exception
  // text, which could later contain submitted authentication data.
  const message = error instanceof Error ? error.message : "";
  if (message === "Specified native messaging host not found.") {
    return "Chrome 未找到本机连接器。请回到拾页重新安装连接器，再重试。";
  }
  if (message === "Access to the specified native messaging host is forbidden.") {
    return "Chrome 未允许此扩展连接拾页。请重新安装连接器，并确认使用拾页提供的扩展。";
  }
  if (message === "Failed to start native messaging host.") {
    return "Chrome 无法启动连接器。请检查拾页项目位置和 macOS 文件访问权限，再重试。";
  }
  if (message === "Native host has exited.") {
    return "本机连接器启动后退出。请回到拾页重新安装连接器，再重试。";
  }
  if (message === "Error when communicating with the native messaging host.") {
    return "Chrome 与本机连接器通信失败。请更新并重新打开拾页，再重试。";
  }
  return "未连接到拾页。请先按安装说明配置本机连接器，再重试。";
}

function isJinjiangDomain(value) {
  if (typeof value !== "string" || value.length > 253) return false;
  const domain = value.toLowerCase().replace(/^\./, "");
  return /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*jjwxc\.(?:net|com)$/.test(domain);
}

function validateCookies(cookies) {
  if (!Array.isArray(cookies) || cookies.length === 0) throw new ConnectionError("尚未找到晋江登录，请先在 Chrome 登录晋江。");
  if (cookies.length > MAX_COOKIES) throw new ConnectionError("晋江 Cookie 数量异常，连接未发送。");
  for (const cookie of cookies) {
    if (!cookie || !isJinjiangDomain(cookie.domain)
      || typeof cookie.name !== "string" || !cookie.name || cookie.name.length > 1024
      || typeof cookie.value !== "string" || cookie.value.length > 65536
      || typeof cookie.path !== "string" || !cookie.path.startsWith("/") || cookie.path.length > 4096
      || typeof cookie.secure !== "boolean" || typeof cookie.httpOnly !== "boolean"
      || typeof cookie.session !== "boolean"
      || !["no_restriction", "lax", "strict", "unspecified"].includes(cookie.sameSite)
      || (cookie.expirationDate !== undefined && (!Number.isFinite(cookie.expirationDate) || cookie.expirationDate < 0))) {
      throw new ConnectionError("晋江登录数据格式异常，连接未发送。");
    }
  }
  return cookies;
}

connectButton.addEventListener("click", async () => {
  connectButton.disabled = true;
  status.textContent = "正在连接本机拾页…";
  try {
    const groups = await Promise.all([
      chrome.cookies.getAll({ domain: "jjwxc.net" }),
      chrome.cookies.getAll({ domain: "jjwxc.com" }),
    ]);
    // A partitioned cookie belongs to a distinct top-level site context. Do not
    // merge that authentication boundary into the first-party reader session.
    const cookies = validateCookies(groups.flat().filter(cookie => cookie.partitionKey == null));
    const message = { action: "connect", cookies };
    if (new TextEncoder().encode(JSON.stringify(message)).length > MAX_BYTES) {
      throw new ConnectionError("晋江登录数据过大，连接未发送。");
    }
    let response;
    try {
      response = await chrome.runtime.sendNativeMessage(HOST, message);
    } catch (error) {
      throw new ConnectionError(nativeConnectionError(error));
    }
    if (!response || typeof response.ok !== "boolean" || typeof response.message !== "string") {
      throw new ConnectionError("拾页没有返回有效结果，请重试。");
    }
    status.textContent = response.message;
    if (response.ok) connectButton.textContent = "重新连接";
  } catch (error) {
    status.textContent = error instanceof ConnectionError ? error.message : "无法读取晋江登录，请检查扩展权限后重试。";
  } finally {
    connectButton.disabled = false;
  }
});
