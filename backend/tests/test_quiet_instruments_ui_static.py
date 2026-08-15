"""宣言された弁と静かな計器（Phase 4 教員支援 v1）フロントエンドの静的ガードレール。

正本: docs/features/teacher_triage_instruments_design.md（TT1〜TT6・§5 精査記録）。
バックエンド（sort パラメータ・load_level_label・forecast API・preview-split の wm）は
別チームが並行実装中のため、ここでは JS/HTML/マニュアル側の静的契約のみを検証する
（test_element_explanation_review_ui_static.py と同型のソース静的検証）:

1. TT1 沈黙の並べ替えを作らない — 既定は従来順（sort パラメータは load のときのみ付く）・
   明示トグル・適用中の並び順を宣言する一行（逐語）・localStorage へ保存しない
   （毎回既定に戻る）。
2. TT2 数値を見せない — 段階ラベルはサーバの `load_level_label` をそのまま表示し、
   JS 側に語彙表・閾値を再定義しない。新規文言に生カウント・%・「残り」が無い。
3. TT3 来歴を偽らない — 確定操作（説明の approve/dismiss/bulk・R層 PATCH）の body に
   `sort_order` を同梱する。
4. TT4 開始をブロックしない — コスト見通しの一行は hidden の器 + `show === true` 分岐のみで、
   ボタンの無効化（.disabled）を一切書かない。fail-open。
5. WMレンズ — preview-split 応答の `wm` を射影し、`wm.fact` の事実文をサーバの正本の
   まま素通し表示する。縮退（textual 照合）の事実文はサーバ fact の一文に含まれて届く
   ため、JS 側で縮退文を作文しない（二重表示の防止 — レビュー是正）。分割マーカー ===
   の自動挿入コードを持たない（TT6）。
6. アンカー4件（deliberation.review-sort-toggle / lecture-studio.recon-review-sort /
   materials.cost-forecast-note / lecture-studio.slide-wm-label）の KNOWN 登録と
   マニュアル節の実在（frontend 担体との双方向網羅は既存
   test_admin_help_inspect_ui_static.py が自動検査する）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

DELIBERATION_JS = ROOT / "frontend" / "public" / "js" / "deliberation.js"
LECTURE_STUDIO_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
LLM_MODELS_JS = ROOT / "frontend" / "public" / "js" / "admin-llm-models.js"
ADMIN_HTML = ROOT / "frontend" / "public" / "admin.html"
MANUAL_DIR = ROOT / "docs" / "manual"

#: TT1 の宣言一行（逐語。設計書 §2「基盤への影響が大きい順に並んでいます」型の事実文）。
SORT_DECLARATION = "基盤への影響が大きい順に並んでいます"
#: 導出不能候補の正直ラベル（設計書 §2 / §5-②）。
UNDERIVABLE_LABEL = "影響度を導出できない候補"
#: かつて JS 側で作文していた縮退文（レビュー是正で削除済み — サーバ fact に一本化）。
#: 再発防止の禁止文字列として使う（JS の WM 表示コードにこれが再登場したら二重表示）。
WM_DEGRADED_JS_SENTENCE = "記号の照合は表記の一致による近似です"

NEW_ANCHOR_IDS = (
    "deliberation.review-sort-toggle",
    "lecture-studio.recon-review-sort",
    "materials.cost-forecast-note",
    "lecture-studio.slide-wm-label",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _function_block(src: str, name: str) -> str:
    """モジュールレベル関数（2スペースインデント）本体をソースから切り出す。"""
    m = re.search(r"function " + name + r"\([^)]*\)\s*\{[\s\S]+?\n  \}", src)
    assert m, f"function {name} が見つかりません"
    return m.group(0)


def _region(src: str, start_marker: str, end_marker: str) -> str:
    start = src.index(start_marker)
    end = src.index(end_marker, start)
    assert start < end
    return src[start:end]


class TestSortDeclarationLine:
    """TT1: 適用中の並び順の宣言一行（逐語）と、既定時は表示しない hidden の器。"""

    def test_declaration_sentence_verbatim_in_both_queues(self):
        for path in (DELIBERATION_JS, LECTURE_STUDIO_JS):
            src = _read(path)
            assert SORT_DECLARATION in src, f"{path.name} に宣言一行が無い"

    def test_declaration_container_is_hidden_by_default(self):
        # 既定＝従来順のとき宣言は出さない（並べ替えをしていないときに宣言は不要）。
        src = _read(DELIBERATION_JS)
        note_idx = src.index('id="deliberation-explanation-review-sort-note"')
        snippet = src[note_idx : note_idx + 300]
        assert "hidden" in snippet.split(SORT_DECLARATION)[0]
        src = _read(LECTURE_STUDIO_JS)
        note_idx = src.index('id="ls-recon-review-sort-note"')
        snippet = src[note_idx : note_idx + 300]
        assert "hidden" in snippet.split(SORT_DECLARATION)[0]

    def test_declaration_visibility_follows_sort_order(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_updateExplanationReviewSortUi")
        assert 'note.hidden = explanationReviewState.sortOrder !== "load"' in block
        src = _read(LECTURE_STUDIO_JS)
        assert 'note.hidden = lsReconReviewSortOrder !== "load"' in src

    def test_declaration_sentence_has_no_numbers(self):
        assert not re.search(r"[0-9％%]", SORT_DECLARATION)
        assert "残り" not in SORT_DECLARATION


class TestSortToggleDefaultAndNonPersistence:
    """TT1: 既定は従来順（パラメータ自体を送らない）・明示トグル・非永続。"""

    def test_explanation_queue_sort_param_only_when_load(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_loadExplanationReviewQueue")
        assert 'explanationReviewState.sortOrder === "load" ? "&sort=load" : ""' in block

    def test_recon_queue_sort_param_only_when_load(self):
        src = _read(LECTURE_STUDIO_JS)
        block = _function_block(src, "lsLoadReconReviewModalList")
        assert 'lsReconReviewSortOrder === "load"' in block
        assert "sort=load" in block

    def test_default_state_is_default_order(self):
        src = _read(DELIBERATION_JS)
        # 初期化とリセット（_resetExplanationReviewState）の両方が既定＝従来順であること。
        assert src.count('selected: {}, sortOrder: "default" }') >= 2, (
            "説明レビューキューの sortOrder 初期値/リセット値が default でない"
        )
        src = _read(LECTURE_STUDIO_JS)
        assert 'var lsReconReviewSortOrder = "default";' in src

    def test_recon_modal_resets_to_default_on_open(self):
        src = _read(LECTURE_STUDIO_JS)
        block = _function_block(src, "lsOpenReconReviewModal")
        assert 'lsReconReviewSortOrder = "default";' in block

    def test_no_local_storage_persistence(self):
        # 並び順は保存しない（毎回既定に戻る — TT1）。3ファイルとも localStorage への
        # 書き込みが存在しないこと。
        for path in (DELIBERATION_JS, LECTURE_STUDIO_JS, LLM_MODELS_JS):
            src = _read(path)
            assert "localStorage.setItem" not in src, f"{path.name} が localStorage へ保存している"

    def test_toggle_is_an_explicit_button(self):
        src = _read(DELIBERATION_JS)
        assert 'id="deliberation-explanation-review-sort-toggle"' in src
        assert 'data-ui-anchor="deliberation.review-sort-toggle"' in src
        src = _read(LECTURE_STUDIO_JS)
        assert 'id="ls-recon-review-sort-toggle"' in src
        assert 'data-ui-anchor="lecture-studio.recon-review-sort"' in src


class TestSortOrderProvenance:
    """TT3: 確定操作の body に sort_order を同梱する（来歴を偽らない）。"""

    def test_single_decide_sends_sort_order(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_decideExplanationReviewCard")
        assert "sort_order: explanationReviewState.sortOrder" in block

    def test_bulk_review_sends_sort_order(self):
        src = _read(DELIBERATION_JS)
        block = _function_block(src, "_bulkReviewExplanations")
        assert "sort_order: explanationReviewState.sortOrder" in block

    def test_recon_patch_sends_sort_order(self):
        src = _read(LECTURE_STUDIO_JS)
        block = _function_block(src, "lsPatchReconItemStatus")
        assert 'sort_order: sortOrder || "default"' in block
        # モーダル経由の PATCH は現在の並び順を渡す。
        modal_block = _function_block(src, "lsPatchReconItemInModal")
        assert "lsPatchReconItemStatus(itemId, status, lsReconReviewSortOrder)" in modal_block


class TestLoadLevelLabelDisplay:
    """TT2: 段階ラベルはサーバの load_level_label をそのまま表示（JS 語彙表なし）。"""

    def test_labels_rendered_from_server_value_without_js_table(self):
        for path in (DELIBERATION_JS, LECTURE_STUDIO_JS):
            src = _read(path)
            assert "load_level_label" in src, f"{path.name} が load_level_label を表示していない"
            # 低/中/高/最高位 の語彙表（doubt-atlas.js の LOAD_LABELS 相当）を
            # 再定義しない（正本はサーバ。test_doubt_vocab_mirror.py の対象を増やさない）。
            # コメントでの言及は許すが、JS 文字列リテラル（＝表の値）としては禁止。
            assert '"最高位"' not in src, f"{path.name} に段階ラベルの独自辞書がある"
            assert "LOAD_LABELS" not in src, f"{path.name} に LOAD_LABELS 表がある"

    def test_underivable_candidates_are_labeled_honestly(self):
        for path in (DELIBERATION_JS, LECTURE_STUDIO_JS):
            src = _read(path)
            assert UNDERIVABLE_LABEL in src, f"{path.name} に導出不能候補の正直ラベルが無い"

    def test_label_values_are_escaped(self):
        src = _read(DELIBERATION_JS)
        assert "escHtml(exp.load_level_label)" in src
        src = _read(LECTURE_STUDIO_JS)
        assert "escHtml(it.load_level_label)" in src


class TestCostForecastNote:
    """§3.1: コスト見通しの一行 — hidden の器 + show=true 分岐のみ・処理を止めない。"""

    def test_upload_zone_container_is_hidden_by_default(self):
        src = _read(ADMIN_HTML)
        idx = src.index('id="llm-model-cost-note"')
        snippet = src[idx : idx + 300]
        assert "hidden" in snippet
        assert 'data-ui-anchor="materials.cost-forecast-note"' in snippet

    def test_reanalyze_container_is_hidden_by_default(self):
        src = _read(LLM_MODELS_JS)
        idx = src.index('id="llm-model-reanalyze-cost-note"')
        snippet = src[idx : idx + 300]
        assert "hidden" in snippet
        assert 'data-ui-anchor="materials.cost-forecast-note"' in snippet

    def test_upload_zone_note_shown_only_when_show_is_true(self):
        src = _read(LLM_MODELS_JS)
        block = _function_block(src, "_loadMaterialsCostForecast")
        assert "data.show === true" in block
        assert "data.message" in block
        # fail-open: 失敗時は何も出さない（エラー表示・処理停止をしない）。
        assert ".catch(" in block
        # 「画像も解析する」を判定に反映する（vision カウンタの有無で見通しが変わる —
        # レビュー是正）。
        assert "analyze_images=" in block
        assert "upload-analyze-images" in block

    def test_upload_zone_forecast_refetches_on_analyze_images_change(self):
        """チェック変更時のイベント駆動再取得（1回ずつ・ポーリング禁止）。"""
        src = _read(LLM_MODELS_JS)
        block = _function_block(src, "initMaterialsPanel")
        change_idx = block.index('addEventListener("change"')
        assert "_loadMaterialsCostForecast()" in block[change_idx:]

    def test_reanalyze_note_shown_only_when_show_is_true(self):
        src = _read(LLM_MODELS_JS)
        block = _function_block(src, "_loadReanalyzeCostForecast")
        assert "data.show === true" in block
        assert "data.message" in block
        assert ".catch(" in block
        assert "analyze_images=" in block

    def test_cost_note_code_never_touches_disabled(self):
        """TT4: 計器は事実文の提示までで、ボタンの無効化・処理の中止をしない。"""
        src = _read(LLM_MODELS_JS)
        for name in (
            "_setMaterialsCostNote",
            "_loadMaterialsCostForecast",
            "_loadReanalyzeCostForecast",
        ):
            block = _function_block(src, name)
            assert ".disabled" not in block, f"{name} がボタンの無効化に触れている"
            assert "throw" not in block, f"{name} が例外で処理を止めている"

    def test_cost_note_message_comes_from_server_only(self):
        """事実文の本文はサーバの正本（data.message）のみ。クライアントで数値・
        残回数を合成しない（TT2）。"""
        src = _read(LLM_MODELS_JS)
        for name in ("_loadMaterialsCostForecast", "_loadReanalyzeCostForecast"):
            block = _function_block(src, name)
            assert "残り" not in block
            assert "%" not in block
            assert not re.search(r"[0-9]", block), f"{name} に数値リテラルがある"

    def test_no_polling_for_forecast(self):
        src = _read(LLM_MODELS_JS)
        assert "setInterval" not in src


class TestWmLens:
    """§3.2: WMレンズ — wm の射影・fact の事実文・degraded の縮退文言・=== は教員の手。"""

    def test_preview_split_projection_carries_wm(self):
        src = _read(LECTURE_STUDIO_JS)
        block = _function_block(src, "lsFetchSplitSlides")
        assert "wm: sd.wm || null" in block

    def test_preview_split_sends_optional_document_id(self):
        src = _read(LECTURE_STUDIO_JS)
        block = _function_block(src, "lsFetchSplitSlides")
        # optional: 取れる場合のみ送る（既存呼び出しは不変 — §5-④）。
        assert "if (documentId) payload.document_id = documentId;" in block

    def test_wm_fact_is_server_passthrough_without_js_composition(self):
        """縮退の事実文はサーバ fact の一文に一本化（JS 側で縮退文を作文しない —
        二重表示の防止。lecture_wm.py WM_DEGRADED_NOTICE がサーバの正本）。"""
        src = _read(LECTURE_STUDIO_JS)
        region = _region(src, "var wm = slide.wm || null;", "var notesHtml")
        assert "escHtml(wm.fact)" in region
        # JS 作文の縮退文が再登場しないこと（再発防止）。
        assert WM_DEGRADED_JS_SENTENCE not in region
        # degraded フラグでの文言分岐を持たない（fact 素通しのみ）。
        assert "wm.degraded ?" not in region

    def test_wm_label_rendered_from_server_label(self):
        src = _read(LECTURE_STUDIO_JS)
        region = _region(src, "var wm = slide.wm || null;", "var notesHtml")
        assert "escHtml(wm.level_label)" in region
        assert 'data-ui-anchor="lecture-studio.slide-wm-label"' in region

    def test_wm_region_has_no_numbers_or_remaining_vocab(self):
        src = _read(LECTURE_STUDIO_JS)
        region = _region(src, "var wm = slide.wm || null;", "var notesHtml")
        assert "残り" not in region
        assert not re.search(r"[0-9％]", region), "WMレンズの新規表示コードに数値がある"

    def test_no_automatic_marker_insertion(self):
        """TT6: 分割マーカー === を書くのは教員の手のみ。WMレンズの表示コードが
        マーカー挿入（lsInsertSlideMarkerIntoTextarea / lsInsertTextAtCursor）を
        呼ばないこと。"""
        src = _read(LECTURE_STUDIO_JS)
        region = _region(src, "var wm = slide.wm || null;", "var notesHtml")
        assert "lsInsertSlideMarkerIntoTextarea" not in region
        assert "lsInsertTextAtCursor" not in region


class TestAnchorsRegistration:
    """アンカー4件の KNOWN/ADMIN_UI_ANCHORS 登録とマニュアル節の実在。

    frontend 担体との双方向網羅・1属性1ID は既存 test_admin_help_inspect_ui_static.py
    が自動検査するため、ここでは登録とマニュアル節の存在のみを固定する。
    """

    def test_four_anchor_ids_registered(self):
        from core.help_kb.admin_ui_anchors import (
            ADMIN_UI_ANCHORS,
            KNOWN_ADMIN_UI_ANCHOR_IDS,
        )

        for anchor_id in NEW_ANCHOR_IDS:
            assert anchor_id in KNOWN_ADMIN_UI_ANCHOR_IDS, f"{anchor_id} が KNOWN に無い"
            assert anchor_id in ADMIN_UI_ANCHORS, f"{anchor_id} がマップされていない"
            assert ADMIN_UI_ANCHORS[anchor_id].startswith("teacher/"), (
                f"{anchor_id} は teacher マニュアル節を指すこと"
            )

    def test_manual_sections_exist_with_explicit_anchors(self):
        from core.help_kb.admin_ui_anchors import ADMIN_UI_ANCHORS

        for anchor_id in NEW_ANCHOR_IDS:
            ref = ADMIN_UI_ANCHORS[anchor_id]
            rel_path, _, section_anchor = ref.partition("#")
            manual_path = MANUAL_DIR / rel_path
            assert manual_path.exists(), f"{rel_path} が存在しない"
            body = _read(manual_path)
            assert "{#" + section_anchor + "}" in body, (
                f"{rel_path} に節 {{#{section_anchor}}} が無い"
            )

    def test_manual_sections_state_the_key_facts(self):
        deliberation = _read(MANUAL_DIR / "teacher" / "24-admin-deliberation.md")
        lecture = _read(MANUAL_DIR / "teacher" / "14-admin-lecture-studio.md")
        materials = _read(MANUAL_DIR / "teacher" / "11-admin-materials.md")
        # 並び順の意味と監査記帳（TT3）。
        assert SORT_DECLARATION in deliberation
        assert "監査記録に残ります" in deliberation
        assert SORT_DECLARATION in lecture
        assert "監査記録に残ります" in lecture
        # WMレンズ: === を挿すのは教員の手（TT6）。縮退の事実文はサーバ fact に
        # 一本化されたため、逐語ではなく「縮退の事実文が併記される」旨のみ固定する
        # （文言の正本は lecture_wm.py WM_DEGRADED_NOTICE）。
        assert "縮退の事実文" in lecture
        assert "常に教員の手" in lecture
        # コスト見通し: 収まる見込みのときは表示されない・処理は止まらない（TT4）。
        assert "この行自体が表示されません" in materials
        assert "処理が" in materials and "止められたりすることはありません" in materials
