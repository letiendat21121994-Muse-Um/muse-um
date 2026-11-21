"use strict";

const $ = (id) => document.getElementById(id);
const groups = {
  spend: "Chi tiêu", delivery: "Delivery", tracking: "Tracking", performance: "Hiệu suất",
};
const levels = { account: "Tài khoản", campaign: "Campaign", adset: "ad set", ad: "Quảng cáo" };
const numeric = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 });
let filter = "all";
let alerts = [];
let selection = null;
let lastPayload = "";

function element(type, text, className) {
  const node = document.createElement(type);
  if (text) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function alertKey(alert) {
  return JSON.stringify([alert.level, alert.entity_id, alert.code]);
}

function tag(alert) {
  const critical = alert.severity === "critical";
  return element("span", critical ? "Khẩn" : "Cảnh báo", critical ? "tag critical" : "tag");
}

function select(alert) {
  selection = alert && alertKey(alert);
  for (const button of $("alerts").querySelectorAll("button")) {
    button.setAttribute("aria-current", String(button.dataset.key === selection));
  }
  const content = $("detail-content");
  if (!alert) {
    content.replaceChildren(element("p", "Không có cảnh báo trong nhóm này.", "explain"));
    return;
  }
  const suggestion = element("div", null, "suggestion");
  suggestion.append(element("strong", "Việc nên kiểm tra"), element("p", alert.suggestion));
  content.replaceChildren(
    tag(alert),
    element("h3", alert.title),
    element("p", levels[alert.level] + " · " + alert.entity_name, "entity"),
    element("p", alert.message, "explain"),
    suggestion,
  );
}

function drawAlerts() {
  const visible = alerts.filter((alert) => filter === "all" || filter === alert.category);
  const count = visible.length;
  const critical = visible.filter((alert) => alert.severity === "critical").length;
  $("counts").textContent = count + " cảnh báo · " + critical + " khẩn";
  const rows = visible.map((alert) => {
    const copy = element("span");
    copy.append(element("strong", alert.title), element("small", alert.entity_name + " · " + groups[alert.category]));
    const button = element("button");
    button.type = "button";
    button.dataset.key = alertKey(alert);
    button.append(copy, tag(alert));
    button.addEventListener("click", () => select(alert));
    const row = element("li");
    row.append(button);
    return row;
  });
  $("alerts").replaceChildren(...(rows.length ? rows : [element("li", "Không có cảnh báo.", "empty")]));
  select(visible.find((alert) => alertKey(alert) === selection) || visible[0] || null);
}

for (const button of document.querySelectorAll("[data-filter]")) {
  button.addEventListener("click", () => {
    filter = button.dataset.filter;
    for (const item of document.querySelectorAll("[data-filter]")) {
      item.setAttribute("aria-pressed", String(item === button));
    }
    drawAlerts();
  });
}

async function refresh() {
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error("status");
    const state = await response.json();
    const snapshot = state.snapshot;
    $("mode").textContent = state.mode === "demo" ? "●  Chế độ demo" : "●  Meta Marketing API";
    $("mode").hidden = false;
    const messages = [];
    if (state.error) messages.push((snapshot ? "Đang hiển thị dữ liệu cũ. " : "") + state.error);
    if (state.log_error) messages.push("Không ghi được alert log; cảnh báo vẫn hiển thị.");
    if (state.mode === "demo") messages.push("Dữ liệu giả lập cho sản phẩm chăm sóc tóc tại US. Không kết nối tài khoản Meta.");
    if (!snapshot && !state.error) messages.push("Đang tải bản kiểm tra đầu tiên…");
    if (snapshot) messages.push(...snapshot.notes);
    $("notice").classList.toggle("error", Boolean(state.error || state.log_error));
    $("notice").textContent = messages.join(" ");
    $("notice").hidden = !messages.length;
    if (!snapshot) return;
    $("overview").hidden = false;
    $("inbox").hidden = false;
    $("account").textContent = snapshot.name;
    const asOf = new Intl.DateTimeFormat("vi-VN", {
      dateStyle: "short", timeStyle: "short", timeZone: snapshot.timezone,
    }).format(new Date(snapshot.data_at));
    $("as-of").textContent = asOf + " · " + snapshot.timezone;
    const metrics = snapshot.metrics;
    const money = (value) => value == null ? "—" : numeric.format(value) + " " + snapshot.currency;
    $("spend").textContent = money(metrics.spend);
    $("purchases").textContent = metrics.purchases == null ? "—" : numeric.format(metrics.purchases);
    $("cpa").textContent = money(metrics.cpa);
    $("roas").textContent = metrics.roas == null ? "—" : numeric.format(metrics.roas) + "×";
    $("poll-info").textContent = "Chu kỳ kiểm tra: " + state.poll_seconds + " giây. Baseline: "
      + snapshot.baseline_days + " ngày đã kết thúc. Không có thao tác tự động trên ad account.";
    const payload = JSON.stringify(snapshot.alerts);
    if (payload !== lastPayload) {
      alerts = snapshot.alerts;
      drawAlerts();
      lastPayload = payload;
    }
  } catch {
    $("notice").classList.add("error");
    $("notice").textContent = ($("overview").hidden ? "" : "Đang hiển thị dữ liệu cũ. ")
      + "Không kết nối được dashboard local.";
    $("notice").hidden = false;
  }
}

refresh();
setInterval(refresh, 5000);
