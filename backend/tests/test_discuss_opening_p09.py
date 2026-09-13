"""discuss 開幕画面「論文の骨格（章の流れ）」（P0-9）のテスト。

正本: `docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の P0-9
（付属資料 A_fidelity.md の F-14 / 本文 D3「文章層…単位として永続化されず学習者に
届かない」）。設計上の制約は `docs/features/discussion_mode_design.md`（DM1/DM5/DM6/DM8）と
`discuss_opening_authoring_design.md`（OA3/OA4/OA7）を継承する。

検証観点:

1. `paper_skeleton.logical_blocks` を**読み時に**射影して `documents[].chapter_skeleton`
   として出す（新テーブル・新ステージ・LLM 呼び出しを足していない）。
2. 章タイトルは `document_structure.sections` の**実所在だけ**で解決する（捏造しない）。
3. artifact が無い document ではキー自体を足さない（OA4: Phase 0 の DTO と完全一致）。
4. `available` の判定を変えない（骨格の有無で開幕画面が出たり消えたりしない）。
5. confidence / reason の生数値・内部値が出ない（DM6）。
6. フロントは問いの直後に区画を描き、空なら区画ごと出さない。

DB 実接続は使わない（fake dict + ソース静的検査のみ）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.discuss import opening  # noqa: E402

_OPENING_SRC = (BACKEND / "core" / "discuss" / "opening.py").read_text(encoding="utf-8")
_DISCUSS_JS = (ROOT / "frontend" / "public" / "js" / "discuss.js").read_text(encoding="utf-8")
_STYLES_CSS = (ROOT / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8")


def _extract_js_function(src: str, signature: str) -> str:
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
    raise AssertionError("unbalanced braces for " + signature)


def _skeleton(blocks=None):
    return {
        "paper_goal": {"text": "Measure the thing."},
        "logical_blocks": blocks
        if blocks is not None
        else [
            {
                "block_id": "lb_1",
                "block_type": "setup",
                "label": "実験装置の構成",
                "section_ids": ["s1", "s-unknown"],
                "summary": "リング共振器の構成を述べる。",
                "reason": "…",
                "confidence": 0.8,
            },
            {
                "block_id": "lb_2",
                "block_type": "result",
                "label": "感度の評価",
                "section_ids": ["s2"],
                "summary": "上限値を与える。",
                "reason": "…",
                "confidence": 0.6,
            },
        ],
    }


def _structure():
    return {
        "sections": [
            {"section_id": "s1", "title": "Experimental setup", "level": 1, "order": 0},
            {"section_id": "s2", "title": "Performance evaluation", "level": 1, "order": 1},
            {"section_id": "s3", "title": "Discussion", "level": 1, "order": 2},
        ]
    }


# ---------------------------------------------------------------------------
# 射影（純関数）
# ---------------------------------------------------------------------------


class TestProjectChapterSkeleton:
    def test_logical_blocks_are_projected_in_artifact_order(self):
        blocks, truncated = opening.project_chapter_skeleton(_skeleton(), _structure())
        assert truncated is False
        assert [b["label"] for b in blocks] == ["実験装置の構成", "感度の評価"]
        assert [b["block_type"] for b in blocks] == ["setup", "result"]
        assert blocks[0]["summary"] == "リング共振器の構成を述べる。"

    def test_section_titles_resolve_only_from_real_sections(self):
        """存在する section_id のタイトルだけを出す（未知 ID は黙って落とす = 捏造しない）。"""
        blocks, _ = opening.project_chapter_skeleton(_skeleton(), _structure())
        assert blocks[0]["section_titles"] == ["Experimental setup"]
        assert blocks[1]["section_titles"] == ["Performance evaluation"]

    def test_section_titles_are_empty_without_document_structure(self):
        blocks, _ = opening.project_chapter_skeleton(_skeleton(), None)
        assert [b["section_titles"] for b in blocks] == [[], []]

    def test_no_skeleton_artifact_yields_nothing(self):
        assert opening.project_chapter_skeleton(None, _structure()) == ([], False)
        assert opening.project_chapter_skeleton({}, _structure()) == ([], False)

    def test_blocks_without_label_or_summary_are_dropped(self):
        """label も summary も無いと block_id しか出せない（内部 ID を表示しない）。"""
        blocks, _ = opening.project_chapter_skeleton(
            _skeleton([{"block_id": "lb_x", "block_type": "setup", "label": "", "summary": ""}]),
            _structure(),
        )
        assert blocks == []

    def test_limit_is_reported_honestly(self):
        many = [
            {"block_id": "lb_%d" % i, "block_type": "setup", "label": "第%d章" % i, "summary": ""}
            for i in range(opening._MAX_CHAPTER_SKELETON + 3)
        ]
        blocks, truncated = opening.project_chapter_skeleton(_skeleton(many), _structure())
        assert len(blocks) == opening._MAX_CHAPTER_SKELETON
        assert truncated is True

    def test_no_numeric_or_reason_keys_are_projected(self):
        blocks, _ = opening.project_chapter_skeleton(_skeleton(), _structure())
        for block in blocks:
            assert set(block.keys()) == {"block_type", "label", "summary", "section_titles"}

    def test_projection_does_not_mutate_the_artifact(self):
        skeleton = _skeleton()
        before = str(skeleton)
        opening.project_chapter_skeleton(skeleton, _structure())
        assert str(skeleton) == before


# ---------------------------------------------------------------------------
# build_opening への配線（部分適用 = OA4）
# ---------------------------------------------------------------------------


class TestBuildOpeningWiring:
    def _patch(self, monkeypatch, artifacts):
        monkeypatch.setattr(opening, "_document_titles", lambda ids: {i: "T" for i in ids})
        monkeypatch.setattr(opening, "document_run_artifacts", lambda doc: artifacts)
        monkeypatch.setattr(opening, "equation_records", lambda doc, artifacts=None: [])
        monkeypatch.setattr(opening, "_claim_label_index", lambda doc, arts: {})
        monkeypatch.setattr(opening, "_load_graph_nodes", lambda doc: [])
        monkeypatch.setattr(opening, "_load_approved_discussion_seeds", lambda doc: [])
        monkeypatch.setattr(opening, "_compile_open_assumptions", lambda course: [])

    def test_chapter_skeleton_is_served_with_its_truncation_flag(self, monkeypatch):
        self._patch(
            monkeypatch,
            {"paper_skeleton": _skeleton(), "document_structure": _structure()},
        )
        out = opening.build_opening("c1", ["d1"])
        doc = out["documents"][0]
        assert [b["label"] for b in doc["chapter_skeleton"]] == ["実験装置の構成", "感度の評価"]
        assert doc["chapter_skeleton_truncated"] is False

    def test_missing_skeleton_leaves_the_phase0_dto_untouched(self, monkeypatch):
        """OA4: 骨格の無い document ではキー自体を足さない（劣化しない）。"""
        self._patch(monkeypatch, {"document_structure": _structure()})
        doc = opening.build_opening("c1", ["d1"])["documents"][0]
        assert "chapter_skeleton" not in doc
        assert "chapter_skeleton_truncated" not in doc

    def test_availability_is_not_changed_by_the_chapter_skeleton(self, monkeypatch):
        """骨格だけがある document で開幕画面が突然「出せる」ことにならない
        （available の判定は従来どおり投影と教員入力だけで決める）。"""
        self._patch(
            monkeypatch,
            {"paper_skeleton": _skeleton(), "document_structure": _structure()},
        )
        out = opening.build_opening("c1", ["d1"])
        assert out["available"] is False
        assert out["documents"][0]["chapter_skeleton"]

    def test_no_llm_import_was_added_to_the_module(self):
        for banned in ("core.llm", "generate_text", "openai", "fastapi"):
            assert banned not in _OPENING_SRC, banned


# ---------------------------------------------------------------------------
# フロント（discuss.js）
# ---------------------------------------------------------------------------


class TestChapterSkeletonUi:
    def test_section_is_rendered_right_after_the_question_section(self):
        block = _extract_js_function(_DISCUSS_JS, "function buildOpeningHtml(data) {")
        idx_question = block.index("renderQuestionSection(doc)")
        idx_chapters = block.index("renderChapterSkeletonSection(doc)")
        idx_thesis = block.index("renderThesisSection(doc)")
        assert idx_question < idx_chapters < idx_thesis

    def test_section_is_hidden_when_empty(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderChapterSkeletonSection(doc) {")
        assert "doc.chapter_skeleton" in fn
        assert 'if (!usable.length) return ""' in fn
        assert "論文の骨格（章の流れ）" in fn

    def test_label_and_summary_are_escaped_not_summarised(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderChapterSkeletonSection(doc) {")
        assert "esc(label)" in fn
        assert "esc(summary)" in fn
        # DTO の `summary` フィールドはそのまま出す。要約・和訳・切り詰めはしない。
        for forbidden in ("summarize", "summarise", "translate", "slice(0,", "substring("):
            assert forbidden not in fn, forbidden

    def test_block_type_is_secondary_not_the_headline(self):
        """block_type は A層の内部語彙。label / summary を主に見せる。"""
        fn = _extract_js_function(_DISCUSS_JS, "function renderChapterSkeletonSection(doc) {")
        assert "discuss-chapter-label" in fn
        assert "discuss-chapter-type" in fn
        assert fn.index("discuss-chapter-label") < fn.index("discuss-chapter-type")
        assert ".discuss-chapter-type {" in _STYLES_CSS

    def test_no_numbers_counts_or_detour_wording(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderChapterSkeletonSection(doc) {")
        for banned in ("confidence", "スコア", "寄り道", "%"):
            assert banned not in fn, banned
        # 件数バッジを作らない（DM6）。
        assert ".length +" not in fn

    def test_truncation_is_stated_without_numbers(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderChapterSkeletonSection(doc) {")
        assert "chapter_skeleton_truncated" in fn
        assert "主要なものに絞って表示しています" in fn
