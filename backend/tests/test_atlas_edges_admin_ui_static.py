"""関係（辺）の候補の教員フロントエンド（レビュー UI）静的ガードレール。

正本: docs/features/atlas_relation_edges_design.md
  §7  管理 UI（修正報告セクション内の第3グループ・候補カード・出所チップ・
      kind 選択・[採用] / [見送り（理由必須）] / 見送り済みフィルタ /
      [下書きへ反映]（preview → 既存 PUT → mark-incorporated の3手））
  §8  `label_vocab.EDGE_KIND_LABELS` が enum→日本語の正本（フロントはミラー規律）
  RE3 骨格を書くのは常に教員の PUT draft（辺候補の API は下書きを書かない）
  RE4 数値非表示（cosine・共起件数を出さない。近さの段階ラベルはサーバが確定する）
  RE5 判断は status 遷移のみ（見送りは理由必須・戻せる）

バックエンド（`core/atlas_edges/` / `routes/atlas_edges.py` / freeze ゲート）は
`test_atlas_edges_{core,api,guardrails}.py` が担当する。本ファイルは admin フロント
（`frontend/public/js/admin.js`）+ アンカー3点セットの静的契約のみを検証する
（`test_atlas_gaps_admin_ui_static.py` と同じ流儀。API は呼ばない）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"
ATLAS_MANUAL = ROOT / "docs" / "manual" / "teacher" / "17-admin-atlas.md"

for _p in (str(BACKEND),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import label_vocab  # noqa: E402
from core.help_kb import admin_ui_anchors as admin_anchors_mod  # noqa: E402
from core.help_kb import manual as kb_manual  # noqa: E402

EDGE_ANCHOR_IDS = (
    "atlas.edge-candidates",
    "atlas.edge-dismissed-filter",
    "atlas.edge-incorporate",
)

#: JS 側の表をパースする正規表現（test_atlas_vocab_mirror.py と同方式。Node を呼ばない）。
_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")


@pytest.fixture(autouse=True)
def _clear_manual_cache():
    kb_manual.clear_manual_cache()
    yield
    kb_manual.clear_manual_cache()


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _edge_segment() -> str:
    """admin.js に追加した関係（辺）の候補のレビュー節だけを取り出す。"""
    src = _read(ADMIN_JS)
    start = src.index("// ── 関係（辺）の候補（RE層, migration 076）")
    return src[start : src.index("    function addDomainOption(key, label) {", start)]


def _fn_block(src: str, signature: str) -> str:
    start = src.index(signature)
    return src[start : src.index("\n    }", start)]


def _strip_comment_lines(src: str) -> str:
    """行頭コメント（`// …`）を落とす。

    実装コメントは設計書の条項（「生の類似度を描画しない」等）を引用するため、
    禁止語彙の検査に混ぜると自分の禁止条項の記述で落ちる。
    """
    return "\n".join(
        line for line in src.splitlines() if not line.strip().startswith("//")
    )


def _js_table(src: str, var_name: str) -> dict[str, str]:
    marker = "var " + var_name + " = {"
    assert marker in src, "admin.js に " + var_name + " が無い"
    start = src.index(marker)
    block = src[start + len(marker) : src.index("};", start)]
    return {
        (quoted or bare): value for quoted, bare, value in _ENTRY_RE.findall(block)
    }


# ===========================================================================
# 1. セクションの定型（§7: 修正報告セクション内の第3グループ・専用タブなし）
# ===========================================================================


class TestSectionContract:
    def test_group_is_appended_into_the_reports_section(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function buildEdgesGroup() {")
        assert 'document.getElementById("atlas-reports-section")' in block
        assert 'group.id = "atlas-edges-group";' in block
        assert "section.appendChild(group);" in block

    def test_group_is_built_next_to_the_gap_group(self):
        """admin.html は変更せず、buildGapsGroup と同じ後付けパターンで生成する。"""
        src = _read(ADMIN_JS)
        assert "buildGapsGroup();" in src
        assert "buildEdgesGroup();" in src
        assert src.index("buildGapsGroup();") < src.index("buildEdgesGroup();")
        assert "atlas-edges-group" not in _read(ROOT / "frontend" / "public" / "admin.html")

    def test_group_is_not_a_new_tab(self):
        seg = _edge_segment()
        assert "onTabActivate" not in seg
        assert "data-tab" not in seg
        assert "data-atlas-section" not in seg

    def test_heading_and_intro_literals(self):
        seg = _edge_segment()
        assert 'var EDGE_GROUP_TITLE = "関係（辺）の候補";' in seg
        assert "var EDGE_GROUP_INTRO = " in seg
        intro = seg[seg.index("var EDGE_GROUP_INTRO = ") :].split("\n", 1)[0]
        assert "プロトタイプ近傍" in intro
        assert "共起" in intro
        # 採用しても凍結するまで骨格は変わらない旨を事実で書く（RE3）
        assert "公開するまで" in intro

    def test_group_carries_the_section_anchor(self):
        seg = _edge_segment()
        assert 'group.setAttribute("data-ui-anchor", "atlas.edge-candidates");' in seg

    def test_missing_skeleton_hides_the_group(self):
        """骨格なし（404）は領域ごと非表示（fail-closed。空の枠を出さない）。"""
        seg = _edge_segment()
        block = _fn_block(seg, "function loadEdgeCandidates() {")
        assert "if (res.status === 404) return null;" in block
        assert 'edgesGroupEl.style.display = "none";' in block

    def test_no_polling(self):
        seg = _edge_segment()
        assert "setInterval" not in seg
        assert "setTimeout" not in seg

    def test_reloaded_from_load_state_only(self):
        """再取得はタブ表示（loadState）と各操作の成功後だけ（RE6）。"""
        src = _read(ADMIN_JS)
        load_state = _fn_block(src, "    function loadState() {")
        assert "loadEdgeCandidates();" in load_state


# ===========================================================================
# 2. 候補カード（§7: ペア / 出所チップ / 支持論文 / kind 選択 / 版チップ）
# ===========================================================================


class TestCandidateCard:
    def test_card_titles_the_pair(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function _edgePairText(candidate) {")
        assert "candidate.from_label" in block
        assert "candidate.to_label" in block
        assert '" — "' in block

    def test_origin_chips_name_both_sources(self):
        seg = _edge_segment()
        assert 'var EDGE_ORIGIN_VECTOR_LABEL = "プロトタイプ近傍";' in seg
        assert 'var EDGE_ORIGIN_CO_OCCURRENCE_LABEL = "共起";' in seg
        block = _fn_block(seg, "function _edgeOriginChipsHtml(candidate) {")
        assert 'origin === "vector"' in block
        assert 'origin === "co_occurrence"' in block
        # 近さの言葉はサーバの nearness_label をそのまま描く（RE4）
        assert "candidate.nearness_label" in block

    def test_origin_label_marks_the_candidate_as_unconfirmed(self):
        seg = _edge_segment()
        assert 'var EDGE_ORIGIN_NOTE = "AIによる推定（未確認）";' in seg
        card = _fn_block(seg, "function _edgeCandidateCardHtml(candidate, draftExists, retired) {")
        assert "EDGE_ORIGIN_NOTE" in card

    def test_supporting_documents_are_listed_by_title(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function _edgeDocumentsHtml(candidate) {")
        assert "candidate.documents" in block
        assert "d.title" in block

    def test_decision_and_applied_version_chips(self):
        seg = _edge_segment()
        card = _fn_block(seg, "function _edgeCandidateCardHtml(candidate, draftExists, retired) {")
        assert "decision.status_label" in card
        assert "decision.applied_version" in card
        assert 'escHtml("版" + appliedVersion + "で反映済み")' in card

    def test_dismissed_rows_offer_restore(self):
        seg = _edge_segment()
        card = _fn_block(seg, "function _edgeCandidateCardHtml(candidate, draftExists, retired) {")
        assert 'data-edge-action="restore"' in card
        assert ">見送りから戻す</button>" in card

    def test_dismissed_filter_refetches_with_include_dismissed(self):
        seg = _edge_segment()
        assert 'id="atlas-edges-show-dismissed"' in seg
        assert "見送り済みも表示" in seg
        load = _fn_block(seg, "function loadEdgeCandidates() {")
        assert 'edgeIncludeDismissed ? "?include_dismissed=true" : ""' in load

    def test_kind_selection_survives_the_requeue_after_a_decision(self):
        """判断のたびにキューを読み直すため、選択途中の種類を持ち越す。"""
        seg = _edge_segment()
        assert "var edgeKindSelections = {};" in seg
        block = _fn_block(seg, "function _edgeSelectedKind(edgeKey, decision) {")
        assert "edgeKindSelections.hasOwnProperty(edgeKey)" in block
        assert "decision.edge_kind" in block
        apply_block = _fn_block(seg, "function edgeApplyIncorporation(edgeKey, preview) {")
        assert "delete edgeKindSelections[edgeKey]" in apply_block


# ===========================================================================
# 3. 辺種別の語彙ミラー（§8: 正本は core/label_vocab.py::EDGE_KIND_LABELS）
# ===========================================================================


class TestEdgeKindVocabularyMirror:
    def test_js_table_matches_the_server_canon_verbatim(self):
        table = _js_table(_read(ADMIN_JS), "EDGE_KIND_LABELS")
        assert table == dict(label_vocab.EDGE_KIND_LABELS)

    def test_mirror_comment_names_the_server_source_of_truth(self):
        seg = _edge_segment()
        head = seg[: seg.index("var EDGE_KIND_LABELS")]
        assert "label_vocab.py" in head
        assert "EDGE_KIND_LABELS" in head

    def test_select_offers_exactly_the_three_kinds(self):
        seg = _edge_segment()
        assert (
            'var EDGE_KIND_ORDER = ["adjacent", "depends", "related"];' in seg
        )
        block = _fn_block(seg, "function _edgeKindSelectHtml(edgeKey, decision, disabled) {")
        assert "EDGE_KIND_ORDER.forEach" in block
        assert "EDGE_KIND_LABELS[kind]" in block

    def test_kind_labels_are_not_re_spelled_outside_the_table(self):
        """日本語の訳語をカード側で直書きしない（表を唯一の出所にする）。"""
        seg = _edge_segment()
        after = seg[seg.index("var EDGE_KIND_ORDER") :]
        for label in label_vocab.EDGE_KIND_LABELS.values():
            assert '"' + label + '"' not in after, label


# ===========================================================================
# 4. ボタンと3手の反映フロー（§7 / RE3 / RE5）
# ===========================================================================


class TestDecisionButtons:
    def test_decide_endpoint_and_actions(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function edgeDecide(edgeKey, action, note, kind) {")
        assert 'edgesPath() + "/decide"' in block
        assert 'method: "POST"' in block
        assert "edge_key: edgeKey" in block
        assert "review_note: note" in block
        assert "body.kind = kind" in block
        card = _fn_block(seg, "function _edgeCandidateCardHtml(candidate, draftExists, retired) {")
        for action in ("accept", "dismiss", "incorporate", "restore"):
            assert 'data-edge-action="%s"' % action in card

    def test_accept_requires_a_kind_before_sending(self):
        seg = _edge_segment()
        assert 'var EDGE_KIND_REQUIRED = "採用するには関係の種類を選んでください";' in seg
        block = _fn_block(seg, "function handleEdgeAction(edgeKey, action, card) {")
        assert "var kind = _edgeKindFromCard(card);" in block
        assert "if (!kind) { setEdgesStatus(EDGE_KIND_REQUIRED, true); return; }" in block
        idx_guard = block.index("if (!kind)")
        idx_call = block.index('edgeDecide(edgeKey, "accept", "", kind);')
        assert idx_guard < idx_call

    def test_accept_note_states_that_the_draft_is_unchanged(self):
        seg = _edge_segment()
        assert (
            'var EDGE_ACCEPT_NOTE = "[採用] は「この関係は妥当」という判断だけを記録します。'
            '次版の下書きはこの操作では変わりません。";'
        ) in seg

    def test_dismiss_requires_a_reason_before_sending(self):
        """見送りの理由は gap と同じ機構（prompt）で集め、空のまま送らない。"""
        seg = _edge_segment()
        assert 'var EDGE_DISMISS_REASON_REQUIRED = "見送りには理由が必要です";' in seg
        block = _fn_block(seg, "function handleEdgeAction(edgeKey, action, card) {")
        assert 'var note = prompt("見送りの理由（必須）") || "";' in block
        assert (
            "if (!note.trim()) { setEdgesStatus(EDGE_DISMISS_REASON_REQUIRED, true); return; }"
            in block
        )
        idx_guard = block.index("if (!note.trim())")
        idx_call = block.index('edgeDecide(edgeKey, "dismiss", note, "");')
        assert idx_guard < idx_call

    def test_dismiss_note_explains_persistence_and_restore(self):
        seg = _edge_segment()
        assert "var EDGE_DISMISS_NOTE = " in seg
        note = seg[seg.index("var EDGE_DISMISS_NOTE = ") :].split("\n", 1)[0]
        assert "見送り済みも表示" in note
        assert "戻せます" in note

    def test_incorporate_is_gated_on_accepted_and_draft(self):
        seg = _edge_segment()
        card = _fn_block(seg, "function _edgeCandidateCardHtml(candidate, draftExists, retired) {")
        assert '(!accepted || !draftExists || retired ? " disabled" : "")' in card

    def test_blocked_reasons_are_fact_sentences(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function _edgeBlockedText(accepted, draftExists, retired) {")
        for const in ("EDGE_RETIRED_TEXT", "EDGE_NOT_ACCEPTED_TEXT", "EDGE_NO_DRAFT_TEXT"):
            assert const in block, const


class TestIncorporationThreeSteps:
    """§7: プレビュー（読み取り専用）→ 教員の既存 PUT draft → 刻印。"""

    def test_step1_preview_endpoint(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function edgeIncorporate(edgeKey) {")
        assert 'edgesPath() + "/incorporate-preview"' in block
        assert 'method: "POST"' in block
        assert "edge_key: edgeKey" in block

    def test_step2_confirm_shows_pair_and_kind_and_aborts_on_errors(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function openEdgeIncorporateConfirm(edgeKey, preview) {")
        assert "_edgePairText(candidate)" in block
        assert "EDGE_KIND_LABELS[preview.kind]" in block
        assert "validation.errors" in block
        assert "validation.warnings" in block
        assert "openDangerConfirmModal(" in block
        # 検証エラー・patch なしのときは確認を出さずに事実文で止める
        assert "if (errorLines.length || !preview.patched_draft) {" in block
        idx_abort = block.index("if (errorLines.length || !preview.patched_draft) {")
        idx_modal = block.index("openDangerConfirmModal(")
        assert idx_abort < idx_modal

    def test_step3_reuses_the_existing_put_draft_path(self):
        """骨格を書くのは常に教員の PUT（RE3）。辺候補の専用保存経路を作らない。"""
        seg = _edge_segment()
        block = _fn_block(seg, "function edgeApplyIncorporation(edgeKey, preview) {")
        assert "applyAssistProposal(preview.patched_draft)" in block
        assert 'method: "PUT"' not in seg
        assert 'method: "DELETE"' not in seg
        assert '"/atlas/skeleton/draft"' not in seg

    def test_step3_marks_incorporated_after_the_put(self):
        seg = _edge_segment()
        block = _fn_block(seg, "function edgeApplyIncorporation(edgeKey, preview) {")
        assert 'edgesPath() + "/mark-incorporated"' in block
        idx_put = block.index("applyAssistProposal(preview.patched_draft)")
        idx_mark = block.index('edgesPath() + "/mark-incorporated"')
        assert idx_put < idx_mark, "mark-incorporated は PUT 成功後に呼ぶ契約"
        assert "loadEdgeCandidates();" in block


# ===========================================================================
# 5. 数値非表示・事実文の温度（RE4）
# ===========================================================================


class TestNoNumbers:
    def test_nearness_labels_are_not_hardcoded_in_the_frontend(self):
        """近さの段階ラベルはサーバ（label_vocab の段階表）が確定して渡す。

        フロントが自前の言葉・閾値を持つと、サーバ側の校正と黙って分裂する。
        """
        src = _read(ADMIN_JS)
        for label in ("かなり近い", "近い可能性", "遠い"):
            assert label not in src, label

    def test_no_similarity_or_count_rendering(self):
        seg = _strip_comment_lines(_edge_segment())
        for token in ("cosine", "similarity", "confidence", "weight", "score"):
            assert token not in seg, token
        assert "documents.length" not in seg
        assert re.search(r"\.length\s*\+\s*\"件", seg) is None

    def test_no_defect_or_urging_vocabulary(self):
        seg = _strip_comment_lines(_edge_segment())
        for term in ("カバー率", "網羅率", "地図の穴", "引きましょう", "埋めてください", "早急", "至急"):
            assert term not in seg, term

    def test_empty_queue_is_a_neutral_fact(self):
        seg = _edge_segment()
        assert 'var EDGE_EMPTY_TEXT = "いまレビューする関係の候補はありません。";' in seg

    def test_errors_are_status_lines_not_alerts(self):
        seg = _edge_segment()
        assert "alert(" not in seg
        assert "function setEdgesStatus(text, isError) {" in seg


# ===========================================================================
# 6. ES5 準拠（開発ルール5: admin.js は ES5）
# ===========================================================================


class TestEdgeSegmentIsEs5:
    def test_no_arrow_functions(self):
        assert "=>" not in _edge_segment()

    def test_no_const_or_let(self):
        seg = _edge_segment()
        assert re.search(r"(^|[^\w.$])const\s+\w", seg) is None
        assert re.search(r"(^|[^\w.$])let\s+\w", seg) is None

    def test_no_template_literals_or_class(self):
        seg = _edge_segment()
        assert "`" not in seg
        assert re.search(r"(^|[^\w.$])class\s+\w", seg) is None

    def test_no_promise_finally(self):
        assert ".finally(" not in _edge_segment()


# ===========================================================================
# 7. 3点セット（anchor 表 + マニュアル節）
# ===========================================================================


class TestAnchorThreePieceSet:
    def test_all_edge_anchor_ids_are_registered(self):
        for anchor_id in EDGE_ANCHOR_IDS:
            assert anchor_id in admin_anchors_mod.KNOWN_ADMIN_UI_ANCHOR_IDS, anchor_id
            assert anchor_id in admin_anchors_mod.ADMIN_UI_ANCHORS, anchor_id

    def test_anchor_values_point_at_the_atlas_manual(self):
        expected = {
            "atlas.edge-candidates": "teacher/17-admin-atlas.md#edge-candidates",
            "atlas.edge-dismissed-filter": "teacher/17-admin-atlas.md#edge-dismissed-filter",
            "atlas.edge-incorporate": "teacher/17-admin-atlas.md#edge-incorporate",
        }
        for anchor_id, ref in expected.items():
            assert admin_anchors_mod.ADMIN_UI_ANCHORS[anchor_id] == ref

    def test_anchors_resolve_for_teacher_role(self):
        resolved = admin_anchors_mod.resolve_admin_ui_anchors("TEACHER")
        for anchor_id in EDGE_ANCHOR_IDS:
            assert anchor_id in resolved, anchor_id
            assert resolved[anchor_id]["title"], anchor_id
            assert resolved[anchor_id]["body"], anchor_id

    def test_each_anchor_is_carried_exactly_once_by_admin_js(self):
        src = _read(ADMIN_JS)
        assert (
            src.count('group.setAttribute("data-ui-anchor", "atlas.edge-candidates");') == 1
        )
        for anchor_id in ("atlas.edge-dismissed-filter", "atlas.edge-incorporate"):
            assert src.count('data-ui-anchor="%s"' % anchor_id) == 1, anchor_id

    def test_manual_sections_have_explicit_anchors(self):
        doc = _read(ATLAS_MANUAL)
        assert "## 関係（辺）の候補 {#edge-candidates}" in doc
        for heading in (
            "### 見送り済みも表示（関係の候補） {#edge-dismissed-filter}",
            "### 採用から次版の下書きへ反映・公開まで {#edge-incorporate}",
        ):
            assert heading in doc, heading

    def test_manual_explains_the_origin_chips_without_numbers(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("## 関係（辺）の候補 {#edge-candidates}") :]
        section = section[: section.index("\n### ")]
        assert "プロトタイプ近傍" in section
        assert "共起" in section
        assert "AIによる推定（未確認）" in section
        assert "数値は表示しません" in section
        assert "件数は表示しません" in section
        # 候補は読み時導出で、凍結するまで骨格は変わらない（RE3 / RE6）
        assert "その場で作られます" in section
        assert "公開するまで地図の関係は変わりません" in section

    def test_manual_documents_the_three_kinds(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("### 採用から次版の下書きへ反映・公開まで {#edge-incorporate}") :]
        section = section[: section.index("\n## ")]
        for label in label_vocab.EDGE_KIND_LABELS.values():
            assert "| " + label + " |" in section, label

    def test_manual_states_the_dismiss_reason_and_kind_requirements(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("### 採用から次版の下書きへ反映・公開まで {#edge-incorporate}") :]
        section = section[: section.index("\n## ")]
        assert "見送りには理由が必要です" in section
        assert "採用するには関係の種類を選んでください" in section

    def test_manual_has_the_disabled_button_lines(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("{#edge-incorporate}") :]
        section = section[: section.index("\n## ")]
        assert "**ボタンが無効（グレーアウト）になっている場合**" in section
        assert "[採用]" in section
        assert "下書き" in section
        assert "retired" in section

    def test_manual_dismissed_filter_section_states_the_learner_effect(self):
        doc = _read(ATLAS_MANUAL)
        section = doc[doc.index("### 見送り済みも表示（関係の候補） {#edge-dismissed-filter}") :]
        section = section[: section.index("\n### ")]
        assert "見送りから戻す" in section
        # RE5 / RE8: 行を消さない・見送りは学習者側の推定表示にも効く
        assert "状態を戻す" in section
        assert "学習" in section
