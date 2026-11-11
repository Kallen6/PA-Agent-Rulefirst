/* PA 决策台前端逻辑：参数记忆 / 决策请求 / 历史抽屉（零依赖原生 JS） */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var STORAGE_KEY = "pa-console-config-v1";
  var els = {
    form: $("config-form"),
    sourceSeg: $("source-seg"),
    regimeGroup: $("regime-group"),
    liveGroup: $("live-group"),
    regime: $("regime"),
    setup: $("setup"),
    symbol: $("symbol"),
    timeframe: $("timeframe"),
    barCount: $("bar-count"),
    market: $("market"),
    baseUrl: $("base-url"),
    proxy: $("proxy"),
    llm: $("llm"),
    vote: $("vote"),
    voteValue: $("vote-value"),
    band: $("band"),
    bandValue: $("band-value"),
    runBtn: $("run-btn"),
    saveToggle: $("save-toggle"),
    connStatus: $("conn-status"),
    summaryBar: $("summary-bar"),
    summaryBadge: $("summary-badge"),
    summarySymbol: $("summary-symbol"),
    summaryCycle: $("summary-cycle"),
    summaryTerminal: $("summary-terminal"),
    summarySource: $("summary-source"),
    summarySaved: $("summary-saved"),
    errorBanner: $("error-banner"),
    reportEmpty: $("report-empty"),
    reportFrame: $("report-frame"),
    historyBtn: $("history-btn"),
    historyDrawer: $("history-drawer"),
    drawerMask: $("drawer-mask"),
    drawerClose: $("drawer-close"),
    historyList: $("history-list"),
  };

  var source = "regime";
  var lastRequest = null;

  /* ── 配置记忆 ── */
  var FIELDS = ["regime", "symbol", "timeframe", "bar-count", "market", "base-url", "proxy", "llm", "vote", "band"];

  function saveConfig() {
    var cfg = { source: source, save: els.saveToggle.checked };
    FIELDS.forEach(function (id) {
      var el = $(id);
      cfg[id] = el.type === "checkbox" ? el.checked : el.value;
    });
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg)); } catch (e) { /* 隐私模式忽略 */ }
  }

  function loadConfig() {
    var cfg;
    try { cfg = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (e) { cfg = null; }
    if (!cfg) return;
    if (cfg.source === "live" || cfg.source === "regime") setSource(cfg.source, true);
    FIELDS.forEach(function (id) {
      var el = $(id);
      if (cfg[id] === undefined || cfg[id] === null) return;
      if (el.type === "checkbox") el.checked = !!cfg[id];
      else el.value = cfg[id];
    });
    if (cfg.save !== undefined) els.saveToggle.checked = !!cfg.save;
    syncHints();
  }

  function syncHints() {
    var v = parseInt(els.vote.value, 10);
    els.voteValue.textContent = v >= 2 ? v + " 次取多数" : (v === 1 ? "1 次（等同关闭）" : "关闭");
    var b = parseInt(els.band.value, 10);
    els.bandValue.textContent = b === 0 ? "默认 0.05" : (b / 100).toFixed(2);
  }

  /* ── 数据来源切换 ── */
  function setSource(value, silent) {
    source = value;
    Array.prototype.forEach.call(els.sourceSeg.children, function (btn) {
      btn.classList.toggle("active", btn.dataset.value === value);
    });
    els.regimeGroup.classList.toggle("hidden", value !== "regime");
    els.liveGroup.classList.toggle("hidden", value !== "live");
    if (!silent) saveConfig();
  }

  els.sourceSeg.addEventListener("click", function (ev) {
    var btn = ev.target.closest(".seg-item");
    if (btn) setSource(btn.dataset.value);
  });

  [els.vote, els.band].forEach(function (el) {
    el.addEventListener("input", function () { syncHints(); saveConfig(); });
  });
  FIELDS.forEach(function (id) {
    $(id).addEventListener("change", saveConfig);
  });
  els.saveToggle.addEventListener("change", saveConfig);

  /* ── 状态指示 ── */
  function setStatus(mode, text) {
    var dot = els.connStatus.querySelector(".dot");
    var label = els.connStatus.querySelector("em");
    dot.className = "dot dot-" + mode;
    label.textContent = text;
  }

  function showError(message) {
    els.errorBanner.textContent = message;
    els.errorBanner.classList.remove("hidden");
  }

  function hideError() {
    els.errorBanner.classList.add("hidden");
    els.errorBanner.textContent = "";
  }

  /* ── 决策请求 ── */
  function buildPayload() {
    var payload = {
      source: source,
      save: els.saveToggle.checked,
      llm: els.llm.value,
      vote: parseInt(els.vote.value, 10) || 0,
    };
    if (els.band.value !== "0") payload.band = parseInt(els.band.value, 10) / 100;
    if (source === "regime") {
      payload.regime = els.regime.value;
      payload.setup = els.setup.checked;
    } else {
      payload.symbol = els.symbol.value.trim();
      payload.timeframe = els.timeframe.value;
      payload.bar_count = parseInt(els.barCount.value, 10) || 101;
      payload.market = els.market.value;
      payload.base_url = els.baseUrl.value.trim();
      payload.proxy = els.proxy.value.trim();
    }
    return payload;
  }

  function renderSummary(summary, savedPath) {
    var outcome = (summary.terminal && summary.terminal.outcome) || "wait";
    var outcomeZh = { trade: "可交易", reject: "否决", wait: "观望" }[outcome] || outcome;
    var node = (summary.terminal && summary.terminal.node_id) || "—";
    els.summaryBadge.textContent = outcomeZh;
    els.summaryBadge.className = "badge badge-" + outcome;
    els.summarySymbol.textContent = (summary.symbol || "—") + " " + (summary.timeframe || "");
    els.summaryCycle.textContent = "周期=" + (summary.cycle_position || "—")
      + " 方向=" + (summary.direction || "—");
    els.summaryTerminal.textContent = "§" + node + " · " + ((summary.terminal && summary.terminal.label) || "");
    els.summarySource.textContent = summary.source || "";
    if (savedPath) {
      els.summarySaved.textContent = "已保存 " + savedPath;
      els.summarySaved.classList.remove("hidden");
    } else {
      els.summarySaved.classList.add("hidden");
    }
    els.summaryBar.classList.remove("hidden");
  }

  function showReport(html) {
    els.reportEmpty.classList.add("hidden");
    els.reportFrame.classList.remove("hidden");
    els.reportFrame.srcdoc = html;
  }

  els.form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (els.runBtn.disabled) return;
    hideError();
    lastRequest = buildPayload();
    els.runBtn.disabled = true;
    els.runBtn.querySelector(".btn-text").classList.add("hidden");
    els.runBtn.querySelector(".btn-loading").classList.remove("hidden");
    setStatus("run", "计算中…");

    fetch("/api/decide", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastRequest),
    })
      .then(function (res) {
        return res.json().then(function (data) { return { status: res.status, data: data }; });
      })
      .then(function (wrapper) {
        var data = wrapper.data;
        if (!data || !data.ok) {
          throw new Error((data && data.error) || ("HTTP " + wrapper.status));
        }
        renderSummary(data.summary, data.saved_path);
        showReport(data.report_html);
        setStatus("ok", "完成");
      })
      .catch(function (err) {
        showError("生成失败：" + err.message);
        setStatus("err", "失败");
      })
      .finally(function () {
        els.runBtn.disabled = false;
        els.runBtn.querySelector(".btn-text").classList.remove("hidden");
        els.runBtn.querySelector(".btn-loading").classList.add("hidden");
      });
  });

  /* ── 历史抽屉 ── */
  function openDrawer() {
    els.historyDrawer.classList.remove("hidden");
    els.drawerMask.classList.remove("hidden");
    loadHistory();
  }

  function closeDrawer() {
    els.historyDrawer.classList.add("hidden");
    els.drawerMask.classList.add("hidden");
  }

  function outcomeBadge(outcome) {
    var zh = { trade: "可交易", reject: "否决", wait: "观望" }[outcome] || outcome || "—";
    return '<span class="badge badge-' + (outcome || "wait") + '">' + zh + "</span>";
  }

  function loadHistory() {
    els.historyList.innerHTML = '<li class="muted">加载中…</li>';
    fetch("/api/records")
      .then(function (res) { return res.json(); })
      .then(function (data) {
        if (!data.ok) throw new Error(data.error || "加载失败");
        if (!data.records.length) {
          els.historyList.innerHTML = '<li class="muted">暂无已保存的决策记录</li>';
          return;
        }
        els.historyList.innerHTML = "";
        data.records.forEach(function (item) {
          var li = document.createElement("li");
          li.className = "history-item";
          li.innerHTML =
            '<div class="hi-top"><span class="hi-time">' + escapeHtml(item.generated_at || "")
            + '</span>' + outcomeBadge(item.outcome) + '</div>'
            + '<div class="hi-mid"><span class="hi-sym">' + escapeHtml((item.symbol || "—") + " " + (item.timeframe || ""))
            + '</span><span class="hi-cycle">' + escapeHtml(item.cycle_position || "—")
            + " · §" + escapeHtml(item.node_id || "—") + "</span></div>";
          li.addEventListener("click", function () { loadRecord(item.path); });
          els.historyList.appendChild(li);
        });
      })
      .catch(function (err) {
        els.historyList.innerHTML = '<li class="muted">加载失败：' + escapeHtml(err.message) + "</li>";
      });
  }

  function loadRecord(path) {
    setStatus("run", "加载记录…");
    fetch("/api/record?path=" + encodeURIComponent(path))
      .then(function (res) { return res.json().then(function (data) { return { status: res.status, data: data }; }); })
      .then(function (wrapper) {
        if (!wrapper.data.ok) throw new Error(wrapper.data.error || "HTTP " + wrapper.status);
        renderSummary(wrapper.data.summary, null);
        showReport(wrapper.data.report_html);
        closeDrawer();
        setStatus("ok", "完成");
      })
      .catch(function (err) {
        showError("加载历史记录失败：" + err.message);
        setStatus("err", "失败");
      });
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  els.historyBtn.addEventListener("click", openDrawer);
  els.drawerClose.addEventListener("click", closeDrawer);
  els.drawerMask.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") closeDrawer();
  });

  /* ── 初始化 ── */
  loadConfig();
})();
