/* muse-um Control Center — vanilla JS, no CDN, no build step.
 * Talks only to the Relay HTTP API (PROTOCOL.md section 9).
 * Never displays root_id or any token, except the one-time token box
 * shown right after an account is created (cleared when dismissed).
 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  function showError(el, err) {
    el.textContent = "Lỗi: " + (err && err.message ? err.message : err);
  }

  async function api(path, options) {
    var res = await fetch(path, Object.assign(
      { headers: { "Content-Type": "application/json" } }, options || {}));
    var data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON body */ }
    if (!res.ok) {
      throw new Error((data && (data.message || data.error)) ||
        ("HTTP " + res.status));
    }
    return data;
  }

  /* ---- status (GET /api/status) ---- */
  async function loadStatus() {
    var box = $("status-text");
    try {
      var s = await api("/api/status");
      box.innerHTML =
        "Panel: " + (s.panel_online
          ? '<span class="status-ok">đang online</span>'
          : '<span class="status-bad">offline</span>') +
        " &nbsp;|&nbsp; Root: " + (s.root_active
          ? '<span class="status-ok">đang hoạt động</span>'
          : '<span class="status-bad">chưa hoạt động</span>') +
        " &nbsp;|&nbsp; Tài khoản: " + s.accounts +
        " &nbsp;|&nbsp; Phòng: " + s.rooms;
    } catch (e) {
      box.innerHTML = '<span class="status-bad">Không nối được Relay.</span> ' +
        "Kiểm tra Relay có đang chạy trên cổng 8000 không.";
    }
  }

  /* ---- accounts (GET /api/accounts) ---- */
  var accountsCache = [];

  async function loadAccounts() {
    var body = $("accounts-body");
    try {
      var list = await api("/api/accounts");
      accountsCache = list || [];
      if (!accountsCache.length) {
        body.innerHTML = "<tr><td colspan='2'>Chưa có tài khoản nào.</td></tr>";
      } else {
        body.innerHTML = accountsCache.map(function (a) {
          return "<tr><td>" + escapeHtml(a.name) + "</td><td><code>" +
            escapeHtml(a.account_id) + "</code></td></tr>";
        }).join("");
      }
      fillSelect($("pair-account"), accountsCache);
      fillSelect($("invite-account"), accountsCache);
      fillMultiSelect($("room-members"), accountsCache);
    } catch (e) {
      body.innerHTML = "<tr><td colspan='2' class='error'>Không tải được danh sách tài khoản.</td></tr>";
    }
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function fillSelect(sel, list) {
    var current = sel.value;
    sel.innerHTML = list.map(function (a) {
      return '<option value="' + escapeHtml(a.account_id) + '">' +
        escapeHtml(a.name) + " (" + escapeHtml(a.account_id) + ")</option>";
    }).join("");
    if (list.some(function (a) { return a.account_id === current; })) {
      sel.value = current;
    }
  }

  function fillMultiSelect(sel, list) {
    sel.innerHTML = list.map(function (a) {
      return '<option value="' + escapeHtml(a.account_id) + '">' +
        escapeHtml(a.name) + "</option>";
    }).join("");
  }

  /* ---- create account (POST /api/accounts) ---- */
  $("form-account").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    $("account-error").textContent = "";
    var name = $("account-name").value.trim();
    if (!name) { $("account-error").textContent = "Nhập tên tài khoản."; return; }
    try {
      var acc = await api("/api/accounts", {
        method: "POST", body: JSON.stringify({ name: name })
      });
      // Token is shown ONCE, right here, and cleared on dismiss.
      $("token-account-name").textContent = acc.name;
      $("token-account-id").textContent = acc.account_id;
      $("token-value").textContent = acc.token;
      $("copy-feedback").textContent = "";
      $("token-box").classList.remove("hidden");
      $("account-name").value = "";
      await loadAccounts();
      await loadStatus();
    } catch (e) { showError($("account-error"), e); }
  });

  $("btn-copy-token").addEventListener("click", async function () {
    var token = $("token-value").textContent;
    try {
      await navigator.clipboard.writeText(token);
      $("copy-feedback").textContent = "Đã copy.";
    } catch (e) {
      $("copy-feedback").textContent = "Không copy tự động được — hãy bôi đen token và copy tay.";
    }
  });

  $("btn-hide-token").addEventListener("click", function () {
    $("token-value").textContent = "";
    $("token-account-name").textContent = "";
    $("token-account-id").textContent = "";
    $("token-box").classList.add("hidden");
  });

  /* ---- pair code (POST /api/pair) ---- */
  var pairTimer = null;

  $("form-pair").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    $("pair-error").textContent = "";
    var accountId = $("pair-account").value;
    if (!accountId) { $("pair-error").textContent = "Chưa có tài khoản nào để tạo mã."; return; }
    try {
      var res = await api("/api/pair", {
        method: "POST", body: JSON.stringify({ account_id: accountId })
      });
      $("pair-code").textContent = res.code;
      $("pair-result").classList.remove("hidden");
      var expiresIn = res.expires_in || 300;
      var deadline = Date.now() + expiresIn * 1000;
      if (pairTimer) { clearInterval(pairTimer); }
      var tick = function () {
        var left = Math.max(0, Math.round((deadline - Date.now()) / 1000));
        $("pair-countdown").textContent =
          Math.floor(left / 60) + " phút " + (left % 60) + " giây";
        if (left <= 0) {
          clearInterval(pairTimer);
          $("pair-result").classList.add("hidden");
        }
      };
      tick();
      pairTimer = setInterval(tick, 1000);
    } catch (e) { showError($("pair-error"), e); }
  });

  /* ---- rooms (GET /api/rooms, POST /api/rooms) ---- */
  var roomsCache = [];

  async function loadRooms() {
    var body = $("rooms-body");
    try {
      var list = await api("/api/rooms");
      roomsCache = list || [];
      if (!roomsCache.length) {
        body.innerHTML = "<tr><td colspan='4'>Chưa có phòng nào.</td></tr>";
      } else {
        body.innerHTML = roomsCache.map(function (r) {
          var members = (r.members || []).map(function (m) {
            return escapeHtml(typeof m === "string" ? m : (m.name || m.account_id));
          }).join(", ");
          return "<tr><td>" + escapeHtml(r.name || "") + "</td><td><code>" +
            escapeHtml(r.room_id) + "</code></td><td>" + members +
            "</td><td>" + (r.message_count || 0) + "</td></tr>";
        }).join("");
      }
      var sel = $("invite-room");
      var current = sel.value;
      sel.innerHTML = roomsCache.map(function (r) {
        return '<option value="' + escapeHtml(r.room_id) + '">' +
          escapeHtml(r.name || r.room_id) + "</option>";
      }).join("");
      if (roomsCache.some(function (r) { return r.room_id === current; })) {
        sel.value = current;
      }
    } catch (e) {
      body.innerHTML = "<tr><td colspan='4' class='error'>Không tải được danh sách phòng.</td></tr>";
    }
  }

  $("form-room").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    $("room-error").textContent = "";
    $("room-created").textContent = "";
    var name = $("room-name").value.trim();
    var memberIds = Array.prototype.slice.call(
      $("room-members").selectedOptions).map(function (o) { return o.value; });
    try {
      var res = await api("/api/rooms", {
        method: "POST",
        body: JSON.stringify({ name: name || undefined, member_ids: memberIds })
      });
      $("room-created").textContent = "Đã tạo phòng. Room ID: " + res.room_id;
      $("room-name").value = "";
      await loadRooms();
      await loadStatus();
    } catch (e) { showError($("room-error"), e); }
  });

  /* ---- invite (POST /api/rooms/<room_id>/invite) ---- */
  $("form-invite").addEventListener("submit", async function (ev) {
    ev.preventDefault();
    $("invite-error").textContent = "";
    $("invite-result").textContent = "";
    var roomId = $("invite-room").value;
    var accountId = $("invite-account").value;
    if (!roomId || !accountId) {
      $("invite-error").textContent = "Chọn cả phòng và tài khoản.";
      return;
    }
    try {
      await api("/api/rooms/" + encodeURIComponent(roomId) + "/invite", {
        method: "POST", body: JSON.stringify({ account_id: accountId })
      });
      $("invite-result").textContent = "Đã mời tài khoản vào phòng.";
      await loadRooms();
    } catch (e) { showError($("invite-error"), e); }
  });

  /* ---- refresh buttons + polling ---- */
  $("btn-refresh-status").addEventListener("click", loadStatus);
  $("btn-refresh-accounts").addEventListener("click", loadAccounts);
  $("btn-refresh-rooms").addEventListener("click", loadRooms);

  loadStatus();
  loadAccounts();
  loadRooms();
  setInterval(loadStatus, 10000);
})();
