/** 正式/合成报表交互：服务端筛选和分页；请求取消与序号保护防止过期响应。
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
  let dailyController = null,
    dailySequence = 0,
    dailyContext = null;
  let previewMode = false;
  let activeView = "original";
  const demoExpansionState = new Map();
  const negativeDialog = el("negative-dialog");
  const expansionDialog = el("expansion-dialog");
  const searchTermDialog = el("search-term-dialog");
  let negativeWord = "";
  let expansionWord = "";
  const demoSearchTerms = {
    "黄金回收": ["哪里可以高价回收黄金", "黄金回收上门", "今日黄金回收价格"],
    "黄金首饰回收": ["旧金首饰回收价格", "金手镯回收多少钱"],
    "竞品甲官网": ["竞品甲官网电话"],
  };
  function openSearchTerms(keyword, searchTerm) {
    el("search-term-keyword").textContent = `匹配关键词：${keyword}`;
    const terms = demoSearchTerms[keyword] || (searchTerm ? [searchTerm] : []);
    el("search-term-rows").replaceChildren();
    for (const term of terms) {
      const tr = document.createElement("tr");
      for (const value of [term, keyword, "演示/搜索词来源"]) {
        const td = document.createElement("td"); td.textContent = value; tr.append(td);
      }
      el("search-term-rows").append(tr);
    }
    el("search-term-status").textContent = terms.length ? `共 ${terms.length} 个用户搜索词匹配。` : "暂无已接入的用户搜索词。";
    searchTermDialog.showModal();
  }
  el("search-term-close").addEventListener("click", () => searchTermDialog.close());
  function lines(value) {
    return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
  }
  function openNegative(word, filters) {
    negativeWord = word;
    el("negative-word").textContent = `关键词：${word}`;
    const selected = filters.account == null ? "" : String(filters.account);
    el("negative-account").value = selected;
    el("negative-result").textContent = "请输入规则后预览；本操作不会保存标注。";
    negativeDialog.showModal();
  }
  function closeNegative() {
    if (negativeDialog.open) negativeDialog.close();
  }
  function parseCategories(value) {
    const output = {};
    for (const line of lines(value)) {
      const index = line.indexOf("=");
      if (index <= 0) continue;
      const name = line.slice(0, index).trim();
      const values = line.slice(index + 1).split(",").map((item) => item.trim()).filter(Boolean);
      if (name && values.length) output[name] = values;
    }
    return output;
  }
  async function previewNegative(event) {
    event.preventDefault();
    const account = Number(el("negative-account").value);
    if (!account || !negativeWord) {
      el("negative-result").textContent = "请选择明确账户后再预览。";
      return;
    }
    el("negative-result").textContent = "正在判断…";
    const body = {
      account_id: account,
      word: negativeWord,
      custom_negatives: lines(el("negative-custom").value),
      categories: parseCategories(el("negative-categories").value),
      competitor_brands: lines(el("negative-brands").value),
      non_target_regions: lines(el("negative-regions").value),
      intent_words: lines(el("negative-intents").value),
    };
    try {
      const response = await fetch("/api/v1/keywords/negative-preview", {
        method: "POST",
        credentials: "same-origin",
        cache: "no-store",
        headers: { "Content-Type": "application/json", "X-OneTitle-Request": "1" },
        body: JSON.stringify(body),
      });
      if (response.status === 401) {
        closeNegative();
        signedOut();
        message("登录已失效，请重新登录。", true);
        return;
      }
      if (!response.ok) throw Object.assign(new Error(), { status: response.status });
      const data = await response.json();
      el("negative-result").textContent = data.matched
        ? `建议标注否词 · ${data.category} · ${data.reason}`
        : data.reason || "未命中否词规则，可继续人工复核。";
    } catch (error) {
      el("negative-result").textContent = error.status === 403
        ? "账户不可见，请刷新账户范围。"
        : "预览失败，请检查规则格式后重试。";
    }
  }
  el("negative-close").addEventListener("click", closeNegative);
  el("negative-dialog").addEventListener("cancel", closeNegative);
  el("negative-form").addEventListener("submit", previewNegative);
  function openExpansion(word, filters) {
    expansionWord = word;
    el("expansion-seed").textContent = `原关键词：${word}`;
    el("expansion-account").value = filters.account == null ? "" : String(filters.account);
    el("expansion-rows").replaceChildren();
    el("expansion-result").textContent = "当前为本地模拟预览，不会保存任务。";
    expansionDialog.showModal();
  }
  async function previewExpansion(event) {
    event.preventDefault();
    const account = Number(el("expansion-account").value);
    if (!account || !expansionWord) {
      el("expansion-result").textContent = "请选择明确账户后再预览。";
      return;
    }
    el("expansion-result").textContent = "正在生成…";
    try {
      const response = await fetch("/api/v1/keywords/expansion-preview", {
        method: "POST", credentials: "same-origin", cache: "no-store",
        headers: { "Content-Type": "application/json", "X-OneTitle-Request": "1" },
        body: JSON.stringify({ account_id: account, seed_id: `preview-${Date.now()}`, keyword: expansionWord, quality: el("expansion-quality").value, negative_status: el("expansion-negative").value }),
      });
      if (response.status === 401) { expansionDialog.close(); signedOut(); message("登录已失效，请重新登录。", true); return; }
      if (!response.ok) { const error = new Error(); error.status = response.status; throw error; }
      const data = await response.json();
      for (const row of data.candidates) {
        const tr = document.createElement("tr");
        for (const value of [row.keyword, row.source_keyword, row.negative_matched ? `建议否词：${row.negative_reason}` : "未命中规则"]) {
          const td = document.createElement("td"); td.textContent = value; tr.append(td);
        }
        el("expansion-rows").append(tr);
      }
      el("expansion-result").textContent = `生成 ${data.candidates.length} 个候选，均保留原关键词来源。`;
    } catch (error) { el("expansion-result").textContent = error.status === 403 ? "账户不可见。" : "拓词预览失败，请重试。"; }
  }
  el("expansion-close").addEventListener("click", () => expansionDialog.close());
  el("expansion-form").addEventListener("submit", previewExpansion);
  const dailyDialog = el("daily-dialog");
  function resetDaily() {
    ++dailySequence;
    dailyController?.abort();
    el("daily-rows").replaceChildren();
    el("daily-retry").hidden = true;
    dailyDialog.setAttribute("aria-busy", "false");
  }
  function closeDaily() {
    resetDaily();
    dailyContext = null;
    if (dailyDialog.open) dailyDialog.close();
  }
  async function loadDaily() {
    if (!active || !dailyContext || !dailyDialog.open) return;
    resetDaily();
    const current = dailySequence;
    const requestController = new AbortController();
    dailyController = requestController;
    const timeout = setTimeout(() => requestController.abort(), 12000);
    el("daily-status").textContent = "正在加载每日表现…";
    dailyDialog.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(
        `/api/v1/reports/keywords/daily?${new URLSearchParams(dailyContext)}`,
        {
          credentials: "same-origin",
          cache: "no-store",
          signal: requestController.signal,
        },
      );
      if (current !== dailySequence || !active) return;
      if (response.status === 401) {
        signedOut();
        message("登录已失效，请重新登录。", true);
        return;
      }
      if (!response.ok) {
        const error = new Error();
        error.status = response.status;
        throw error;
      }
      const data = await response.json();
      if (current !== dailySequence || !active) return;
      for (const row of data.rows) {
        const tr = document.createElement("tr");
        for (const value of [
          row.date,
          money(row.cost),
          row.impression,
          row.click,
          row.lead_total,
          money(row.lead_cost),
        ]) {
          const td = document.createElement("td");
          td.textContent = String(value);
          tr.append(td);
        }
        el("daily-rows").append(tr);
      }
      el("daily-status").textContent = data.rows.length
        ? `共 ${data.rows.length} 个有记录日期。`
        : "该关键词在当前范围暂无正式日报。";
    } catch (error) {
      if (current !== dailySequence || !active) return;
      el("daily-status").textContent =
        error.status === 403
          ? "账户权限已变化，请关闭并刷新账户范围。"
          : error.status === 422
            ? "每日表现最多查询 366 天，请缩小日期范围后重试。"
            : "加载失败或连接超时，请重试。";
      el("daily-retry").hidden = [403, 422].includes(error.status);
    } finally {
      clearTimeout(timeout);
      if (current === dailySequence)
        dailyDialog.setAttribute("aria-busy", "false");
    }
  }
  function openDaily(keyword, filters) {
    dailyContext = { keyword, start: filters.start, end: filters.end };
    if (filters.account !== null)
      dailyContext.account = String(filters.account);
    el("daily-keyword").textContent = keyword;
    el("daily-range").textContent =
      `${filters.start} 至 ${filters.end} · ${filters.account === null ? "全部可见账户" : "当前所选账户"}`;
    dailyDialog.showModal();
    loadDaily();
  }
  el("daily-close").addEventListener("click", closeDaily);
  dailyDialog.addEventListener("cancel", closeDaily);
  dailyDialog.addEventListener("close", () => {
    resetDaily();
    dailyContext = null;
  });
  el("daily-retry").addEventListener("click", loadDaily);
  function clear() {
    closeNegative();
    if (expansionDialog.open) expansionDialog.close();
    closeDaily();
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
  function statusCell(value) {
    const td = document.createElement("td");
    td.textContent = String(value ?? "—");
    if (["优质", "非否词", "已采纳"].includes(String(value))) td.className = "status-good";
    if (["建议否词", "待判断", "未判定", "待审核"].includes(String(value))) td.className = "status-warn";
    if (["普通", "已忽略"].includes(String(value))) td.className = "status-muted";
    return td;
  }
  function loadDemo() {
    clear();
    el("report-panel").hidden = false;
    el("account").value = "1";
    el("start").value = "2026-11-01";
    el("end").value = "2026-11-11";
    const rows = [
      { keyword: "黄金回收", search_term: "哪里可以高价回收黄金", cost: "680.00", impression: 12400, click: 86, lead_total: 8, lead_cost: "85.00", consult_rate: "0.1395", quality: "优质", negative: "非否词" },
      { keyword: "黄金首饰回收", search_term: "旧金首饰回收价格", cost: "420.00", impression: 8100, click: 52, lead_total: 3, lead_cost: "140.00", consult_rate: "0.1154", quality: "普通", negative: "未判定" },
      { keyword: "竞品甲官网", search_term: "竞品甲官网电话", cost: "96.00", impression: 2300, click: 18, lead_total: 0, lead_cost: null, consult_rate: "0.0000", quality: "待判断", negative: "建议否词" },
    ];
    const qualityFilter = el("quality-filter").value;
    const searchText = el("search").value.trim().toLowerCase();
    const qualityLabels = { high: "优质", ordinary: "普通", pending: "待判断" };
    const expansionRows = [
      { keyword: "高价回收黄金", source_keyword: "黄金回收", search_term: "哪里可以高价回收黄金", cost: "—", impression: "—", click: "—", lead_total: "—", lead_cost: null, consult_rate: null, quality: "待判断", negative: "未判定", review: demoExpansionState.get("高价回收黄金") || "待审核" },
      { keyword: "黄金上门回收", source_keyword: "黄金回收", search_term: "黄金回收上门", cost: "—", impression: "—", click: "—", lead_total: "—", lead_cost: null, consult_rate: null, quality: "待判断", negative: "未判定", review: demoExpansionState.get("黄金上门回收") || "待审核" },
    ];
    configureView();
    const baseRows = activeView === "expansions" ? expansionRows : rows;
    const visibleRows = baseRows.filter((row) => {
      const qualityMatch = !qualityFilter || row.quality === qualityLabels[qualityFilter];
      const searchMatch = !searchText || `${row.keyword} ${row.search_term || ""} ${row.source_keyword || ""}`.toLowerCase().includes(searchText);
      return qualityMatch && searchMatch;
    });
    total = visibleRows.length;
    el("previous").disabled = true;
    el("next").disabled = true;
    el("metric-cost").textContent = "¥1,196.00";
    el("metric-click").textContent = "156";
    el("metric-leads").textContent = "11";
    el("metric-lead-cost").textContent = "¥108.73";
    const renderedKeys = new Set();
    for (const row of visibleRows) {
      const renderKey = `${activeView}|${row.keyword}|${row.source_keyword || ""}|${row.search_term || ""}`;
      if (renderedKeys.has(renderKey)) continue;
      renderedKeys.add(renderKey);
      const tr = document.createElement("tr");
      const values = activeView === "search-terms"
        ? [row.search_term || "—", row.keyword, "搜索词来源", row.quality || "待判断", row.negative || "未判定"]
        : activeView === "expansions"
          ? [row.keyword, row.source_keyword, row.search_term || "—", row.negative || "未判定", row.review || "待审核"]
          : [row.keyword, row.search_term || "—", money(row.cost), row.impression, row.click, row.lead_total, money(row.lead_cost), row.consult_rate == null ? "—" : `${(Number(row.consult_rate) * 100).toFixed(2)}%`, row.quality, row.negative || "未判定"];
      for (const value of values) {
        const td = statusCell(value);
        if (activeView === "search-terms" && tr.children.length === 0) {
          td.replaceChildren();
          const button = document.createElement("button"); button.type = "button"; button.className = "keyword-detail"; button.textContent = value; button.addEventListener("click", () => openSearchTerms(row.keyword, row.search_term)); td.append(button);
        } else if (activeView === "expansions" && tr.children.length === 0) {
          td.replaceChildren();
          const button = document.createElement("button"); button.type = "button"; button.className = "keyword-detail"; button.textContent = value; button.addEventListener("click", () => openExpansion(row.source_keyword, { account: 1 })); td.append(button);
        } else if (tr.children.length === 0) {
          td.replaceChildren();
          const button = document.createElement("button"); button.type = "button"; button.className = "keyword-detail"; button.textContent = value; button.addEventListener("click", () => openNegative(row.keyword, { account: 1 })); td.append(button);
        } else if (activeView === "original" && tr.children.length === 1) {
          td.replaceChildren();
          const button = document.createElement("button"); button.type = "button"; button.className = "keyword-detail"; button.textContent = value; button.addEventListener("click", () => openSearchTerms(row.keyword, row.search_term)); td.append(button);
        } else if (!td.className) td.textContent = String(value);
        tr.append(td);
      }
      const action = document.createElement("td");
      const actions = activeView === "expansions"
        ? [["查看原词", () => openSearchTerms(row.source_keyword, row.search_term)], ["采纳", () => updateExpansion(row.keyword, "已采纳")], ["忽略", () => updateExpansion(row.keyword, "已忽略")], ["标为否词", () => updateExpansion(row.keyword, "建议否词")]]
        : activeView === "search-terms" ? [["查看关键词", () => openNegative(row.keyword, { account: 1 })]]
          : [["否词预览", () => openNegative(row.keyword, { account: 1 })], ["拓词预览", () => openExpansion(row.keyword, { account: 1 })]];
      for (const [label, handler] of actions) {
        const button = document.createElement("button"); button.type = "button"; button.className = "secondary keyword-action"; button.textContent = label; button.addEventListener("click", handler); button.disabled = label === "拓词预览" && row.quality !== "优质"; button.title = button.disabled ? "只有优质词可以拓词" : ""; action.append(button);
      }
      tr.append(action); el("report-rows").append(tr);
    }
    el("page-info").textContent = `演示数据 · ${visibleRows.length} 个关键词`;
    if (!visibleRows.length) {
      const empty = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = activeView === "original" ? 11 : activeView === "search-terms" ? 6 : 6;
      cell.className = "empty-cell";
      cell.textContent = "当前筛选没有匹配结果，请调整关键词或质量条件。";
      empty.append(cell); el("report-rows").append(empty);
    }
    note(activeView === "expansions" ? "拓词结果视图：候选词保留原关键词来源，真实指标待投放后回流。" : qualityFilter || searchText ? `已应用筛选：${qualityFilter ? qualityLabels[qualityFilter] : "全部质量"}${searchText ? ` · 搜索“${searchText}”` : ""}。` : "本地演示数据已加载，可直接查看否词和拓词交互。", false);
  }
  function updateExpansion(keyword, status) {
    demoExpansionState.set(keyword, status);
    loadDemo();
    note(`已将“${keyword}”标记为${status}。演示模式仅更新页面状态。`, false);
  }
  function configureView() {
    const headers = activeView === "search-terms"
      ? ["用户搜索词", "匹配关键词", "来源", "质量", "否词状态", "操作"]
      : activeView === "expansions"
        ? ["拓展词", "原关键词", "用户搜索词来源", "否词状态", "审核状态", "操作"]
        : ["关键词", "用户搜索词", "消耗", "展现", "点击", "综合线索", "线索成本", "咨询率", "质量", "否词状态", "操作"];
    const head = document.querySelector("#report-panel thead tr");
    head.replaceChildren();
    for (const label of headers) { const th = document.createElement("th"); th.scope = "col"; th.textContent = label; head.append(th); }
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
      const renderedKeys = new Set();
      for (const row of data.rows) {
        const renderKey = `${activeView}|${row.keyword}|${row.search_term || ""}`;
        if (renderedKeys.has(renderKey)) continue;
        renderedKeys.add(renderKey);
        const tr = document.createElement("tr");
        for (const value of [
          row.keyword,
          row.search_term || "未接入搜索词明细",
          money(row.cost),
          row.impression,
          row.click,
          row.lead_total,
          money(row.lead_cost),
          row.consult_rate === null
            ? "—"
            : `${(Number(row.consult_rate) * 100).toFixed(2)}%`,
          row.quality || "待判断",
          row.negative || "未判定",
        ]) {
          const td = statusCell(value);
          if (tr.children.length === 0 && data.data_source === "formal") {
            td.replaceChildren();
            // 使用已应用的服务器筛选快照，避免未提交表单改变明细范围。
            const button = document.createElement("button");
            button.type = "button";
            button.className = "keyword-detail";
            button.textContent = String(value);
            button.title = "查看每日表现";
            button.addEventListener("click", () =>
              openDaily(row.keyword, data.applied_filters),
            );
            td.append(button);
          } else if (tr.children.length === 1) {
            td.replaceChildren();
            const button = document.createElement("button"); button.type = "button"; button.className = "keyword-detail"; button.textContent = String(value); button.addEventListener("click", () => openSearchTerms(row.keyword, row.search_term)); td.append(button);
          } else if (!td.className) td.textContent = String(value);
          tr.append(td);
        }
        const action = document.createElement("td");
        const preview = document.createElement("button");
        preview.type = "button";
        preview.className = "secondary keyword-action";
        preview.textContent = "否词预览";
        preview.addEventListener("click", () => openNegative(row.keyword, data.applied_filters));
        action.append(preview);
        const expand = document.createElement("button");
        expand.type = "button"; expand.className = "secondary keyword-action"; expand.textContent = "拓词预览";
        expand.addEventListener("click", () => openExpansion(row.keyword, data.applied_filters));
        expand.disabled = row.quality != null && row.quality !== "优质";
        expand.title = expand.disabled ? "只有优质词可以拓词" : "";
        action.append(expand);
        tr.append(action);
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
    previewMode = Boolean(event.detail.preview);
    page = 1;
    el("report-panel").hidden = false;
    const selected = el("account").value;
    el("account").replaceChildren(new Option("全部可见账户", ""));
    for (const account of event.detail.accounts)
      el("account").append(new Option(account.name, String(account.id)));
    el("negative-account").replaceChildren();
    el("expansion-account").replaceChildren(new Option("请选择账户", ""));
    for (const account of event.detail.accounts)
      el("negative-account").append(new Option(account.name, String(account.id)));
    for (const account of event.detail.accounts)
      el("expansion-account").append(new Option(account.name, String(account.id)));
    el("account").value = [...el("account").options].some(
      (option) => option.value === selected,
    )
      ? selected
      : "";
    if (previewMode) loadDemo();
    else load();
  });
  document.addEventListener("session-ended", () => {
    active = false;
    previewMode = false;
    ++sequence;
    controller?.abort();
    clear();
    el("report-panel").hidden = true;
  });
  el("report-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    page = 1;
    if (previewMode) loadDemo();
    else load();
  });
  for (const [id, view] of [["view-original", "original"], ["view-search-terms", "search-terms"], ["view-expansions", "expansions"]]) {
    el(id).addEventListener("click", () => {
      activeView = view;
      document.querySelectorAll(".view-tab").forEach((button) => button.classList.toggle("active", button.id === id));
      configureView();
      if (previewMode) loadDemo(); else load();
    });
  }
  for (const id of ["sort", "order", "page-size", "data-source", "quality-filter"])
    el(id).addEventListener("change", () => {
      page = 1;
      if (previewMode) loadDemo();
      else load();
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
