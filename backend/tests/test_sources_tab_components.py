"""出典タブの「このトピックの論理要素」（2026-07-26）に対するガードレール。

背景（実画面の欠陥）: 旧「本セッションで参照されたセクション」は
``course_content_builder._referenced_sections_from_topics`` が作る
「トピック → component_id の内部対応表」をそのまま学習者に描画していた。1カードの中身は
固定文字列 ``"Agent pipeline"`` / 内部 ID（``comp_001``）/ **トピック名**（要素名ではない）/
固定文字列 ``"CourseMappingAgent / ComponentAssemblyAgent から対応付け"`` で、
学習者に伝わる情報が無かった。見出しも事実と違った（セッション単位でもセクションでもない）。

検証観点:
1. 内部対応表の生成が復活していない（固定文字列・生成関数の不在）。
2. 学習画面が ``course.referenced_sections`` を描画していない。
3. 置き換え後の表示は evidence_items（教材本文の ⚓ チップと同じ供給元）から
   要素名と要旨を出し、詳細は既存のチップのポップオーバーへ送る（実装を二重化しない）。
4. ラベルの無い要素（ID だけ）をカードにしない。
5. 「登録済み教材」の題名解決は保存データを書き換えず、教員が付けた題名を上書きしない。
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_JS = ROOT / "frontend" / "public" / "js" / "app.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
BUILDER_PY = ROOT / "backend" / "core" / "course_content_builder.py"
LEARNING_PY = ROOT / "backend" / "api" / "routes" / "learning.py"
SERVICES_PY = ROOT / "backend" / "api" / "services.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _extract_function_body(src: str, signature: str) -> str:
    start = src.index(signature)
    brace_start = src.index("{", start)
    depth = 0
    i = brace_start
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1
    raise AssertionError("unterminated function body for: " + signature)


class TestInternalMappingNotShownToLearners:
    def test_builder_no_longer_emits_referenced_sections(self):
        py = _read(BUILDER_PY)
        assert "_referenced_sections_from_topics" not in py
        # 学習者に出していた固定文字列（内部 agent 名）が復活していない
        assert "Agent pipeline" not in py
        assert "CourseMappingAgent / ComponentAssemblyAgent から対応付け" not in py

    def test_learning_ui_does_not_render_referenced_sections(self):
        js = _read(APP_JS)
        # 参照（コメント内の経緯説明は可）ではなく、実際の読み出しが無いこと
        assert "state.course.referenced_sections" not in js
        assert "<h4>本セッションで参照されたセクション</h4>" not in js

    def test_stored_referenced_sections_are_not_deleted(self):
        """既存コースの保存済みデータは消さない（P4）。UI が読まなくなるだけ。"""
        py = _read(BUILDER_PY)
        assert 'course.pop("referenced_sections"' not in py
        assert 'del course["referenced_sections"]' not in py


class TestTopicComponentSection:
    def test_section_built_from_evidence_items(self):
        """教材本文の ⚓ チップと同じ供給元（evidence_items）から作る。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function collectTopicComponentEvidence() {")
        assert "evidence_items" in block
        assert 'item.kind !== "component"' in block

    def test_cards_show_label_and_summary_not_internal_ids(self):
        js = _read(APP_JS)
        block = _extract_function_body(js, "function collectTopicComponentEvidence() {")
        # ラベルが無い要素はカードにしない（ID だけのカードを作らない）
        assert "if (!title) return;" in block
        # 要旨がラベルと同一なら二重に出さない
        assert "item.summary !== title" in block

    def test_detail_reuses_existing_chip_popover(self):
        """詳細表示（component context API）の実装を二重に持たない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function jumpToMaterialEvidenceChip(ref) {")
        assert "ls-material-evidence-chip" in block
        assert "chip.click()" in block
        # 出典タブ側で context API を直接叩き直していない
        assert "/context" not in block

    def test_anchor_presence_is_derived_from_chunk_text_not_dom(self):
        """右パネルと教材区画の描画順に依存させない。"""
        js = _read(APP_JS)
        block = _extract_function_body(js, "function collectTopicComponentEvidence() {")
        assert "chunk.text" in block
        assert "querySelector" not in block

    def test_section_has_a_bounded_item_count(self):
        js = _read(APP_JS)
        assert "_TOPIC_COMPONENT_LIMIT" in js

    def test_css_classes_defined(self):
        css = _read(STYLES_CSS)
        for selector in (
            ".lx-topic-component",
            ".lx-topic-component-title",
            ".lx-topic-component-summary",
            ".lx-topic-component-btn",
        ):
            assert selector in css, f"missing CSS selector: {selector}"


class TestRegisteredMaterialTitle:
    def test_resolver_exists_and_skips_empty_titles(self):
        py = _read(SERVICES_PY)
        assert "def resolve_course_source_titles(" in py
        block = py[py.index("def resolve_course_source_titles("):]
        block = block[: block.index("\ndef ", 10)]
        assert "documents" in block and "source_path" in block
        # 空題名は対応表に載せない（差し替えない）
        assert 'str(row[1] or "").strip()' in block

    def test_display_only_never_writes_back(self):
        py = _read(LEARNING_PY)
        start = py.index("def _with_resolved_source_titles(")
        block = py[start: py.index("\n@router", start)]
        # 保存経路（save_course_data 等）を呼ばない
        assert "save_course_data" not in block
        # 元の dict を破壊しない（コピーを返す）
        assert "out = dict(data)" in block

    def test_teacher_authored_title_is_not_overwritten(self):
        py = _read(LEARNING_PY)
        start = py.index("def _with_resolved_source_titles(")
        block = py[start: py.index("\n@router", start)]
        assert "(not stored or stored == material_id)" in block
        # 差し替えたときは元の値を落とさない（P4）
        assert 'new_src["subtitle"] = stored' in block
