"""分野マップ — コースバインディングの「該当なし」UX とドメインライフサイクルの静的回帰テスト。

正本: docs/features/atlas_binding_lifecycle_design.md（AB1〜AB8、特に §8 フロントエンド）。
バックエンド API（propose の domains_checked/retired_skipped/atlas_binding_pending・
pending PUT/DELETE・retire/restore・freeze-impact 等）は別エージェントが並行実装しており、
本ファイルはフロントエンド (frontend/public/js/admin.js, versioning.js, admin.html) の
静的検証にとどめる（既存 test_atlas_operations_ux.py と同じ方式）。
"""

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_binding_editor_does_not_fall_back_to_first_proposal():
    """§2.1: 0一致でも先頭ドメインへ fallback しない。既定は「バインドしない」。"""
    js = read("frontend/public/js/admin.js")
    assert "proposals[0].domain_key" not in js
    assert 'var initial = data.recommended || data.current_cartridge_id || "";' in js


def test_binding_editor_shows_no_match_fact_and_three_exits():
    """§2.1/2.2: 該当なしの事実文（N分野を照合）+ 3つの出口（手動対応・バインドしない・新分野作成）。"""
    js = read("frontend/public/js/admin.js")
    assert "var noMatch = !data.recommended && !data.current_cartridge_id;" in js
    assert "対応する分野マップは見つかりませんでした" in js
    assert "分野を照合" in js
    assert "domains_checked" in js
    assert "retired_skipped" in js
    assert "廃止済み" in js and "照合対象外" in js
    # 出口2: 今はバインドしなくても『次にやること』からいつでも再開できる（督促文にしない）
    assert "今はバインドしなくても" in js and "次にやること" in js
    # 出口3: このコースから新しい分野マップを作る
    assert "このコースから新しい分野マップを作る" in js


def test_binding_editor_shows_pending_note_with_cancel():
    """§2.3: 凍結待ちの仮予約の表示 + 取り消しボタン（DELETE）。"""
    js = read("frontend/public/js/admin.js")
    assert "data.atlas_binding_pending" in js
    assert "の骨格の凍結待ちです" in js
    assert "ab-pending-cancel" in js
    assert '"/atlas-binding/pending", { method: "DELETE" }' in js


def test_binding_editor_new_domain_flow_calls_pending_then_generate():
    """§2.3: 新分野作成ミニフォームが pending PUT → skeleton/generate の順で呼ぶこと。"""
    js = read("frontend/public/js/admin.js")
    assert "ab-new-domain-toggle" in js
    assert "ab-new-domain-key" in js
    assert "ab-new-domain-name" in js
    assert "ab-new-domain-desc" in js
    assert '/^[a-z0-9_]+$/' in js
    # 仮予約 PUT (domain_key のみ送る) の一意な目印
    pending_put_idx = js.index("body: JSON.stringify({ domain_key: key })")
    generate_idx = js.index("/atlas/skeleton/generate")
    assert pending_put_idx < generate_idx, "仮予約 (PUT pending) は generate 呼び出しより前に実行されること"
    assert "atlas-binding/pending" in js and 'method: "PUT"' in js
    assert "下書きの生成に失敗しました（仮予約は保存済み。分野の地図タブから再生成できます）" in js
    assert "下書きを生成しました。『分野の地図』タブでレビュー・凍結してください。" in js


def test_binding_editor_options_param_threads_course_title_and_description():
    """options 引数（courseTitle/courseDescription）を受け取り、コース承認直後の呼び出しから渡すこと。"""
    js = read("frontend/public/js/admin.js")
    assert "function atlasBindingRenderEditor(bodyEl, statusEl, courseId, data, onSaved, options)" in js
    assert "function atlasBindingPropose(courseId, bodyEl, statusEl, onSaved, options)" in js
    assert "options.courseTitle" in js
    assert "options.courseDescription" in js
    assert "{ courseTitle: draft.title || \"\", courseDescription: draft.description || \"\" }" in js


def test_binding_editor_confirms_zero_match_save():
    """§2.4: 0一致のまま明示バインドを保存しようとしたときだけ window.confirm で確認する。"""
    js = read("frontend/public/js/admin.js")
    assert "domainSelect.value && !anyBound" in js
    assert "window.confirm(" in js
    assert "足がかり" in js


def test_atlas_domain_lifecycle_ui_present():
    """§3.1/§8.2: retired 表示・retire/restore ボタン・retired 中の編集ボタン無効化。"""
    html = read("frontend/public/admin.html")
    js = read("frontend/public/js/admin.js")
    assert 'id="atlas-domain-retire"' in html
    assert 'id="atlas-domain-restore"' in html
    assert 'id="atlas-domain-retired-note"' in html
    assert "domainLifecycles[d.domain_key] = d.lifecycle" in js
    assert "（廃止済み）" in js
    assert '"/atlas/retire"' in js
    assert '"/atlas/restore"' in js
    assert "function updateLifecycleUI()" in js
    # retired 中は生成・保存・凍結ボタンを無効化する
    assert "generateBtn.disabled = isRetired" in js
    assert "saveDraftBtn.disabled = isRetired" in js
    assert "freezeBtnEl.disabled = isRetired" in js


def test_freeze_checks_impact_before_confirming():
    """§3.2: 凍結実行前に freeze-impact を取得し、影響があれば事実文で確認する。

    実行時の前後関係は Promise チェーンで決まる（凍結本体は openFreezeChecklist()
    に包まれ、freeze-impact 取得の .then / .catch 両方から呼ばれる）ため、ソース上の
    行順ではなく「関数定義 → 呼び出し」の構造で検証する。
    """
    js = read("frontend/public/js/admin.js")
    assert "/atlas/freeze-impact" in js
    assert "function openFreezeChecklist()" in js
    definition_idx = js.index("function openFreezeChecklist()")
    freeze_post_idx = js.index('basePath() + "/freeze"')
    assert definition_idx < freeze_post_idx, "凍結 POST は openFreezeChecklist() 関数の内側にあること"

    freeze_impact_idx = js.index("/atlas/freeze-impact")
    call_sites = [m.start() for m in re.finditer(r"openFreezeChecklist\(\);", js)]
    assert len(call_sites) >= 2, "openFreezeChecklist() は影響なし経路と best-effort 失敗経路の双方から呼ばれること"
    assert all(freeze_impact_idx < c for c in call_sites), (
        "openFreezeChecklist() の呼び出しは freeze-impact 取得より後（その .then/.catch 内）であること"
    )
    assert "removed_node_ids" in js
    assert "affected_courses" in js
    assert "概念が削除され" in js and "コースの対応が外れます" in js


def test_notification_labels_added_for_atlas_lifecycle_events():
    """§3.4/§8.3: 通知インボックスに atlas_skeleton_frozen / atlas_domain_retired のラベルを追加。"""
    js = read("frontend/public/js/versioning.js")
    assert 'n.kind === "atlas_skeleton_frozen"' in js
    assert 'n.kind === "atlas_domain_retired"' in js
    assert "が凍結されました" in js
    assert "が廃止されました" in js


def test_new_domain_success_handler_focuses_atlas_domain_not_just_activates_tab():
    """コードレビュー指摘1: 新分野生成後の「分野の地図タブを開く」導線が機能しない。

    activateTabView("atlas") 単独では initAtlas の onTabActivate コールバック
    （loadCartridges/loadAtlasCourses）が発火しないため、atlasAdminFocusDomain
    経由で新分野へフォーカスしてタブ内一覧・状態を明示的に読み込むこと。
    """
    js = read("frontend/public/js/admin.js")
    assert "var atlasAdminFocusDomain = null;" in js
    assert "atlasAdminFocusDomain = focusDomain;" in js
    assert "function focusDomain(domainKey)" in js

    # 成功ハンドラの「分野の地図タブを開く」クリックが activateTabView("atlas") だけで
    # 終わらないこと（focusDomain 呼び出しか、その未初期化フォールバックのタブクリックが続くこと）。
    handler_idx = js.index("if (typeof activateTabView === \"function\") activateTabView(\"atlas\");")
    tail = js[handler_idx:handler_idx + 600]
    assert "atlasAdminFocusDomain(key)" in tail
    assert '.admin-tab[data-tab="atlas"]' in tail


def test_focus_domain_resets_and_reloads_cartridge_list():
    """focusDomain は一覧を取り直し（cartridgesLoaded リセット）、対象キーを選択すること。"""
    js = read("frontend/public/js/admin.js")
    definition_idx = js.index("function focusDomain(domainKey)")
    body = js[definition_idx:definition_idx + 600]
    assert "pendingFocusKey = domainKey;" in body
    assert "cartridgesLoaded = false;" in body
    assert "loadCartridges();" in body
    assert "loadAtlasCourses();" in body
    assert "if (pendingFocusKey && keys[pendingFocusKey]) {" in js


def test_binding_editor_warns_when_current_binding_missing_from_candidates():
    """コードレビュー指摘2: 現行バインドが候補にない（廃止済み等）場合、無言で解除しない。

    事実文で状態を明示し、保存しない限り現状維持であることを伝える。
    「（バインドしない）」を選んで保存する場合のみ明示 confirm を挟む。
    """
    js = read("frontend/public/js/admin.js")
    assert "var currentMissing = !!(currentKey && !byKey[currentKey]);" in js
    assert "ab-current-missing-note" in js
    assert "は候補にありません" in js
    assert "は廃止済みのため、候補には表示されません" in js
    assert "保存しない限り現在のバインドは維持されます" in js
    assert "学習者の地図表示は変わりません" in js

    # 解除 confirm は 0一致確認 (domainSelect.value && !anyBound) とは独立した分岐であること。
    assert "if (currentMissing && !domainSelect.value) {" in js
    assert "とトピックの対応をすべて解除します" in js


def test_init_atlas_binding_threads_course_title_to_propose():
    """コードレビュー指摘3: 学習マップ編集タブの propose もコース名/説明を prefill すること。"""
    js = read("frontend/public/js/admin.js")
    definition_idx = js.index("function initAtlasBinding()")
    end_idx = js.index("\n  }\n", definition_idx)
    body = js[definition_idx:end_idx]
    assert "var courseMeta = {};" in body
    assert "courseMeta[c.id] = { title: c.title || \"\", description: c.description || \"\" };" in body
    assert "atlasBindingPropose(select.value, bodyEl, statusEl, null, {" in body
    assert "courseTitle: meta.title" in body


def test_existing_atlas_operations_ux_still_holds():
    """既存の状態起点 UX 回帰テストが引き続き成立する前提条件（壊していないことの粗い確認）。"""
    html = read("frontend/public/admin.html")
    js = read("frontend/public/js/admin.js")
    assert 'id="atlas-overview"' in html
    assert 'id="atlas-overview-action"' in js
    assert "atlas:binding-saved" in js


def test_discard_draft_button_present_and_calls_delete_endpoint():
    """コードレビュー2回目 修正1: 下書きを破棄ボタン（draft 破棄 API の UI）。

    admin.html には触れず JS 側で「次版を保存」ボタンの隣に生成すること。
    draft は作業コピーであり retired ドメインでも破棄は許可する（後始末）。
    """
    js = read("frontend/public/js/admin.js")
    assert 'discardDraftBtn.id = "atlas-discard-draft"' in js
    assert "document.createElement(\"button\")" in js
    assert "saveDraftBtnEl.parentNode.insertBefore(discardDraftBtn, saveDraftBtnEl.nextSibling)" in js
    assert '"/atlas/skeleton/draft", { method: "DELETE" }' in js
    assert "現在の下書きを破棄します。凍結済みの版と学習者の表示には影響しません。よろしいですか？" in js
    assert "破棄する下書きがありません" in js
    assert "下書きを破棄しました" in js


def test_discard_draft_button_not_disabled_by_retired_lifecycle():
    """下書き破棄は retired 中も有効のまま（updateLifecycleUI の無効化対象に含めない）。"""
    js = read("frontend/public/js/admin.js")
    definition_idx = js.index("function updateLifecycleUI()")
    end_idx = js.index("\n    }\n", definition_idx)
    body = js[definition_idx:end_idx]
    assert "atlas-discard-draft" not in body
    assert "discardDraftBtn" not in body


def test_atlas_binding_course_select_disables_non_owner_options():
    """コードレビュー2回目 修正2: 学習マップ編集のコース選択で所有者以外を操作不可にする。

    atlas-binding API（propose/save/pending）は所有者または SYSTEM_ADMIN のみ許可のため、
    viewer/editor のコースは選択肢の時点で disabled にし、理由を表示する。
    """
    js = read("frontend/public/js/admin.js")
    definition_idx = js.index("function initAtlasBinding()")
    end_idx = js.index("\n  }\n", definition_idx)
    body = js[definition_idx:end_idx]
    assert 'c.role !== "owner" && state.role !== "SYSTEM_ADMIN"' in body
    assert "opt.disabled = true;" in body
    assert "（所有者のみ操作できます）" in body
