/* 主権台帳 v1「わたしの記録」パネル（Phase 1 フロントエンド）。
 *
 * 設計の正本: docs/features/trace_registry_sovereignty_ledger_design.md §3.1/§3.4。
 * 参照する不変条項:
 *   TR4 台帳は読み取り専用・本人のみ（このファイルの fetch は GET のみ。行削除・
 *       封印の操作は存在しない — 偽のボタンを置かない）
 *   TR5 来歴は誠実に（provenance_note・publicity はサーバの事実文をそのまま表示する。
 *       推定で補わない）
 *   TR6 数値を見せない（件数バッジ・進捗率の類は一切描かない）
 *   TR7 台帳の表示はステアリングに使わない（表示専用。他機能への入力にしない）
 *
 * personal-map-home.js と同型のオーバーレイ（#my-records-overlay。pm-home-* の
 * 既存スタイルを再利用する）。ポーリング禁止・開いたときのみフェッチ。
 * キャッシュはしない — 開くたびにサーバから取り直す（台帳は常に正本の写し）。
 *
 * 公開契約 window.MyRecords（呼び出し側は app.js。名前・引数は固定）:
 *   init(deps) — 依存の受け口（現状 deps なしで動作する）
 *   open()     — パネルを開く（GET /api/me/records を1回フェッチ）
 *   close()    — パネルを閉じる
 */
(() => {
  "use strict";

  const API_BASE = "/api";
  const OVERLAY_ID = "my-records-overlay";

  // 常設注記（わたしの地図と同文。設計 §3.4）。
  const PRIVACY_NOTE = "この記録はあなたにだけ表示されます。成績評価には使用されません。";

  // 封印は v2 の専用設計を経る（設計 §4）。偽のボタンを置かず、現物の事実だけを常設する
  // （第 23 波 F4: 「設計中です」という設計メモ調の約束を画面に出さない）。
  const SEAL_NOTE = "この画面でできるのは、一覧を見ることと持ち出すことです。記録の内容を読めなくする（封印する）操作はありません。";
  // 第 23 波 F4（P-0008）: 中身の無い系統を見出しごと並べず、末尾の1行に畳む。
  const EMPTY_SYSTEMS_PREFIX = "まだ記録の無い種類: ";

  const state = {
    deps: {},
    overlayEl: null,
    contentEl: null,
    exportBtnEl: null,
    exportNoteEl: null,
    lastFocus: null,
    fetchSeq: 0, // 開き直しの競合対策（古い応答で新しい表示を上書きしない）
  };

  // -------------------------------------------------------------------
  // 認証・取得（personal-map-home.js と同じ様式。fail-closed）
  // -------------------------------------------------------------------

  function token() {
    try {
      return localStorage.getItem("eg_token") || null;
    } catch (e) {
      return null;
    }
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fmtDate(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    return d.getFullYear() + "年" + (d.getMonth() + 1) + "月" + d.getDate() + "日";
  }

  // 本人の全記録（GET のみ・キャッシュしない）。401/失敗はすべて null に丸め、
  // 呼び出し側が「いまは表示できません。」に縮退できるようにする（fail-closed）。
  function loadRecords() {
    const t = token();
    if (!t) return Promise.resolve(null);
    return fetch(API_BASE + "/me/records", {
      headers: { Authorization: "Bearer " + t },
    })
      .then((res) => {
        if (!res.ok) throw new Error("my-records " + res.status);
        return res.json();
      })
      .catch(() => null);
  }

  // -------------------------------------------------------------------
  // 表示（系統別セクション + 行カード。件数・数値は描かない TR6）
  // -------------------------------------------------------------------

  function failHtml() {
    return '<p class="pm-home-fail my-records-fail">いまは表示できません。</p>';
  }

  function loadingHtml() {
    return '<p class="pm-home-loading my-records-loading">読み込み中…</p>';
  }

  // 第 23 波・第 2 波 V4': 行の公表文が系統の見出しの下の文に含まれるなら行には繰り返さない
  // （見出しの下に 1 回だけ）。行ごとに違う文（AI の候補など）だけを行に出す。
  function rowPublicityDiffers(publicity, systemNote) {
    if (!publicity) return false;
    if (!systemNote) return true;
    return systemNote.indexOf(publicity) === -1;
  }

  // 行の階層: 本文（1 段目）→ 日付・コース（2 段目）→ 状態チップと補足（3 段目）。
  function itemHtml(item, systemNote) {
    const flags = (item && item.flags) || {};
    let cls = "pm-home-node-row my-records-item";
    if (flags.superseded) cls += " my-records-item-superseded";
    if (flags.candidate) cls += " my-records-item-candidate";
    if (flags.dismissed) cls += " my-records-item-dismissed";
    let html = '<div class="' + cls + '">';
    html += '<div class="pm-home-node-top">';
    html += '<span class="pm-home-node-label my-records-item-text">' + esc(item.text || "") + "</span>";
    html += "</div>";
    const metaParts = [];
    const date = fmtDate(item.created_at);
    if (date) metaParts.push(date);
    if (item.course_label) metaParts.push(item.course_label);
    if (item.context_label) metaParts.push(item.context_label);
    if (metaParts.length) {
      html += '<div class="pm-home-node-ctx my-records-item-meta">' + esc(metaParts.join(" · ")) + "</div>";
    }
    const stateParts = [];
    if (item.status_label) {
      stateParts.push('<span class="pm-home-node-kind my-records-status-chip">' + esc(item.status_label) + "</span>");
    }
    if (flags.map_excluded) {
      stateParts.push('<span class="pm-home-node-ctx my-records-item-flag">地図には出していません</span>');
    }
    if (rowPublicityDiffers(item.publicity, systemNote)) {
      stateParts.push('<span class="pm-home-node-ctx my-records-item-publicity">' + esc(item.publicity) + "</span>");
    }
    if (stateParts.length) {
      html += '<div class="my-records-item-state">' + stateParts.join("") + "</div>";
    }
    html += "</div>";
    return html;
  }

  function systemHtml(sys) {
    let html = '<div class="my-records-system">';
    html += '<div class="pm-home-related-heading my-records-system-heading">' + esc(sys.label || "") + "</div>";
    if (sys.publicity_note) {
      html += '<div class="pm-home-node-ctx my-records-system-note">' + esc(sys.publicity_note) + "</div>";
    }
    (sys.items || []).forEach((item) => {
      html += itemHtml(item, sys.publicity_note || "");
    });
    html += "</div>";
    return html;
  }

  function recordsHtml(data) {
    let html = "";
    const systems = (data && data.systems) || [];
    if (!systems.length) {
      html += '<p class="pm-home-empty my-records-empty">まだ記録がありません。学習の中で問いを残すと、ここに現れます。</p>';
    }
    // 中身のある系統だけを見出しごと出し、空の系統は名前を1行に畳む（P-0008）。
    const emptyLabels = [];
    systems.forEach((sys) => {
      if ((sys.items || []).length) html += systemHtml(sys);
      else if (sys.label) emptyLabels.push(sys.label);
    });
    if (emptyLabels.length && emptyLabels.length < systems.length) {
      html += '<p class="pm-home-node-ctx my-records-empty-systems">' +
        esc(EMPTY_SYSTEMS_PREFIX + emptyLabels.join("・")) + "</p>";
    } else if (systems.length && emptyLabels.length === systems.length) {
      html += '<p class="pm-home-empty my-records-empty">まだ記録がありません。学習の中で問いを残すと、ここに現れます。</p>';
    }
    if (data && data.truncated) {
      // 省略の事実だけを言う（TR5。件数は出さない TR6。「すべて」と断言しない —
      // 持ち出しにも読み出し上限があり、ごく大量の記録では上限までになる）。
      html += '<p class="pm-home-node-ctx my-records-truncated">表示は最新分のみです。持ち出しには記録が新しい順に含まれます（ごく大量の記録がある場合は上限まで）。</p>';
    }
    if (data && data.provenance_note) {
      html += '<div class="my-records-provenance">';
      html += '<div class="pm-home-related-heading my-records-provenance-heading">匿名集計に使われたかどうか（来歴）</div>';
      html += '<p class="pm-home-node-ctx my-records-provenance-note">' + esc(data.provenance_note) + "</p>";
      html += "</div>";
    }
    return html;
  }

  // -------------------------------------------------------------------
  // 持ち出し（Blob ダウンロード。admin-discuss-observation.js の doDownload と
  // 同じ流儀: fetch + blob + <a download>。台帳側の DB は変更しない TR4）
  // -------------------------------------------------------------------

  function setExportNote(text) {
    const el = state.exportNoteEl;
    if (!el) return;
    el.textContent = text || "";
    el.hidden = !text;
  }

  function doExport() {
    const t = token();
    if (!t) return;
    const btn = state.exportBtnEl;
    if (btn) btn.disabled = true;
    setExportNote("持ち出し用のデータを準備しています…");

    fetch(API_BASE + "/me/records/export", {
      headers: { Authorization: "Bearer " + t },
    })
      .then((res) => {
        if (!res.ok) throw new Error("export " + res.status);
        const cd = res.headers.get("Content-Disposition") || "";
        const match = cd.match(/filename="?([^";\n]+)"?/);
        const filename = match ? match[1] : "my-records.json";
        return res.blob().then((blob) => {
          const url = URL.createObjectURL(blob);
          const a = document.createElement("a");
          a.href = url;
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          setTimeout(() => {
            URL.revokeObjectURL(url);
            a.remove();
          }, 1000);
        });
      })
      .then(() => {
        setExportNote("ダウンロードを開始しました。");
      })
      .catch(() => {
        setExportNote("持ち出しに失敗しました。");
      })
      .then(() => {
        if (btn) btn.disabled = false;
      });
  }

  // -------------------------------------------------------------------
  // オーバーレイの構築（一度だけ。イベントはオーバーレイ1本への委譲）
  // -------------------------------------------------------------------

  function onOverlayClick(e) {
    const closeBtn = e.target.closest(".my-records-close-btn");
    if (closeBtn) {
      close();
      return;
    }
    const exportBtn = e.target.closest(".my-records-export-btn");
    if (exportBtn) {
      doExport();
    }
  }

  function ensureOverlay() {
    if (state.overlayEl) return state.overlayEl;

    const overlay = document.createElement("div");
    overlay.id = OVERLAY_ID;
    overlay.className = "pm-home-overlay my-records-overlay";
    overlay.hidden = true;
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-label", "わたしの記録");

    const panel = document.createElement("div");
    panel.className = "pm-home-panel my-records-panel";

    const header = document.createElement("div");
    header.className = "pm-home-header";
    header.innerHTML =
      '<span class="pm-home-title">わたしの記録</span>' +
      '<button type="button" class="pm-home-close-btn my-records-close-btn" aria-label="閉じる">×</button>';
    panel.appendChild(header);

    // 常設注記（ヘッダ直後・textContent で固定表示。わたしの地図と同型）。
    const note = document.createElement("div");
    note.className = "pm-home-note";
    note.textContent = PRIVACY_NOTE;
    panel.appendChild(note);

    // 第 23 波・第 2 波 V6: 「入力した文がどこへ送られるか」を 1 操作で確かめられる場所として、
    // 学習画面の入力欄の上と同じ事実文をここにも 1 回置く（文言はサーバの正本。
    // disclosure-note.js の mount が埋める・取れなければ何も描かない）。
    const disclosure = document.createElement("div");
    disclosure.className = "my-records-disclosure";
    disclosure.setAttribute("data-disclosure-note", "learning_chat");
    panel.appendChild(disclosure);
    if (window.DisclosureNote) window.DisclosureNote.mount(disclosure, "learning_chat");

    // 持ち出し（台帳の唯一の操作。読み取り専用のダウンロードで DB は変更しない）。
    const actions = document.createElement("div");
    actions.className = "my-records-actions";
    const exportBtn = document.createElement("button");
    exportBtn.type = "button";
    exportBtn.className = "pm-home-journey-btn my-records-export-btn";
    exportBtn.textContent = "持ち出す";
    exportBtn.title = "自分の記録一式を JSON で保存します";
    actions.appendChild(exportBtn);
    const exportNote = document.createElement("span");
    exportNote.className = "pm-home-node-ctx my-records-export-note";
    exportNote.hidden = true;
    actions.appendChild(exportNote);
    panel.appendChild(actions);

    const content = document.createElement("div");
    content.className = "pm-home-content my-records-content";
    panel.appendChild(content);

    // パネル末尾の常設事実文（封印は v2 — ボタンにしない。TR5）。
    const seal = document.createElement("div");
    seal.className = "pm-home-note my-records-seal-note";
    seal.textContent = SEAL_NOTE;
    panel.appendChild(seal);

    overlay.appendChild(panel);
    overlay.addEventListener("click", onOverlayClick);
    document.body.appendChild(overlay);

    state.overlayEl = overlay;
    state.contentEl = content;
    state.exportBtnEl = exportBtn;
    state.exportNoteEl = exportNote;
    return overlay;
  }

  // -------------------------------------------------------------------
  // 公開 API
  // -------------------------------------------------------------------

  function init(deps) {
    state.deps = deps || {};
  }

  function open() {
    const t = token();
    if (!t) return; // fail-closed: 未ログインでは開かない
    ensureOverlay();
    state.lastFocus = document.activeElement;
    state.overlayEl.hidden = false;
    setExportNote("");
    if (state.contentEl) state.contentEl.innerHTML = loadingHtml();
    const seq = ++state.fetchSeq;
    loadRecords().then((data) => {
      if (seq !== state.fetchSeq) return; // 開き直し後の古い応答は捨てる
      if (!state.contentEl) return;
      state.contentEl.innerHTML = data ? recordsHtml(data) : failHtml();
    });
  }

  function close() {
    if (!state.overlayEl) return;
    state.overlayEl.hidden = true;
    if (state.lastFocus && typeof state.lastFocus.focus === "function") state.lastFocus.focus();
  }

  window.MyRecords = {
    init,
    open,
    close,
  };
})();
