/*
 * 人に見せる名前の登録（IK-0848 / IK-1007・第 25 周 第 5 波・migration 094）。ES5。
 *
 * 学習画面（index.html の topbar.profile-name）と管理画面（admin.html の header.profile-name）の
 * 「👤 名前」ボタンから、本人の users.profile_name を登録・変更・消去する。
 *
 * - ログインに使うユーザー名は扱わない。共有先に出るのはこの名前だけで、
 *   空なら「名前の登録がありません」（サーバの文言をそのまま出す — 文言をここに焼き込まない）。
 * - 取得はボタンを押したときだけ（ポーリングしない）。
 * - トークンは学習・管理の両画面が共有する localStorage の eg_token を読む。
 */
(function () {
  "use strict";

  var ENDPOINT = "/api/me/profile";

  function token() {
    try {
      return window.localStorage.getItem("eg_token") || "";
    } catch (e) {
      return "";
    }
  }

  function request(method, body) {
    var headers = { "Authorization": "Bearer " + token() };
    var init = { method: method, headers: headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    return fetch(ENDPOINT, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var detail = (data && typeof data.detail === "string") ? data.detail : "名前を保存できませんでした。";
          throw new Error(detail);
        }
        return data;
      });
    });
  }

  function edit(button) {
    if (!token()) {
      window.alert("ログインしてから押してください。");
      return;
    }
    request("GET").then(function (profile) {
      var current = (profile && profile.profile_name) || "";
      var note = (profile && profile.note) || "";
      var input = window.prompt(
        "ほかの人に見せる名前（空にすると登録を消します）\n" + note,
        current
      );
      if (input === null) return null;
      return request("PATCH", { profile_name: input }).then(function (saved) {
        if (button && saved && saved.label) button.title = "共有先に出る名前: " + saved.label;
        if (saved && saved.message) window.alert(saved.message);
      });
    }).catch(function (err) {
      window.alert((err && err.message) || "名前を保存できませんでした。");
    });
  }

  function bind(root) {
    var buttons = (root || document).querySelectorAll("[data-profile-name-btn]");
    for (var i = 0; i < buttons.length; i++) {
      (function (btn) {
        if (btn.getAttribute("data-profile-name-bound") === "1") return;
        btn.setAttribute("data-profile-name-bound", "1");
        btn.addEventListener("click", function () { edit(btn); });
      })(buttons[i]);
    }
  }

  window.ProfileName = { bind: bind, edit: edit };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { bind(document); });
  } else {
    bind(document);
  }
})();
