"""component 根拠カードの再設計（docs/features/component_evidence_redesign.md Phase 1）
のうち、原稿スタジオ（admin-lecture-studio.js）担当分に対する静的ガードレール。

対象:
- 語彙マップ（LS_EVIDENCE_ROLE_LABELS / LS_EVIDENCE_CONFIDENCE_LABELS）+ ヘルパー
  （lsEvidenceKindLabel / lsEvidenceMetaLabel / lsTopicMappingNoteHtml）が内部語彙
  （"component" / "support" / "title_similarity" 等）を日本語ラベルへ変換すること。
  なお種別（kind）の訳語そのものは element-vocab.js（window.ElementVocab）が正本で、
  lsEvidenceKindLabel はそこへ委譲する（admin_ux_issues_2026-08-01.md §3.3 Phase 0。
  正本側の内容は test_element_vocab_ui_static.py が検証する）。
- **CP9（element_context_presentation_redesign.md §3.2 / §6 S3）**: 各根拠カードの
  メタ行は「役割」だけを出し（lsEvidenceMetaLabel は単一引数）、トピック↔論文の
  照合来歴（content_confidence）は転写しない。来歴は要素の性質ではなくトピックの属性
  なので、ペイン見出しの注記（lsTopicMappingNoteHtml）が「このトピックと論文の
  対応付け: タイトル一致 / タイトル類似（教員確認を推奨） / 対応付けなし」として
  1回だけ描く。旧実装は全カードに「対応付け: 対応付けなし」を並べており、要素側の
  欠陥に見える誤読を生んでいた。
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
    def test_kind_labels_delegated_to_element_vocab(self):
        """種別の訳語辞書をこのファイルに再定義しない（正本 = element-vocab.js）。"""
        src = _read()
        assert "var LS_EVIDENCE_KIND_LABELS" not in src
        assert "window.ElementVocab" in src

    def test_confidence_label_map_defined_with_japanese_labels(self):
        src = _read()
        start = src.index("var LS_EVIDENCE_ROLE_LABELS")
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

    def test_kind_label_helper_delegates_and_falls_back(self):
        """kind の表示名は正本へ委譲し、正本未読み込み時は原文へフォールバックする
        （情報を落とさない）。"""
        src = _read()
        start = src.index("function lsEvidenceKindLabel(kind) {")
        end = src.index("\n  }\n", start)
        body = src[start:end]
        assert "vocab.kindLabel(kind)" in body
        assert "return kind;" in body

    def test_meta_label_helper_emits_role_only(self):
        """CP9（element_context_presentation_redesign.md §3.2）: 根拠カードのメタ行は
        「この根拠がトピックに対して果たす役割」だけを出す。トピック↔論文の照合来歴
        （content_confidence）は要素の性質ではないため転写しない — 全カードに
        「対応付け: 対応付けなし」が並び、要素側の欠陥に見える誤読を生んでいた。
        来歴はペイン見出しの lsTopicMappingNoteHtml が1回だけ出す（下記クラス参照）。"""
        src = _read()
        # 単一引数（role のみ）であること = confidence を受け取る余地がない。
        assert "function lsEvidenceMetaLabel(role, confidence) {" not in src
        start = src.index("function lsEvidenceMetaLabel(role) {")
        end = src.index("\n  }\n", start)
        body = src[start:end]
        assert "LS_EVIDENCE_ROLE_LABELS[role]" in body
        assert "対応付け" not in body
        assert "confidence" not in body


class TestMappingNoteMovedToPaneHeader:
    """CP9 の移設先: 照合来歴はペイン見出しの注記として1回だけ描く。"""

    def _note_block(self, src: str) -> str:
        start = src.index("function lsTopicMappingNoteHtml(topic) {")
        end = src.index("\n  }\n", start)
        return src[start:end]

    def test_note_helper_renders_confidence_as_a_header_fact(self):
        src = _read()
        block = self._note_block(src)
        assert "topic.content_confidence" in block
        assert "LS_EVIDENCE_CONFIDENCE_LABELS[confidence]" in block
        assert "このトピックと論文の対応付け: " in block
        assert "ls-evidence-mapping-note" in block

    def test_note_is_omitted_when_confidence_is_unknown(self):
        """旧コースデータ（content_confidence なし）では欄ごと出さない
        = 事実を捏造しない。"""
        block = self._note_block(_read())
        assert 'if (!confidence) return "";' in block

    def test_note_is_rendered_once_by_the_pane_renderer(self):
        """ペイン見出しに1回だけ（カード側には出さない）。空リスト・フラット・
        アウトラインの3経路すべてに同じ注記が付く。"""
        src = _read()
        start = src.index("function lsCourseEvidenceHtml(topic) {")
        end = src.index("function lsCourseEvidenceCardHtml(", start)
        block = src[start:end]
        assert "var mappingNote = lsTopicMappingNoteHtml(topic);" in block
        assert block.count("mappingNote +") == 3
        card_start = src.index("function lsCourseEvidenceCardHtml(topic, item) {")
        card_end = src.index("// ── Phase 2:", card_start)
        assert "lsTopicMappingNoteHtml" not in src[card_start:card_end]

    def test_confidence_labels_are_the_three_documented_values(self):
        src = _read()
        start = src.index("var LS_EVIDENCE_CONFIDENCE_LABELS")
        end = src.index("\n  };\n", start)
        block = src[start:end]
        assert 'exact_title: "タイトル一致"' in block
        assert 'title_similarity: "タイトル類似（教員確認を推奨）"' in block
        assert 'none: "対応付けなし"' in block


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
        # CP9: メタ行は役割のみ（照合来歴はペイン見出しへ移設済み）。
        assert "lsEvidenceMetaLabel(evidenceItem.role)" in block
        assert "lsEvidenceMetaLabel(evidenceItem.role, evidenceItem.confidence)" not in block
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

    def test_evidence_cards_use_kind_and_role_only_meta_labels(self):
        """右ペインのカードも kind は正本語彙、メタ行は役割のみ（CP9）。
        照合来歴はカードではなくペイン見出しの注記が担う。"""
        src = _read()
        start = src.index("function lsCourseEvidenceHtml(topic) {")
        end = src.index("function lsFocusEvidence(key) {")
        block = src[start:end]
        assert "lsEvidenceKindLabel(item.kind)" in block
        assert "lsEvidenceMetaLabel(item.role)" in block
        assert "lsEvidenceMetaLabel(item.role, item.confidence)" not in block
        # カード本体のコードに「対応付け」の文言が混ざらない
        # （申し送りコメントは残るのでコード行だけを見る）。
        code = "\n".join(
            line for line in block.splitlines() if not line.strip().startswith("//")
        )
        assert "対応付け" not in code


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
        r1_start = src.index("var LS_EVIDENCE_ROLE_LABELS")
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
