/**
 * 共有物のバージョン管理 UI（V層, migration 037）。
 *
 * ES5 / IIFE。window.Versioning を公開。admin.js の initApp() から
 * Versioning.init({apiFetch: apiFetch, state: state}) で起動する。
 *
 * - openModal(objectType, objectId, title): 所有者向け（発行 / 版履歴 / 削除予約・取消）
 * - initInbox(): 共有先向け通知インボックス（更新あり → 取り込む / 削除予定バナー）
 *
 * API は /api/admin/shared/... （apiFetch は /api を前置し、Response を返す）。
 */
(function () {
  "use strict";

  var deps = { apiFetch: null, state: null };
  var _inboxTimer = null;

  function apiFetch(path, opts) {
    var fn = deps.apiFetch || window.apiFetch;
    return fn(path, opts);
  }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      if (isNaN(d.getTime())) return iso;
      return d.getFullYear() + "/" + (d.getMonth() + 1) + "/" + d.getDate() +
        " " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2);
    } catch (e) { return iso; }
  }

  function typeLabel(t) { return t === "course" ? "コース" : (t === "material" ? "教材" : "教材の解析成果"); }

  function json(res) { return res.json(); }

  // -------------------------------------------------------------------------
  // 所有者向けモーダル
  // -------------------------------------------------------------------------

  function _introText(objectType) {
    // コースは版解決が読み取り経路に効くため「同意するまで旧版」が成立する。
    // 教材（解析成果）の版固定ブラウズは今後対応（設計 §7）。現状は履歴記録 + 更新通知 +
    // 引用の版固定のみが有効なので、それに合わせて正直に説明する（過剰な約束をしない）。
    if (objectType === "document") {
      return '発行した版は履歴として記録され、共有先へ更新通知が届きます。引用（再利用）はその時点の版に固定されます。' +
             '※解析成果ビューの版固定表示は今後対応予定で、現在は共有先にも最新の解析結果が表示されます。';
    }
    return '発行した版だけが共有先に見え、共有先は同意するまで自分の版に留まります。';
  }

  function openModal(objectType, objectId, title) {
    var existing = document.getElementById("vg-modal");
    if (existing) existing.remove();

    var overlay = document.createElement("div");
    overlay.id = "vg-modal";
    overlay.style.cssText = "position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:10000";
    overlay.innerHTML =
      '<div style="background:var(--color-bg-primary,#fff);border-radius:8px;max-width:560px;width:92%;max-height:86vh;overflow:auto;padding:20px;box-shadow:0 8px 30px rgba(0,0,0,0.25)">' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px">' +
          '<h3 style="margin:0;font-size:16px;color:var(--color-text-primary)">共有バージョン管理</h3>' +
          '<button id="vg-close" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--color-text-tertiary)">&times;</button>' +
        '</div>' +
        '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0 0 12px">' + esc(typeLabel(objectType)) + '「' + esc(title || "") + '」の共有版を管理します。' + esc(_introText(objectType)) + '</p>' +
        '<div id="vg-banner"></div>' +
        '<div id="vg-body"><p style="font-size:12px;color:var(--color-text-tertiary)">読み込み中...</p></div>' +
      '</div>';
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (e) { if (e.target === overlay) overlay.remove(); });
    document.getElementById("vg-close").addEventListener("click", function () { overlay.remove(); });

    _loadModal(objectType, objectId, title);
  }

  function _loadModal(objectType, objectId, title) {
    var base = "/admin/shared/" + encodeURIComponent(objectType) + "/" + encodeURIComponent(objectId);
    Promise.all([
      apiFetch(base + "/version-state").then(json).catch(function () { return {}; }),
      apiFetch(base + "/releases").then(json).catch(function () { return []; })
    ]).then(function (out) {
      _renderModal(objectType, objectId, title, out[0] || {}, out[1] || []);
    });
  }

  function _renderModal(objectType, objectId, title, state, releases) {
    var body = document.getElementById("vg-body");
    var banner = document.getElementById("vg-banner");
    if (!body) return;

    var lifecycle = state.lifecycle || "active";
    var pending = lifecycle === "pending_deletion";

    if (banner) {
      if (pending) {
        banner.innerHTML =
          '<div style="border:1px solid var(--color-text-danger,#c00);background:rgba(200,0,0,0.06);border-radius:6px;padding:10px;margin-bottom:12px">' +
            '<div style="font-size:13px;color:var(--color-text-danger,#c00);font-weight:600">削除予定</div>' +
            '<div style="font-size:12px;color:var(--color-text-secondary);margin-top:2px">期限 <b>' + esc(fmtDate(state.delete_purge_after)) + '</b> 以降、全ユーザーから物理削除されます。' +
            (state.delete_reason ? ' 理由: ' + esc(state.delete_reason) : '') + '</div>' +
            '<button id="vg-cancel-del" style="margin-top:8px;background:none;border:1px solid var(--color-text-info);color:var(--color-text-info);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">削除予約を取り消す</button>' +
          '</div>';
      } else {
        banner.innerHTML = "";
      }
    }

    var latest = state.latest_version_no || 0;
    var histHtml = "";
    if (!releases.length) {
      histHtml = '<p style="font-size:12px;color:var(--color-text-tertiary);margin:0">まだ発行された版はありません。</p>';
    } else {
      histHtml = releases.map(function (r) {
        var isActive = state.active_release_id && r.id === state.active_release_id;
        return '<div style="border:1px solid var(--color-border,#ddd);border-radius:6px;padding:8px 10px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:center">' +
          '<div><b style="font-size:13px;color:var(--color-text-primary)">v' + esc(r.version_no) + '</b>' +
          (isActive ? ' <span style="font-size:10px;background:var(--color-text-success,#2a7);color:#fff;padding:1px 6px;border-radius:3px">現行</span>' : '') +
          '<div style="font-size:11px;color:var(--color-text-tertiary)">' + esc(fmtDate(r.created_at)) + (r.note ? ' — ' + esc(r.note) : '') + '</div></div>' +
          '</div>';
      }).join("");
    }

    body.innerHTML =
      '<div style="margin-bottom:14px">' +
        '<div style="font-size:12px;color:var(--color-text-secondary);margin-bottom:6px">現在の最新版: <b>' + (latest ? 'v' + esc(latest) : '未発行') + '</b></div>' +
        (pending ? '' :
          '<textarea id="vg-note" placeholder="この版のメモ（任意。例: 第3章を修正）" style="width:100%;box-sizing:border-box;min-height:48px;font-size:12px;padding:6px;border:1px solid var(--color-border,#ccc);border-radius:4px"></textarea>' +
          '<button id="vg-publish" style="margin-top:6px;background:var(--color-text-info,#06c);border:none;color:#fff;padding:6px 14px;border-radius:4px;cursor:pointer;font-size:13px">共有版として発行</button>') +
      '</div>' +
      '<h4 style="font-size:13px;margin:12px 0 8px 0;color:var(--color-text-secondary)">版の履歴</h4>' +
      histHtml +
      (pending ? '' :
        '<div style="margin-top:16px;border-top:1px solid var(--color-border,#eee);padding-top:12px">' +
          '<h4 style="font-size:13px;margin:0 0 6px 0;color:var(--color-text-danger,#c00)">削除の予約</h4>' +
          '<p style="font-size:11px;color:var(--color-text-tertiary);margin:0 0 6px">猶予日数を指定して削除を予約します。共有先に通知され、期限後に全ユーザーから物理削除されます。</p>' +
          '<label style="font-size:12px;color:var(--color-text-secondary)">猶予日数 <input id="vg-grace" type="number" min="1" value="14" style="width:64px;padding:4px;border:1px solid var(--color-border,#ccc);border-radius:4px"></label> ' +
          '<input id="vg-reason" type="text" placeholder="理由（任意）" style="width:200px;padding:4px;border:1px solid var(--color-border,#ccc);border-radius:4px;font-size:12px"> ' +
          '<button id="vg-schedule-del" style="background:none;border:1px solid var(--color-text-danger,#c00);color:var(--color-text-danger,#c00);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">削除を予約</button>' +
        '</div>') +
      '<div id="vg-status" style="margin-top:10px;font-size:12px"></div>';

    _bindModalActions(objectType, objectId, title);
  }

  function _setStatus(msg, kind) {
    var el = document.getElementById("vg-status");
    if (!el) return;
    var color = kind === "error" ? "var(--color-text-danger,#c00)" : "var(--color-text-success,#2a7)";
    el.style.color = color;
    el.textContent = msg;
  }

  function _bindModalActions(objectType, objectId, title) {
    var base = "/admin/shared/" + encodeURIComponent(objectType) + "/" + encodeURIComponent(objectId);

    var pub = document.getElementById("vg-publish");
    if (pub) pub.addEventListener("click", function () {
      var note = (document.getElementById("vg-note") || {}).value || "";
      pub.disabled = true;
      apiFetch(base + "/releases", { method: "POST", body: JSON.stringify({ note: note }) })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (b) { throw b; });
          return res.json();
        })
        .then(function (rel) {
          _setStatus("v" + rel.version_no + " を発行しました（" + (rel.notified || 0) + "名に通知）", "success");
          _loadModal(objectType, objectId, title);
        })
        .catch(function (b) { pub.disabled = false; _setStatus((b && b.detail) || "発行に失敗しました", "error"); });
    });

    var sched = document.getElementById("vg-schedule-del");
    if (sched) sched.addEventListener("click", function () {
      var grace = parseInt((document.getElementById("vg-grace") || {}).value, 10);
      var reason = (document.getElementById("vg-reason") || {}).value || "";
      if (!grace || grace < 1) { _setStatus("猶予日数を1以上で指定してください", "error"); return; }

      function _doScheduleDeletion() {
        sched.disabled = true;
        apiFetch(base + "/deletion", { method: "POST", body: JSON.stringify({ grace_days: grace, reason: reason }) })
          .then(function (res) { if (!res.ok) return res.json().then(function (b) { throw b; }); return res.json(); })
          .then(function () { _setStatus("削除を予約しました", "success"); _loadModal(objectType, objectId, title); })
          .catch(function (b) { sched.disabled = false; _setStatus((b && b.detail) || "予約に失敗しました", "error"); });
      }

      // admin.js の共通2段確認モーダル（明示ボタンクリック必須）があればそれを使い、
      // 無ければ従来の window.confirm() にフォールバックする。
      if (window.AdminDangerConfirm && typeof window.AdminDangerConfirm.open === "function") {
        window.AdminDangerConfirm.open({
          title: "削除の予約",
          message: "削除を予約します。期限後に全ユーザーから物理削除されます。よろしいですか？",
          confirmLabel: "予約する",
        }, _doScheduleDeletion);
      } else {
        if (!window.confirm("削除を予約します。期限後に全ユーザーから物理削除されます。よろしいですか？")) return;
        _doScheduleDeletion();
      }
    });

    var cancel = document.getElementById("vg-cancel-del");
    if (cancel) cancel.addEventListener("click", function () {
      cancel.disabled = true;
      apiFetch(base + "/deletion", { method: "DELETE" })
        .then(function (res) { if (!res.ok) return res.json().then(function (b) { throw b; }); return res.json(); })
        .then(function () { _setStatus("削除予約を取り消しました", "success"); _loadModal(objectType, objectId, title); })
        .catch(function (b) { cancel.disabled = false; _setStatus((b && b.detail) || "取り消しに失敗しました", "error"); });
    });
  }

  // -------------------------------------------------------------------------
  // 共有先向け通知インボックス
  // -------------------------------------------------------------------------

  // 通知ベルはトップバーの常設ボタン（admin.html の #vg-inbox-btn）を使う。
  // 以前この位置は右下の常設フローティングボタンだったが、「AI アシスタントへの
  // アクセスを良くする」ため位置を入れ替え、右下フローティングは Admin Copilot（🤖）に譲った。
  function initInbox() {
    var btn = document.getElementById("vg-inbox-btn");
    if (!btn || btn.dataset.vgInboxBound) return;
    btn.dataset.vgInboxBound = "1";
    var badge = document.createElement("span");
    badge.id = "vg-inbox-badge";
    badge.style.cssText = "margin-left:3px;font-size:11px";
    btn.appendChild(badge);
    btn.addEventListener("click", _toggleInbox);
    _refreshInbox();
    _inboxTimer = setInterval(_refreshInbox, 60000);
  }

  function _refreshInbox() {
    var badge = document.getElementById("vg-inbox-badge");
    apiFetch("/admin/notifications").then(json).then(function (data) {
      if (!badge) return;
      var n = (data && data.unread_count) || 0;
      badge.textContent = n > 0 ? (n > 9 ? " (9+)" : " (" + n + ")") : "";
      badge.style.color = n > 0 ? "var(--color-text-danger,#e24b4a)" : "var(--color-text-secondary,#6e6e73)";
      badge.style.fontWeight = n > 0 ? "600" : "normal";
    }).catch(function () {});
  }

  function _toggleInbox() {
    var existing = document.getElementById("vg-inbox-panel");
    if (existing) { existing.remove(); return; }
    var panel = document.createElement("div");
    panel.id = "vg-inbox-panel";
    panel.style.cssText = "position:fixed;top:46px;right:20px;width:340px;max-width:calc(100vw - 32px);max-height:60vh;overflow:auto;background:var(--color-background-primary,#fff);border:1px solid var(--color-border-secondary,#d2d2d7);border-radius:12px;box-shadow:0 20px 48px rgba(0,0,0,0.24);z-index:10045;padding:12px";
    panel.innerHTML =
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
        '<b style="font-size:14px;color:var(--color-text-primary)">共有物の更新通知</b>' +
        '<button id="vg-inbox-readall" style="background:none;border:none;color:var(--color-text-info);font-size:12px;cursor:pointer">すべて既読</button>' +
      '</div><div id="vg-inbox-list"><p style="font-size:12px;color:var(--color-text-tertiary)">読み込み中...</p></div>';
    document.body.appendChild(panel);
    document.getElementById("vg-inbox-readall").addEventListener("click", function () {
      apiFetch("/admin/notifications/read-all", { method: "POST" }).then(function () {
        _refreshInbox(); _loadInboxList();
      });
    });
    _loadInboxList();
  }

  function _loadInboxList() {
    apiFetch("/admin/notifications").then(json).then(function (data) {
      var list = document.getElementById("vg-inbox-list");
      if (!list) return;
      var items = (data && data.notifications) || [];
      if (!items.length) {
        list.innerHTML = '<p style="font-size:12px;color:var(--color-text-tertiary)">通知はありません。</p>';
        return;
      }
      list.innerHTML = items.map(_renderNotif).join("");
      _bindInboxItems();
    });
  }

  // 生テキストを返す（HTML エスケープは _renderNotif 側で一度だけ行う。二重エスケープ防止）。
  function _notifText(n) {
    var t = typeLabel(n.object_type || n.entity_type);
    var note = n.payload && n.payload.note ? n.payload.note : "";
    if (n.kind === "version_published") {
      if (n.object_type === "document") {
        // 教材は版固定ブラウズ未対応のため「更新されました」と断定しない（現状は最新表示）。
        return t + "の新しい共有版 v" + (n.version_no || "?") + " が発行されました" + (note ? "（" + note + "）" : "");
      }
      return t + "が v" + (n.version_no || "?") + " に更新されました" + (note ? "（" + note + "）" : "");
    }
    if (n.kind === "deletion_scheduled") return t + "の削除が予約されました。期限 " + fmtDate(n.payload && n.payload.purge_after) + " 以降に削除されます";
    if (n.kind === "deletion_cancelled") return t + "の削除予約が取り消されました";
    if (n.kind === "deleted") return t + "は削除されました";
    // 状態管理・通知基盤（migration 038）の通知種別
    if (n.kind === "material_analysis_completed") return t + "の解析が完了しました";
    if (n.kind === "material_analysis_failed") {
      var reason = n.payload && n.payload.reason ? "（" + n.payload.reason + "）" : "";
      return t + "の解析に失敗しました" + reason;
    }
    if (n.kind === "course_script_completed") return t + "の原稿生成が完了しました";
    if (n.kind === "course_script_failed") return t + "の原稿生成に失敗しました";
    if (n.kind === "course_audio_completed") return t + "の音声生成が完了しました";
    if (n.kind === "course_audio_failed") return t + "の音声生成に失敗しました";
    return t + "の更新";
  }

  function _adoptLabel(objectType) {
    // コースは取り込みで表示版が進む。教材は表示は変わらず「更新の確認」に留まる（正直な文言）。
    return objectType === "document" ? "更新を確認" : "取り込む";
  }

  function _renderNotif(n) {
    var unread = !n.read_at;
    var canAdopt = n.kind === "version_published" && !n.acted_at;
    return '<div class="vg-notif" data-id="' + esc(n.id) + '" data-type="' + esc(n.object_type || n.entity_type) + '" data-oid="' + esc(n.object_id || n.entity_id) + '"' +
      ' style="border-bottom:1px solid var(--color-border,#eee);padding:8px 2px;' + (unread ? 'background:rgba(0,100,200,0.05)' : '') + '">' +
      '<div style="font-size:12px;color:var(--color-text-primary)">' + esc(_notifText(n)) + '</div>' +
      '<div style="font-size:10px;color:var(--color-text-tertiary);margin-top:2px">' + esc(fmtDate(n.created_at)) + '</div>' +
      (canAdopt ? '<button class="vg-adopt-btn" style="margin-top:6px;background:var(--color-text-success,#2a7);border:none;color:#fff;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:12px">' + esc(_adoptLabel(n.object_type)) + '</button>' : '') +
      '</div>';
  }

  function _bindInboxItems() {
    var nodes = document.querySelectorAll("#vg-inbox-list .vg-notif");
    Array.prototype.forEach.call(nodes, function (node) {
      var id = node.getAttribute("data-id");
      var objectType = node.getAttribute("data-type");
      var objectId = node.getAttribute("data-oid");
      // クリックで既読
      node.addEventListener("click", function (e) {
        if (e.target && e.target.className && String(e.target.className).indexOf("vg-adopt-btn") >= 0) return;
        apiFetch("/admin/notifications/" + encodeURIComponent(id) + "/read", { method: "POST" })
          .then(function () { _refreshInbox(); });
      });
      var adoptBtn = node.querySelector(".vg-adopt-btn");
      if (adoptBtn) adoptBtn.addEventListener("click", function () {
        adoptBtn.disabled = true;
        adoptBtn.textContent = "取り込み中...";
        _adopt(objectType, objectId).then(function (ok) {
          var doneLabel = objectType === "document" ? "確認済み" : "取り込み済み";
          adoptBtn.textContent = ok ? doneLabel : "再読込してください";
          // 取り込みに失敗したら未読のまま残す（成功時のみ既読化する）。
          if (ok) {
            apiFetch("/admin/notifications/" + encodeURIComponent(id) + "/read", { method: "POST" });
          }
          _refreshInbox(); _loadInboxList();
        });
      });
    });
  }

  function _adopt(objectType, objectId) {
    var base = "/admin/shared/" + encodeURIComponent(objectType) + "/" + encodeURIComponent(objectId);
    // 現在のピンを expected として楽観ロックで取り込む
    return apiFetch(base + "/version-state").then(json).then(function (st) {
      var expected = st && st.pinned_release_id ? st.pinned_release_id : null;
      return apiFetch(base + "/subscription/adopt", {
        method: "POST", body: JSON.stringify({ expected_pinned_release_id: expected })
      }).then(function (res) { return res.ok; });
    }).catch(function () { return false; });
  }

  function init(d) {
    d = d || {};
    deps.apiFetch = d.apiFetch || window.apiFetch;
    deps.state = d.state || window.state;
  }

  window.Versioning = { init: init, openModal: openModal, initInbox: initInbox };
})();
