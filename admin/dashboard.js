(function () {
  "use strict";

  const els = {
    loginPanel: document.getElementById("login-panel"),
    dashboard: document.getElementById("dashboard"),
    tokenForm: document.getElementById("token-form"),
    tokenInput: document.getElementById("token-input"),
    statusLine: document.getElementById("status-line"),
    refreshBtn: document.getElementById("refresh-btn"),
    logoutBtn: document.getElementById("logout-btn"),
    summaryGrid: document.getElementById("summary-grid"),
    runtimeBots: document.getElementById("runtime-bots"),
    botCount: document.getElementById("bot-count"),
    metricsTable: document.getElementById("metrics-table"),
    metricsUptime: document.getElementById("metrics-uptime"),
    cacheList: document.getElementById("cache-list"),
    storageSummary: document.getElementById("storage-summary"),
    botUserCounts: document.getElementById("bot-user-counts"),
    eventCounts: document.getElementById("event-counts"),
    recentEvents: document.getElementById("recent-events"),
    recentUsers: document.getElementById("recent-users")
  };

  const TOKEN_KEY = "bradycadia_admin_token";
  let refreshTimer = null;

  function token() {
    return localStorage.getItem(TOKEN_KEY) || "";
  }

  function setToken(value) {
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  }

  function formatNumber(value) {
    if (value === null || value === undefined || value === "") return "0";
    return Number(value).toLocaleString();
  }

  function formatBytes(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 * 1024 * 1024) return `${(value / 1024 / 1024 / 1024).toFixed(2)} GB`;
    if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB`;
    if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${value} B`;
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function text(value) {
    return value === null || value === undefined || value === "" ? "-" : String(value);
  }

  function pill(label, tone) {
    return `<span class="pill ${tone || ""}">${label}</span>`;
  }

  function row(cells) {
    return `<tr>${cells.map(cell => `<td>${cell}</td>`).join("")}</tr>`;
  }

  function emptyRow(colspan, label) {
    return `<tr><td class="muted" colspan="${colspan}">${label}</td></tr>`;
  }

  async function loadDashboard() {
    const adminToken = token();
    if (!adminToken) {
      showLogin("Token required");
      return;
    }

    els.statusLine.textContent = "Loading...";
    try {
      const response = await fetch("/admin/api/overview", {
        headers: { Authorization: `Bearer ${adminToken}` },
        cache: "no-store"
      });
      if (response.status === 401 || response.status === 503) {
        showLogin(response.status === 503 ? "Dashboard disabled on server" : "Invalid token");
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      render(data);
      els.loginPanel.hidden = true;
      els.dashboard.hidden = false;
      els.statusLine.textContent = `Updated ${formatDate(data.generated_at)}`;
      scheduleRefresh();
    } catch (error) {
      els.statusLine.textContent = `Load failed: ${error.message}`;
    }
  }

  function showLogin(message) {
    els.loginPanel.hidden = false;
    els.dashboard.hidden = true;
    els.statusLine.textContent = message;
    if (refreshTimer) clearTimeout(refreshTimer);
  }

  function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(loadDashboard, 30000);
  }

  function render(data) {
    renderSummary(data);
    renderRuntimeBots(data.runtime_bots || []);
    renderMetrics(data.metrics || {});
    renderCache(data);
    renderBotUserCounts(data.bot_user_counts || []);
    renderEventCounts(data.webapp_event_counts || []);
    renderRecentEvents(data.recent_webapp_events || []);
    renderRecentUsers(data.recent_users || []);
  }

  function renderSummary(data) {
    const s = data.summary || {};
    const items = [
      ["Runtime Bots", (data.runtime_bots || []).filter(bot => bot.username).length],
      ["Products", s.products],
      ["Users", s.bot_users],
      ["Active 24h", s.active_users_24h],
      ["WebApp Events 24h", s.webapp_events_24h],
      ["Webhook 401", data.webhook_401_total],
      ["Pending Notifications", s.pending_notifications],
      ["File ID Cache", s.file_id_cache_entries],
      ["Media Mappings", s.bot_media_mappings],
      ["Inline Queries 24h", s.inline_queries_24h],
      ["Inline Chosen 24h", s.inline_chosen_24h],
      ["Blocked Users", s.blocked_users]
    ];
    els.summaryGrid.innerHTML = items.map(([label, value]) => (
      `<div class="kpi"><strong>${formatNumber(value)}</strong><span>${label}</span></div>`
    )).join("");
  }

  function renderRuntimeBots(bots) {
    els.botCount.textContent = `${bots.length} configured`;
    els.runtimeBots.innerHTML = bots.length ? bots.map(bot => {
      const tone = bot.username ? "good" : "warn";
      const runtime = bot.username ? pill(bot.running ? "running" : "loaded", tone) : pill("missing", "warn");
      return row([
        text(bot.index),
        runtime,
        text(bot.username),
        text(bot.telegram_bot_id),
        `<span class="muted">${text(bot.registry_bot_id)}</span>`,
        text(bot.webhook_secret_fp)
      ]);
    }).join("") : emptyRow(6, "No runtime bot data");
  }

  function renderMetrics(metrics) {
    els.metricsUptime.textContent = `${formatNumber(metrics.uptime_seconds || 0)}s uptime`;
    const endpoints = Object.entries(metrics.endpoints || {});
    els.metricsTable.innerHTML = endpoints.length ? endpoints.map(([name, stat]) => {
      const tone = Number(stat.p95_ms || 0) > 500 ? "warn" : "";
      return row([
        text(name),
        formatNumber(stat.count),
        formatNumber(stat.avg_ms),
        `<span class="${tone ? "pill warn" : ""}">${formatNumber(stat.p95_ms)}</span>`
      ]);
    }).join("") : emptyRow(4, "No metrics recorded yet");
  }

  function renderCache(data) {
    const cache = data.cache_state || {};
    const resources = data.resource_usage || {};
    const tmp = resources.tmp || {};
    const disk = resources.cache || {};
    els.storageSummary.textContent = `${formatBytes(tmp.used_bytes)} temp, ${formatBytes(disk.app_cache_bytes)} app cache`;
    const items = [
      ["Resolved file IDs", cache.resolved_file_ids],
      ["Media info", cache.media_info],
      ["API results", cache.api_results],
      ["Media write keys", cache.media_registry_write_keys],
      ["Orphan legacy secrets", cache.orphan_legacy_secrets],
      ["Webhook queues", cache.webhook_processing_locks],
      ["Webhook concurrency", cache.webhook_update_concurrency],
      ["Temp used", formatBytes(tmp.used_bytes)],
      ["App cache", formatBytes(disk.app_cache_bytes)]
    ];
    els.cacheList.innerHTML = items.map(([label, value]) => (
      `<div class="kv"><span>${label}</span><strong>${text(value)}</strong></div>`
    )).join("");
  }

  function renderBotUserCounts(counts) {
    els.botUserCounts.innerHTML = counts.length ? counts.map(item => row([
      text(item.bot_username),
      formatNumber(item.users),
      formatNumber(item.active_24h),
      formatNumber(item.blocked)
    ])).join("") : emptyRow(4, "No users tracked yet");
  }

  function renderEventCounts(counts) {
    els.eventCounts.innerHTML = counts.length ? counts.map(item => row([
      text(item.event_type),
      text(item.action),
      text(item.status),
      formatNumber(item.count)
    ])).join("") : emptyRow(4, "No WebApp events in the last 24h");
  }

  function renderRecentEvents(events) {
    els.recentEvents.innerHTML = events.length ? events.map(event => {
      let details = "";
      try {
        const parsed = JSON.parse(event.details_json || "{}");
        details = Object.entries(parsed).slice(0, 4).map(([key, value]) => `${key}: ${value}`).join(", ");
      } catch (_) {
        details = event.details_json || "";
      }
      const statusTone = event.status === "error" ? "bad" : (event.status === "warning" ? "warn" : "good");
      return row([
        formatDate(event.created_at),
        `${text(event.username)}<br><span class="muted">${text(event.user_id)}</span>`,
        text(event.event_type),
        text(event.action),
        pill(text(event.status), statusTone),
        `<div class="details">${text(details)}</div>`
      ]);
    }).join("") : emptyRow(6, "No recent WebApp events");
  }

  function renderRecentUsers(users) {
    els.recentUsers.innerHTML = users.length ? users.map(user => {
      const state = Number(user.is_blocked) ? pill("blocked", "bad") : (Number(user.is_subscribed) ? pill("subscribed", "good") : pill("unsubscribed", "warn"));
      return row([
        text(user.user_id),
        text(user.username),
        text(user.bot_username),
        text(user.language),
        formatNumber(user.interaction_count),
        formatDate(user.last_seen),
        state
      ]);
    }).join("") : emptyRow(7, "No users tracked yet");
  }

  els.tokenForm.addEventListener("submit", (event) => {
    event.preventDefault();
    setToken(els.tokenInput.value.trim());
    els.tokenInput.value = "";
    loadDashboard();
  });

  els.refreshBtn.addEventListener("click", loadDashboard);
  els.logoutBtn.addEventListener("click", () => {
    setToken("");
    showLogin("Token cleared");
  });

  if (token()) {
    loadDashboard();
  }
})();
