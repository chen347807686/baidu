/** 合成报表交互：服务端筛选和分页；新请求取消旧请求并校验序号，防止过期响应。
 * 会话退出清空数据，文本经 textContent 输出，凭据不进入本地存储。
 */
"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  let page = 1,
    total = 0,
    sequence = 0,
    controller = null,
    active = false;
  function clear() {
    el("report-rows").replaceChildren();
    for (const key of ["cost", "click", "leads", "lead-cost"])
      el(`metric-${key}`).textContent = "—";
    el("page-info").textContent = "尚未查询";
  }
  function note(text, error = false) {
    el("report-status").textContent = text;
    el("report-status").dataset.error = String(error);
  }
  function money(value) {
    return value == null ? "—" : `¥${value}`;
  }
  async function load() {
    if (!active) return;
    const start = el("start").value,
      end = el("end").value;
    if (!start !== !end || (start && start > end)) {
      note("请填写完整且顺序正确的日期范围。", true);
      return;
    }
    controller?.abort();
    const requestController = new AbortController();
    controller = requestController;
    const current = ++sequence;
    const timeout = setTimeout(() => requestController.abort(), 12000);
    const params = new URLSearchParams({
      page: String(page),
      page_size: el("page-size").value,
      sort: el("sort").value,
      order: el("order").value,
      search: el("search").value,
      source: el("data-source").value,
    });
    if (start) {
      params.set("start", start);
      params.set("end", end);
    }
    if (el("account").value) params.set("account", el("account").value);
    clear();
    el("source-note").textContent =
      el("data-source").value === "formal"
        ? "正式日报存储 · 显示已入库记录，尚未接入自动拉数"
        : "合成数据验证 · 不代表真实投放效果";
    el("previous").disabled = true;
    el("next").disabled = true;
    el("report-retry").hidden = true;
    el("report-panel").setAttribute("aria-busy", "true");
    note("正在查询…");
    try {
      const response = await fetch(`/api/v1/reports/keywords?${params}`, {
        credentials: "same-origin",
        cache: "no-store",
        signal: requestController.signal,
      });
      if (current !== sequence || !active) return;
      if (response.status === 401) {
        signedOut();
        message("登录已失效，请重新登录。", true);
        return;
      }
      if (!response.ok) {
        const error = new Error("查询失败");
        error.status = response.status;
        throw error;
      }
      const data = await response.json();
      if (current !== sequence || !active) return;
      total = data.total;
      el("start").value = data.applied_filters.start;
      el("end").value = data.applied_filters.end;
      el("metric-cost").textContent = money(data.summary.cost);
      el("metric-click").textContent = String(data.summary.click);
      el("metric-leads").textContent = String(data.summary.lead_total);
      el("metric-lead-cost").textContent = money(data.summary.lead_cost);
      for (const row of data.rows) {
        const tr = document.createElement("tr");
        for (const value of [
          row.keyword,
          money(row.cost),
          row.impression,
          row.click,
          row.lead_total,
          money(row.lead_cost),
          row.consult_rate === null
            ? "—"
            : `${(Number(row.consult_rate) * 100).toFixed(2)}%`,
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value);
          tr.append(td);
        }
        el("report-rows").append(tr);
      }
      const pages = Math.max(
        1,
        Math.ceil(total / Number(el("page-size").value)),
      );
      el("page-info").textContent =
        `共 ${total} 个关键词 · 第 ${page} / ${pages} 页`;
      el("previous").disabled = page <= 1;
      el("next").disabled = page >= pages;
      note(
        total
          ? "查询完成，指标为整个筛选范围汇总。"
          : data.data_source === "formal"
            ? "当前筛选范围暂无正式日报，请先完成核对并确认入库。"
            : "当前筛选范围暂无合成验证数据。",
      );
    } catch (error) {
      if (current !== sequence || !active) return;
      note(
        error.status === 403
          ? "账户权限已变化，请刷新账户范围后重试。"
          : error.status === 422
            ? "查询参数无效，请检查筛选条件。"
            : "查询失败或连接超时，请重试。",
        true,
      );
      el("report-retry").hidden = false;
    } finally {
      clearTimeout(timeout);
      if (current === sequence)
        el("report-panel").setAttribute("aria-busy", "false");
    }
  }
  document.addEventListener("session-ready", (event) => {
    active = true;
    page = 1;
    el("report-panel").hidden = false;
    const selected = el("account").value;
    el("account").replaceChildren(new Option("全部可见账户", ""));
    for (const account of event.detail.accounts)
      el("account").append(new Option(account.name, String(account.id)));
    el("account").value = [...el("account").options].some(
      (option) => option.value === selected,
    )
      ? selected
      : "";
    load();
  });
  document.addEventListener("session-ended", () => {
    active = false;
    ++sequence;
    controller?.abort();
    clear();
    el("report-panel").hidden = true;
  });
  el("report-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    page = 1;
    load();
  });
  for (const id of ["sort", "order", "page-size", "data-source"])
    el(id).addEventListener("change", () => {
      page = 1;
      load();
    });
  el("previous").addEventListener("click", () => {
    if (page > 1) {
      --page;
      load();
    }
  });
  el("next").addEventListener("click", () => {
    if (page * Number(el("page-size").value) < total) {
      ++page;
      load();
    }
  });
  el("report-retry").addEventListener("click", load);
})();
