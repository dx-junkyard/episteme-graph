/* 分野の地図 — 修正報告フロー (Issue D: D-1 / D-3 学習者側)
 *
 * atlas-panel.js (Issue C) が発火する "atlas:reportrequest" イベントを購読し、
 * 詳細パネル内にインラインフォームをトグル表示 → POST /api/atlas/report で送信する。
 *
 * 守るべき設計原則 (仕様書 §7 / issue D):
 * - 送信は帰属つき(匿名不可)。フォームに匿名オプションは置かず、送信ボタンの
 *   近傍に帰属つきであることを明示する
 * - 対象(node_id | region_id)・レベル・骨格バージョンは選択イベントから自動添付する
 * - 空文字送信のガード(「(内容未記入)」は本実装では送信不可)
 * - 送信後トースト「修正報告を記録しました」→ フォームを閉じてパネルへ復帰
 *   (オーバーレイは閉じない)
 * - D-3: 採用/見送りの結果を報告者本人に通知する(未読分を取得し、確認で既読化)
 */
(() => {
  "use strict";

  const FORM_ID = "atlas-report-form";

  const state = {
    current: null, // 直近の reportrequest detail {node_id, level, skeleton_version, ...}
  };

  // -------------------------------------------------------------------
  // 認証つき fetch (app.js と同じトークン保管場所を読む)
  // -------------------------------------------------------------------

  function authedFetch(path, opts) {
    const headers = Object.assign(
      { "Content-Type": "application/json" },
      (opts && opts.headers) || {}
    );
    const token = localStorage.getItem("eg_token");
    if (token) headers["Authorization"] = "Bearer " + token;
    return fetch("/api" + path, Object.assign({}, opts, { headers }));
  }

  // -------------------------------------------------------------------
  // トースト (オーバーレイ内・自動消滅)
  // -------------------------------------------------------------------

  function showToast(message) {
    const host =
      document.querySelector("#atlas-overlay .atlas-sheet") || document.body;
    let toast = host.querySelector(".atlas-toast");
    if (!toast) {
      toast = document.createElement("div");
      toast.className = "atlas-toast";
      toast.setAttribute("role", "status");
      host.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => toast.classList.remove("show"), 3200);
  }

  // -------------------------------------------------------------------
  // D-1: 対象の分類 (node か region か) と自動添付メタ
  // -------------------------------------------------------------------

  // 骨格の領域 id 集合 (levels[*].regions)。ここに載る id は region_id として送る。
  function isRegionTarget(id) {
    const data = window.AtlasOverlay && window.AtlasOverlay.data;
    if (!data || !data.levels) return false;
    return Object.keys(data.levels).some((lv) =>
      ((data.levels[lv] || {}).regions || []).some((r) => r.id === id)
    );
  }

  function reportPayload(detail, text) {
    const data = window.AtlasOverlay && window.AtlasOverlay.data;
    const isRegion = isRegionTarget(detail.node_id);
    return {
      text: text,
      node_id: isRegion ? "" : detail.node_id,
      region_id: isRegion ? detail.node_id : "",
      level: detail.level,
      skeleton_version: detail.skeleton_version || "",
      cartridge_id: (data && data.cartridge) || "",
      node_label: detail.node_label || "",
    };
  }

  // -------------------------------------------------------------------
  // D-1: インラインフォーム (トグル表示)
  // -------------------------------------------------------------------

  function removeForm() {
    const form = document.getElementById(FORM_ID);
    if (form) form.remove();
  }

  function buildForm(detail) {
    const form = document.createElement("form");
    form.id = FORM_ID;
    form.className = "atlas-report-form";

    const textarea = document.createElement("textarea");
    textarea.className = "atlas-report-text";
    textarea.rows = 3;
    textarea.placeholder = "この配置・状態のどこが実際と違うか";
    textarea.setAttribute("aria-label", "修正報告の内容");
    form.appendChild(textarea);

    // 自動添付メタの明示 (どの版への指摘かを固定する)
    const meta = document.createElement("p");
    meta.className = "atlas-report-meta";
    meta.textContent =
      "対象: " + (detail.node_label || detail.node_id) +
      " ／ レベル" + detail.level +
      " ／ 骨格 " + (detail.skeleton_version || "—") + " に自動添付";
    form.appendChild(meta);

    const row = document.createElement("div");
    row.className = "atlas-report-actions";

    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "atlas-act-btn atlas-report-submit";
    submit.textContent = "帰属つきで送信 ↗";
    row.appendChild(submit);

    // 帰属の明示 (匿名オプションは置かない)
    const attribution = document.createElement("span");
    attribution.className = "atlas-report-attribution";
    attribution.textContent = "あなたの名前とともに記録されます（匿名にはできません）";
    row.appendChild(attribution);
    form.appendChild(row);

    const status = document.createElement("p");
    status.className = "atlas-report-status";
    status.setAttribute("role", "alert");
    form.appendChild(status);

    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const text = textarea.value.trim();
      if (!text) {
        // 空文字送信のガード (内容未記入では送信不可)
        status.textContent = "内容が未記入です。どこが実際と違うかを書いてください。";
        textarea.focus();
        return;
      }
      submit.disabled = true;
      status.textContent = "送信中...";
      authedFetch("/atlas/report", {
        method: "POST",
        body: JSON.stringify(reportPayload(detail, text)),
      })
        .then((res) =>
          res.json().catch(() => ({})).then((body) => {
            if (!res.ok) {
              const detailMsg = body && body.detail;
              const errors = detailMsg && detailMsg.errors;
              throw new Error(
                (errors && errors.join(" / ")) ||
                  (typeof detailMsg === "string" ? detailMsg : "HTTP " + res.status)
              );
            }
            return body;
          })
        )
        .then(() => {
          // フォームを閉じてパネルへ復帰 (オーバーレイは閉じない)
          removeForm();
          showToast("修正報告を記録しました");
        })
        .catch((err) => {
          submit.disabled = false;
          status.textContent = "送信に失敗しました: " + err.message;
        });
    });
    return form;
  }

  function toggleForm(detail) {
    const body = document.getElementById("atlas-panel-body");
    if (!body) return;
    const existing = document.getElementById(FORM_ID);
    if (existing) {
      // 同じノードで再度 [修正を報告] → トグルで閉じる
      existing.remove();
      if (state.current && state.current.node_id === detail.node_id) return;
    }
    state.current = detail;
    const form = buildForm(detail);
    body.appendChild(form);
    form.querySelector("textarea").focus();
  }

  document.addEventListener("atlas:reportrequest", (e) => toggleForm(e.detail));
  // ノード選択が変わるとパネルが再描画されフォームは消える。内部状態も揃えておく
  document.addEventListener("atlas:nodeselect", (e) => {
    if (state.current && state.current.node_id !== e.detail.node_id) state.current = null;
  });

  // -------------------------------------------------------------------
  // D-3: 採用/見送りの結果通知 (報告者本人のみ・確認で既読化)
  // -------------------------------------------------------------------

  function resultMessage(report) {
    if (report.status === "accepted") {
      return report.applied_version
        ? "地図の修正報告が採用され、骨格 " + report.applied_version + " に反映されました"
        : "地図の修正報告が採用されました（次版で反映予定）";
    }
    if (report.status === "declined") {
      return (
        "地図の修正報告は見送りになりました" +
        (report.resolution_note ? " — 理由: " + report.resolution_note : "")
      );
    }
    if (report.status === "merged") {
      return "地図の修正報告は同様の報告と統合されました";
    }
    return "地図の修正報告が処理されました";
  }

  function renderNotifications(reports) {
    if (!reports.length) return;
    let host = document.getElementById("atlas-report-notices");
    if (!host) {
      host = document.createElement("div");
      host.id = "atlas-report-notices";
      host.setAttribute("aria-label", "地図の修正報告の結果通知");
      document.body.appendChild(host);
    }
    reports.forEach((report) => {
      const card = document.createElement("div");
      card.className = "atlas-report-notice";
      const p = document.createElement("p");
      const target = report.node_label || report.node_id || report.region_id;
      p.textContent = resultMessage(report) + (target ? "（対象: " + target + "）" : "");
      card.appendChild(p);
      const ok = document.createElement("button");
      ok.type = "button";
      ok.textContent = "確認";
      ok.addEventListener("click", () => {
        ok.disabled = true;
        authedFetch("/atlas/reports/" + report.id + "/ack", { method: "POST" })
          .then(() => card.remove())
          .catch(() => {
            ok.disabled = false;
          });
      });
      card.appendChild(ok);
      host.appendChild(card);
    });
  }

  function checkReportResults() {
    if (!localStorage.getItem("eg_token")) return;
    authedFetch("/atlas/reports/mine?unacked=true", { method: "GET" })
      .then((res) => (res.ok ? res.json() : { reports: [] }))
      .then((data) => renderNotifications(data.reports || []))
      .catch(() => { /* 通知は次回ログイン時に再試行される */ });
  }

  document.addEventListener("DOMContentLoaded", checkReportResults);

  // テスト用の公開 (挙動は変えない)
  window.AtlasReport = { isRegionTarget, reportPayload, resultMessage, checkReportResults };
})();
