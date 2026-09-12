"""コースビルダー（``admin.js``）の「学ぶ単位」素通し + 前提知識の事実文に対する
静的ガードレール。

正本: ``docs/features/learning_units_design.md`` §6.2 / §6.4。ここで固定するのは:

1. 登録ペイロードの ``topics[]`` に ``units`` を素通しすること（handle の配列）。
2. handle の正規化が **文字列だけ**を通すこと（型の絞り込みはフロントでも行う。
   最終の弁はサーバ側の候補表照合）。
3. 下書きプレビューが ``POST /api/admin/course-builder/prerequisite-check`` を呼び、
   返った ``facts`` を事実の段落として描くこと（失敗・``available:false`` は何も描かない）。
4. 事実の段落・学ぶ単位行に ``data-ui-anchor`` を**新設しない**こと
   （操作要素ではない = ``admin-indicators.js`` の規律）。
5. 数値（件数・一致率・スコア）を描かないこと（LU5）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADMIN_JS = ROOT / "frontend" / "public" / "js" / "admin.js"


def _src() -> str:
    return ADMIN_JS.read_text(encoding="utf-8")


def _function(name: str) -> str:
    """``function name(`` から次のトップレベル ``\\n  function `` までを切り出す。"""
    src = _src()
    start = src.index(f"function {name}(")
    tail = src[start + 1 :]
    rel_end = tail.find("\n  function ")
    end = start + 1 + rel_end if rel_end != -1 else len(src)
    return src[start:end]


# ---------------------------------------------------------------------------
# 1. 登録ペイロードの素通し
# ---------------------------------------------------------------------------

def test_approve_course_passes_units_through():
    body = _function("approveCourse")
    assert "units: cbDraftUnitHandles(t)" in body
    # prerequisites の素通し（既存契約）を壊していない。
    assert "prerequisites: prereqs" in body


def test_unit_handle_normalizer_accepts_strings_only():
    body = _function("cbDraftUnitHandles")
    # 文字列 handle が主経路。dict は handle / unit キーだけを拾う。
    assert 'typeof item === "string"' in body
    assert "item.handle || item.unit" in body
    # 配列でなければ空（数値・オブジェクトのまま通さない）。
    assert "Array.isArray(raw)" in body
    assert "return []" in body


# ---------------------------------------------------------------------------
# 2. 下書きプレビュー
# ---------------------------------------------------------------------------

def test_preview_shows_selected_unit_handles():
    body = _function("renderCoursePreview")
    assert "cbDraftUnitHandles(t)" in body
    assert "学ぶ単位:" in body
    assert "cbRenderPrerequisiteFacts(draft)" in body


def test_prerequisite_check_is_called_and_facts_are_rendered():
    body = _function("cbRenderPrerequisiteFacts")
    assert '"/admin/course-builder/prerequisite-check"' in body
    assert 'method: "POST"' in body
    # available=false / facts 空 / 失敗時は何も描かない（fail-soft）。
    assert "if (!data || !data.available" in body
    assert "catch(function" in body
    # 事実文はサーバの文字列をそのまま描く（フロントに文言を焼き込まない）。
    assert "escHtml(String(fact))" in body


def test_prerequisite_facts_do_not_poll():
    body = _function("cbRenderPrerequisiteFacts")
    for forbidden in ("setInterval", "setTimeout"):
        assert forbidden not in body


def test_prerequisite_payload_carries_only_titles_and_prerequisite_names():
    """送るのは下書きの章・トピック題名と前提の名前だけ（DB 非変更の読み取り検査）。"""
    body = _function("cbRenderPrerequisiteFacts")
    assert "chapters:" in body and "topics:" in body
    assert "prerequisites: prereqs" in body
    # 教材 ID・セッション ID・学習者情報は送らない。
    for forbidden in ("selectedMaterialIds", "state.token", "user_id"):
        assert forbidden not in body


# ---------------------------------------------------------------------------
# 3. アンカーと数値の規律
# ---------------------------------------------------------------------------

def test_no_new_ui_anchor_is_introduced_by_this_layer():
    """事実の段落・学ぶ単位行は操作要素ではないので data-ui-anchor を付けない。"""
    for name in ("cbRenderPrerequisiteFacts", "cbDraftUnitHandles"):
        assert "data-ui-anchor" not in _function(name)
    preview = _function("renderCoursePreview")
    # プレビュー内の新規追加ブロック（前提事実文 / 学ぶ単位行）にアンカーを付けない。
    for snippet in ('id="cb-prereq-facts"', "学ぶ単位:"):
        line = next(l for l in preview.splitlines() if snippet in l)
        assert "data-ui-anchor" not in line


def test_units_line_shows_no_numbers():
    """件数・一致率・スコアを描かない（LU5）。"""
    preview = _function("renderCoursePreview")
    units_lines = [
        l for l in preview.splitlines()
        if "学ぶ単位" in l and not l.strip().startswith("//")
    ]
    assert units_lines
    for line in units_lines:
        assert not re.search(r"\.length\s*\+", line)
        for forbidden in ("件", "スコア", "confidence"):
            assert forbidden not in line


def test_facts_host_element_exists_in_the_preview_markup():
    preview = _function("renderCoursePreview")
    assert 'id="cb-prereq-facts"' in preview
    # 既存の「前提知識:」行の直後（メタ区画）に置く。
    assert preview.index("前提知識:") < preview.index('id="cb-prereq-facts"')
