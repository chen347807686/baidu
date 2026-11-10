/** 登录链路交互。固定同源路径发送请求；密码不持久化，令牌由 HttpOnly Cookie 管理。
 * 串行执行操作防止重复提交；账户名称用 textContent 输出，禁止拼接 HTML。
 */
"use strict";
const form = document.querySelector("#login-form"),
  password = document.querySelector("#password"),
  status = document.querySelector("#status"),
  retry = document.querySelector("#retry"),
  loginPanel = document.querySelector("#login-panel"),
  sessionPanel = document.querySelector("#session-panel");
let busy = false;
function message(text, error = false) {
  status.textContent = text;
  status.dataset.error = String(error);
}
function signedOut() {
  loginPanel.hidden = false;
  sessionPanel.hidden = true;
  document.querySelector("#accounts").replaceChildren();
  document.querySelector("#account-count").textContent = "";
}
// 浏览器会自动为同源 POST 提供 Origin；自定义头用于服务器的跨站请求校验。
async function request(path, body) {
  const response = await fetch(`/api/v1/auth/${path}`, {
    method: body === undefined ? "GET" : "POST",
    credentials: "same-origin",
    cache: "no-store",
    signal: AbortSignal.timeout(12000),
    headers:
      body === undefined
        ? {}
        : { "Content-Type": "application/json", "X-Onetitle-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = new Error("请求失败");
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}
async function loadSession() {
  try {
    const user = await request("me");
    const list = document.querySelector("#accounts");
    list.replaceChildren();
    for (const account of user.accounts) {
      const li = document.createElement("li");
      li.textContent = account.name;
      list.append(li);
    }
    document.querySelector("#account-count").textContent =
      `可见账户 · ${user.accounts.length}`;
    loginPanel.hidden = true;
    sessionPanel.hidden = false;
    message(
      user.accounts.length
        ? "账户范围已更新。"
        : "暂无可见账户，请联系管理员分配。",
    );
  } catch (error) {
    if (error.status !== 401) throw error;
    signedOut();
    message("请登录后继续。");
  }
}
// 网络错误可能发生在服务器已执行之后；重连先读取实际会话，不自动重发登录。
async function perform(action) {
  if (busy) return;
  busy = true;
  retry.hidden = true;
  document.querySelectorAll("button").forEach((b) => (b.disabled = true));
  form.setAttribute("aria-busy", "true");
  try {
    await action();
  } catch (error) {
    if (error.status === 401) {
      signedOut();
      message("账号或密码错误，或账号已停用。", true);
    } else if (error.status === 429) {
      message("登录尝试过多，请稍后重试。", true);
    } else if (error.status === 403) {
      message("请求来源校验失败，请从本地服务地址重新打开页面。", true);
    } else {
      message("暂时无法连接服务，请检查连接后重试。", true);
      retry.hidden = false;
    }
  } finally {
    busy = false;
    document.querySelectorAll("button").forEach((b) => (b.disabled = false));
    form.setAttribute("aria-busy", "false");
  }
}
form.addEventListener("submit", (event) => {
  event.preventDefault();
  perform(async () => {
    message("正在登录…");
    try {
      await request("login", {
        username: document.querySelector("#username").value,
        password: password.value,
      });
    } finally {
      password.value = "";
    }
    await loadSession();
  });
});
document.querySelector("#logout").addEventListener("click", () =>
  perform(async () => {
    await request("logout", {});
    signedOut();
    message("已安全退出。");
    document.querySelector("#username").focus();
  }),
);
document
  .querySelector("#refresh")
  .addEventListener("click", () => perform(loadSession));
retry.addEventListener("click", () => perform(loadSession));
perform(loadSession);
