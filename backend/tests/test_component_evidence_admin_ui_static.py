"""component 根拠カードの再設計（docs/features/component_evidence_redesign.md Phase 1）
のうち、原稿スタジオ（admin-lecture-studio.js）担当分に対する静的ガードレール。

対象:
- 語彙マップ（LS_EVIDENCE_KIND_LABELS / LS_EVIDENCE_ROLE_LABELS /
  LS_EVIDENCE_CONFIDENCE_LABELS）+ ヘルパー（lsEvidenceKindLabel /
  lsEvidenceMetaLabel）が内部語彙（"component" / "support" / "title_similarity" 等）を
  日本語ラベルへ変換すること。
- lsRenderCourseMaterialPreview の evidence 分岐で component/claim がブロックカードでは
  なくインラインチップ（`ls-material-evidence-chip`）に格下げされていること。
- 一方で equation/figure/source 側のブロックカードは維持され、
  `ls-material-embed-meta`（grounding 検証情報）が admin では引き続き出ること
  （設計書 §受け入れ基準「admin ドラフト画面では従来どおり grounding 検証情報を
  確認できる」）。
- 右ペイン根拠リスト（lsCourseEvidenceHtml / lsCourseEvidenceChipsHtml）の kind/meta
  表示にも同じ語彙マップが使われていること。
- 既存の `[data-evidence-ref]` → `lsFocusEvidence` クリック配線が変更後も残っていること
  （チップ・カードとも同じ属性名を使うため、既存配線がそのまま効く設計）。
- 追加・変更領域が ES5 準拠（開発ルール5: アロー関数・const/let・テンプレートリテラル禁止）。

バックエンド（backend/**）・app.js・admin.js・styles.css はこの変更の対象外
（並行作業中の他エージェント担当のため、本テストは admin-lecture-studio.js のみを見る）。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"


def _read() -> str:
    return ADMIN_LS_JS.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 語彙マップ + ヘルパー
# ---------------------------------------------------------------------------


class TestEvidenceVocabulary:
    def test_kind_label_map_defined_with_japanese_labels(self):
        src = _read()
        start = src.index("var LS_EVIDENCE_KIND_LABELS")
        end = src.index("function lsCourseComponentById(componentId) {")
        block = src[start:end]
        assert "論理要素" in block
        assert "主張" in block
        assert "数式" in block
        assert "図" in block
        assert "出典" in block

    def test_confidence_label_map_defined_with_japanese_labels(self):
        src = _read()
        start = src.index("var LS_EVIDENCE_KIND_LABELS")
        end = src.index("function lsCourseComponentById(componentId) {")
        block = src[start:end]
        assert "タイトル類似" in block
        assert "タイトル一致" in block
        assert "対応付けなし" in block

    def test_role_label_map_defined(self):
        src = _read()
        start = src.index("var LS_EVIDENCE_ROLE_LABELS")
        end = src.index("var LS_EVIDENCE_CONFIDENCE_LABELS")
        block = src[start:end]
        assert "根拠" in block

    def test_kind_label_helper_falls_back_to_raw_value(self):
        """未知の kind はマップに無くても原文へフォールバックする（情報を落とさない）。"""
        src = _read()
        start = src.index("function lsEvidenceKindLabel(kind) {")
        end = src.index("\n  }\n", start)
        body = src[start:end]
        assert "LS_EVIDENCE_KIND_LABELS[kind] || kind" in body

    def test_meta_label_helper_combines_role_and_confidence(self):
        src = _read()
        start = src.index("function lsEvidenceMetaLabel(role, confidence) {")
        end = src.index("\n  }\n", start)
        body = src[start:end]
        assert "対応付け: " in body
        assert "roleLabel && confidenceLabel" in body


# ---------------------------------------------------------------------------
# プレビュー: component/claim のチップ化
# ---------------------------------------------------------------------------


class TestMaterialPreviewChipification:
    def _evidence_item_branch(self, src: str) -> str:
        start = src.index("if (evidenceItem) {")
        end = src.index('if (embed.kind === "source" && topic.source_excerpt) {', start)
        return src[start:end]

    def test_component_and_claim_render_as_inline_chip(self):
        src = _read()
        block = self._evidence_item_branch(src)
        assert 'evidenceItem.kind === "component" || evidenceItem.kind === "claim"' in block
        assert "ls-material-evidence-chip" in block
        assert "ls-material-evidence-chip-icon" in block
        assert "ls-material-evidence-chip-label" in block

    def test_chip_keeps_existing_evidence_ref_attribute(self):
        """チップも従来の data-evidence-ref を持ち、既存のクリック配線に乗る。"""
        src = _read()
        block = self._evidence_item_branch(src)
        chip_start = block.index("ls-material-evidence-chip")
        chip_button_start = block.rindex("<button", 0, chip_start)
        chip_button_end = block.index("</button>", chip_button_start)
        chip_html = block[chip_button_start:chip_button_end]
        assert "data-evidence-ref=" in chip_html

    def test_non_component_claim_kinds_still_render_block_card_with_meta(self):
        """設計書の受け入れ基準:
        admin ドラフト画面では従来どおり grounding 検証情報（対応付け方法等）を
        確認できる。equation/figure/source 等はブロックカードのまま維持し、
        ls-material-embed-meta を出す。"""
        src = _read()
        block = self._evidence_item_branch(src)
        assert "ls-material-evidence-card" in block
        assert "ls-material-embed-meta" in block
        assert "lsEvidenceKindLabel(evidenceItem.kind)" in block
        assert "lsEvidenceMetaLabel(evidenceItem.role, evidenceItem.confidence)" in block
        # 内部語彙（英語の role/confidence 生値）をそのまま結合する旧実装には戻っていない
        assert "[evidenceItem.role, evidenceItem.confidence].filter(Boolean).join" not in block

    def test_other_embed_kinds_untouched(self):
        """equation / figure / source (summary) 分岐自体は変更していない
        （据え置きの契約: test_figure_course_flow_ui_static.py 側で別途検証済み）。"""
        src = _read()
        assert 'if (embed.kind === "equation") {' in src
        assert 'if (embed.kind === "figure") {' in src
        assert 'if (embed.kind === "source" && (embedId === "summary" || embedId === "topic_summary")) {' in src


# ---------------------------------------------------------------------------
# 右ペイン根拠リストの日本語化
# ---------------------------------------------------------------------------


class TestRightPaneEvidenceList:
    def test_chips_html_uses_kind_label(self):
        src = _read()
        start = src.index("function lsCourseEvidenceChipsHtml(topic) {")
        end = src.index("function lsCourseEvidenceHtml(topic) {")
        block = src[start:end]
        assert "lsEvidenceKindLabel(item.kind)" in block

    def test_evidence_cards_use_kind_and_meta_labels(self):
        src = _read()
        start = src.index("function lsCourseEvidenceHtml(topic) {")
        end = src.index("function lsFocusEvidence(key) {")
        block = src[start:end]
        assert "lsEvidenceKindLabel(item.kind)" in block
        assert "lsEvidenceMetaLabel(item.role, item.confidence)" in block


# ---------------------------------------------------------------------------
# 既存配線 (data-evidence-ref → lsFocusEvidence) の維持
# ---------------------------------------------------------------------------


class TestEvidenceRefWiringPreserved:
    def test_evidence_ref_click_still_wired_to_focus_evidence(self):
        src = _read()
        start = src.index('root.querySelectorAll("[data-evidence-ref]")')
        end = src.index("});", start)
        block = src[start:end]
        assert "lsFocusEvidence(link.getAttribute(" in block

    def test_focus_evidence_function_still_defined(self):
        src = _read()
        assert "function lsFocusEvidence(key) {" in src


# ---------------------------------------------------------------------------
# ES5 準拠（開発ルール5）
# ---------------------------------------------------------------------------


class TestEs5Guard:
    """追加・変更した領域に限定した ES5 チェック
    （test_figure_course_flow_ui_static.py の _regions() 方式を踏襲）。"""

    def _regions(self):
        src = _read()
        r1_start = src.index("var LS_EVIDENCE_KIND_LABELS")
        r1_end = src.index("function lsCourseComponentById(componentId) {")
        r2_start = src.index("function lsCourseEvidenceChipsHtml(topic) {")
        r2_end = src.index("function lsFocusEvidence(key) {")
        r3_start = src.index("if (evidenceItem) {")
        r3_end = src.index('if (embed.kind === "source" && topic.source_excerpt) {', r3_start)
        return [src[r1_start:r1_end], src[r2_start:r2_end], src[r3_start:r3_end]]

    def test_no_arrow_functions(self):
        for region in self._regions():
            assert "=>" not in region

    def test_no_const_or_let(self):
        for region in self._regions():
            assert re.search(r"\bconst\s+\w", region) is None
            assert re.search(r"\blet\s+\w", region) is None

    def test_no_template_literals(self):
        for region in self._regions():
            assert "`" not in region

    def test_uses_var_or_function(self):
        for region in self._regions():
            assert "var " in region or "function " in region
