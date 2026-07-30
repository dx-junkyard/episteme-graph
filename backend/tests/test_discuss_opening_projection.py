"""discuss 開幕画面「投影の是正」（Phase 0）+ `course_focus`（Phase 0b）のガードレール。

正本: `docs/features/discuss_opening_authoring_design.md` §2（画面に出すもの・主語で分ける）/
§3（Phase 0: 投影の是正）/ §8（ガードレール Phase 0）/ §10（Phase 0b）。

検証観点:
1. thesis_reconstruction / paper_skeleton artifact が既に持っている情報
   （`central_question` / `paper_goal` / `central_thesis.text` / `support_structure[].text` /
   `alternative_theses`）が DTO に出る（従来は捨てられていた）。
2. `alternative_theses` 由来の項目には出所ラベルが付き、無署名で「論文の主張」として
   並ばない（OA7: 選別せずラベルで区別する / DM1: 論文の内容は論文まで辿れること）。
3. 脆い箇所は主語（論文 / システム）で分離され、フロントが2区画に描画する
   （§0 欠陥1: 主語の混在）。
4. 内部語彙（atomic claim / リンク / レビュー待ち）が学習者向け文面に出ない。
5. **言語変換はしない**（§3 注意: Phase 0 で直るのは構成と主語であって言語ではない）。
6. `course_focus` は教員の任意入力のみで AI 生成経路が無く、未入力なら区画ごと非表示。
7. confidence 等の生数値は従来どおり出ない（OA6）。

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

from core import course_data as course_data_module  # noqa: E402
from core.discuss import opening  # noqa: E402
from tests.guardrail_helpers import assert_source_forbids, extract_function_source  # noqa: E402

_OPENING_SRC = (BACKEND / "core" / "discuss" / "opening.py").read_text(encoding="utf-8")
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")
_SCHEMAS_SRC = (BACKEND / "api" / "schemas.py").read_text(encoding="utf-8")
_DISCUSS_JS = (ROOT / "frontend" / "public" / "js" / "discuss.js").read_text(encoding="utf-8")
_ADMIN_JS = (ROOT / "frontend" / "public" / "js" / "admin.js").read_text(encoding="utf-8")
_STYLES_CSS = (ROOT / "frontend" / "public" / "css" / "styles.css").read_text(encoding="utf-8")


def _extract_js_function(src: str, signature: str) -> str:
    """`signature` から対応する閉じ `}` までを素朴な波括弧カウントで抽出する
    （test_discuss_phase2_ui_static.py と同じ流儀）。"""
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


# 英語原文をそのまま出す（和訳・要約しない）ことを確認するためのサンプル。
_ENGLISH_QUESTION = "How does the sum rule constrain the observable spectrum?"
_ENGLISH_THESIS = "The proposed sum rule predicts the observed spectrum within stated bounds."


def _thesis_artifact(**overrides) -> dict:
    artifact = {
        "central_question": _ENGLISH_QUESTION,
        "headline_claim": "headline fallback",
        "central_thesis": {
            "text": _ENGLISH_THESIS,
            "claim_ids": ["c1"],
            "equation_ids": ["eq1"],
            "reason": "because",
            "confidence": 0.82,
        },
        "alternative_theses": [
            {"text": "An alternative formulation emphasising the noise floor.", "reason": "r", "confidence": 0.4},
        ],
        "support_structure": {
            "assumptions": [
                {
                    "text": "The detector response is linear over the band.",
                    "claim_ids": ["a1"],
                    "equation_ids": [],
                    "support_type": "assumption_support",
                    "reason": "r",
                    "confidence": 0.6,
                }
            ],
        },
        "confidence": 0.7,
    }
    artifact.update(overrides)
    return artifact


def _skeleton_artifact(**overrides) -> dict:
    artifact = {
        "paper_goal": {"text": "Establish a predictive sum rule.", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
        "central_question": {"text": "Skeleton question fallback.", "evidence_block_ids": ["b1"], "reason": "", "confidence": 0.8},
    }
    artifact.update(overrides)
    return artifact


# ---------------------------------------------------------------------------
# Phase 0: 投影の是正（agent が既に合成していた文を捨てない）
# ---------------------------------------------------------------------------


class TestProjectedThesisFields:
    def test_central_question_projected(self):
        dto = opening.project_thesis(_thesis_artifact(), {}, {})
        assert dto["central_question"] == _ENGLISH_QUESTION

    def test_central_question_falls_back_to_paper_skeleton(self):
        dto = opening.project_thesis(
            _thesis_artifact(central_question=""), {}, {}, _skeleton_artifact()
        )
        assert dto["central_question"] == "Skeleton question fallback."

    def test_paper_goal_comes_from_paper_skeleton_artifact(self):
        """paper_goal は thesis_reconstruction artifact に存在しない（paper_skeleton が正本）。
        2つの artifact を併読して初めて「この論文が答えようとした問い」が組める。"""
        dto = opening.project_thesis(_thesis_artifact(), {}, {}, _skeleton_artifact())
        assert dto["paper_goal"] == "Establish a predictive sum rule."

    def test_paper_goal_empty_without_skeleton(self):
        dto = opening.project_thesis(_thesis_artifact(), {}, {})
        assert dto["paper_goal"] == ""
        assert dto["central_question"] == _ENGLISH_QUESTION  # thesis 側は独立に出る

    def test_central_thesis_text_projected(self):
        """従来は claim_ids → claim の生ラベルだけを出し、agent が合成した命題文を
        捨てていた（§3）。"""
        dto = opening.project_thesis(_thesis_artifact(), {"c1": "raw claim label"}, {})
        assert dto["central_thesis_text"] == _ENGLISH_THESIS
        # 合成文は claim の生ラベルを置き換えない（併記する）。
        assert dto["central_claims"] == [{"id": "c1", "label": "raw claim label"}]

    def test_central_thesis_text_falls_back_to_headline_claim(self):
        artifact = _thesis_artifact()
        artifact["central_thesis"]["text"] = ""
        dto = opening.project_thesis(artifact, {}, {})
        assert dto["central_thesis_text"] == "headline fallback"

    def test_support_entry_text_projected_with_its_refs(self):
        dto = opening.project_thesis(_thesis_artifact(), {"a1": "assumption label"}, {})
        section = dto["support_sections"][0]
        assert section["key"] == "assumptions"
        entries = section["entries"]
        assert entries[0]["text"] == "The detector response is linear over the band."
        assert entries[0]["items"] == [{"type": "claim", "id": "a1", "label": "assumption label"}]
        # 従来の flat items[] も残る（既存フロントのチップ表示の後方互換）。
        assert section["items"] == [{"type": "claim", "id": "a1", "label": "assumption label"}]

    def test_support_entry_kept_even_without_refs(self):
        """参照 id を持たない合成文も落とさない（P4）。"""
        artifact = _thesis_artifact(
            support_structure={"derivation_core": [{"text": "A synthesised sentence.", "claim_ids": [], "equation_ids": []}]}
        )
        dto = opening.project_thesis(artifact, {}, {})
        section = dto["support_sections"][0]
        assert section["items"] == []
        assert section["entries"][0]["text"] == "A synthesised sentence."

    def test_support_entries_capped(self):
        entries = [{"text": f"sentence {i}", "claim_ids": [], "equation_ids": []} for i in range(9)]
        dto = opening.project_thesis(_thesis_artifact(support_structure={"assumptions": entries}), {}, {})
        assert len(dto["support_sections"][0]["entries"]) == opening._MAX_SUPPORT_ENTRIES_PER_SECTION

    def test_language_is_not_translated_or_summarised(self):
        """§3 注意: Phase 0 で直るのは構成と主語であって言語ではない。原文を verbatim で出す。"""
        dto = opening.project_thesis(_thesis_artifact(), {}, {}, _skeleton_artifact())
        assert dto["central_question"] == _ENGLISH_QUESTION
        assert dto["central_thesis_text"] == _ENGLISH_THESIS
        assert len(dto["central_thesis_text"]) == len(_ENGLISH_THESIS)


class TestAlternativeTheses:
    def test_alternatives_projected_with_attribution_label(self):
        dto = opening.project_thesis(_thesis_artifact(), {}, {})
        alt = dto["alternatives"][0]
        assert alt["text"] == "An alternative formulation emphasising the noise floor."
        # 出典（claim_ids / evidence_block_ids）を持たない artifact なので、
        # 出所ラベルを必ず添える（無署名で論文の主張として出さない）。
        assert alt["attribution_label"]
        assert "AI" in alt["attribution_label"]
        assert "未確認" in alt["attribution_label"]

    def test_every_alternative_carries_the_label(self):
        artifact = _thesis_artifact(
            alternative_theses=[{"text": "alt one"}, {"text": "alt two"}, "alt three", {"text": ""}]
        )
        dto = opening.project_thesis(artifact, {}, {})
        assert [a["text"] for a in dto["alternatives"]] == ["alt one", "alt two", "alt three"]
        assert all(a["attribution_label"] for a in dto["alternatives"])

    def test_alternatives_capped(self):
        artifact = _thesis_artifact(alternative_theses=[{"text": f"alt {i}"} for i in range(8)])
        dto = opening.project_thesis(artifact, {}, {})
        assert len(dto["alternatives"]) == opening._MAX_ALTERNATIVES

    def test_alternative_reason_and_confidence_not_projected(self):
        dto = opening.project_thesis(_thesis_artifact(), {}, {})
        assert set(dto["alternatives"][0].keys()) == {"text", "attribution_label"}

    def test_thesis_with_only_question_is_not_empty(self):
        """claim リンクが1つも無くても、問い・命題文があれば開幕画面は成立する。"""
        dto = opening.project_thesis({"central_question": "Q?"}, {}, {})
        assert opening._thesis_is_empty(dto) is False


class TestNoNumericLeakInNewFields:
    def test_no_forbidden_numeric_keys_anywhere(self):
        dto = opening.project_thesis(_thesis_artifact(), {"c1": "x", "a1": "y"}, {}, _skeleton_artifact())
        stripped = opening._strip_numeric_keys(dto)

        def _walk(node):
            if isinstance(node, dict):
                for key in ("confidence", "load_score", "score"):
                    assert key not in node
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(stripped)
        # 射影自体がホワイトリストなので、strip 前でも紛れ込んでいない。
        _walk(dto)


# ---------------------------------------------------------------------------
# §7（Phase 3）: 承認済み素材「議論のきっかけ」の射影
#
# この区画だけは投影ではなく element_explanations の approved 行の配信。承認済み以外を
# 出さないこと（OA2）・Phase 0 を劣化させないこと（OA4）等の不変条項は
# test_discuss_opening_authoring_guardrails.py 側で固定し、ここでは射影の形（並び・上限・
# 出所表示）を確認する。
# ---------------------------------------------------------------------------


def _approved_seed_row(**overrides) -> dict:
    row = {
        "id": "row-1",
        "document_id": "doc-1",
        "element_type": "document",
        "element_id": "doc-1",
        "kind": "contextual",
        "role": "discussion_seed",
        "status": "approved",
        "body": "この前提を外すと、結論はどこまで残ると考えますか？",
        "evidence": {"evidence_quote": "The detector response is linear.", "confidence": 0.6},
        "created_at": "2026-07-30T00:00:00+00:00",
    }
    row.update(overrides)
    return row


class TestDiscussionSeedProjection:
    def test_body_and_quote_projected_with_attribution(self):
        seeds = opening.project_discussion_seeds([_approved_seed_row()])
        assert seeds[0]["body"] == "この前提を外すと、結論はどこまで残ると考えますか？"
        # evidence_quote は「論文まで辿れる」ことの明示なので出す（DM1）。
        assert seeds[0]["evidence_quote"] == "The detector response is linear."
        assert seeds[0]["authored"] is True
        assert "担当教員" in seeds[0]["authored_by_label"]

    def test_missing_quote_is_empty_not_fabricated(self):
        seeds = opening.project_discussion_seeds([_approved_seed_row(evidence={})])
        assert seeds[0]["evidence_quote"] == ""

    def test_blank_body_is_not_delivered(self):
        assert opening.project_discussion_seeds([_approved_seed_row(body="   ")]) == []

    def test_order_is_deterministic_for_siblings_of_one_transaction(self):
        """同一トランザクションで投入された兄弟行は created_at が同値になるため、
        DB の ORDER BY だけでは並びが決まらない（body / id まで含めて全順序にする）。"""
        rows = [
            _approved_seed_row(id="r3", body="問い C？"),
            _approved_seed_row(id="r1", body="問い A？"),
            _approved_seed_row(id="r2", body="問い B？"),
        ]
        first = [s["body"] for s in opening.project_discussion_seeds(rows)]
        second = [s["body"] for s in opening.project_discussion_seeds(list(reversed(rows)))]
        assert first == second == ["問い A？", "問い B？", "問い C？"]

    def test_newer_material_comes_first(self):
        rows = [
            _approved_seed_row(id="old", body="古い問い？", created_at="2026-07-29T00:00:00+00:00"),
            _approved_seed_row(id="new", body="新しい問い？", created_at="2026-07-31T00:00:00+00:00"),
        ]
        assert [s["body"] for s in opening.project_discussion_seeds(rows)] == [
            "新しい問い？", "古い問い？",
        ]

    def test_cap_keeps_the_newest_material(self):
        """上限で切るとき、古い承認済み素材が新しい承認を押し出さないこと
        （昇順で切ると再解析後の承認が永久に画面へ出ない）。"""
        rows = [
            _approved_seed_row(id=f"r{i}", body=f"問い{i}？", created_at=f"2026-07-1{i}T00:00:00+00:00")
            for i in range(9)
        ]
        seeds = opening.project_discussion_seeds(rows)
        assert len(seeds) == opening._MAX_DISCUSSION_SEEDS
        assert [s["body"] for s in seeds] == ["問い8？", "問い7？", "問い6？", "問い5？"]

    def test_non_dict_rows_ignored(self):
        assert opening.project_discussion_seeds([None, "x", 1]) == []
        assert opening.project_discussion_seeds(None) == []


# ---------------------------------------------------------------------------
# §0 欠陥1 / §3: 脆い箇所の主語の分離と内部語彙の平易化
# ---------------------------------------------------------------------------


class TestFragileSubjectSeparation:
    def test_assumption_subject_is_the_paper(self):
        items = [{"target_id": "t1", "statement": "前提の文", "verification_status": "untested"}]
        points, _ = opening.project_fragile_points(items, {})
        assert points[0]["kind"] == "assumption"
        assert points[0]["subject"] == opening.FRAGILE_SUBJECT_PAPER == "paper"

    def test_backbone_node_subject_is_the_system(self):
        backbone = {
            "doc1": [
                {
                    "node_id": "n1", "label": "Elimination", "review_status": "review_required",
                    "source_backing_status": "review_required", "review_reasons": ["missing_atomic_claim"],
                }
            ]
        }
        points, _ = opening.project_fragile_points([], backbone)
        assert points[0]["kind"] == "backbone_node"
        assert points[0]["subject"] == opening.FRAGILE_SUBJECT_SYSTEM == "system"

    def test_system_fact_line_names_the_analysis_as_the_subject(self):
        """「レビュー待ちの箇所です」では誰が何を待っているのか学習者に分からない。
        主語（解析）が読み取れる文にする。"""
        node = {"node_id": "n1", "label": "L", "review_status": "review_required", "review_reasons": []}
        line = opening._backbone_fact_line(node)
        assert "解析" in line
        assert "レビュー待ち" not in line

    def test_review_reason_phrases_are_plain_japanese(self):
        for reason, phrase in opening._REVIEW_REASON_FACT_PHRASES.items():
            assert phrase, reason
            assert "atomic" not in phrase, reason
            assert "claim" not in phrase, reason
            assert "リンク" not in phrase, reason

    def test_unknown_review_reason_kept_verbatim(self):
        """未知の reason コードは落とさずそのまま出す（情報を落とさない）。"""
        node = {"review_reasons": ["some_new_reason"]}
        assert "some_new_reason" in opening._backbone_fact_line(node)

    def test_assumption_fact_lines_avoid_internal_vocabulary(self):
        for item in (
            {"scope_count_is_zero": True},
            {"verification_status": "untested"},
            {"verification_status": "refuted"},
            {"verification_status": "indirectly_supported"},
        ):
            line = opening._assumption_fact_line(item)
            assert line
            assert "記帳" not in line
            assert "スコープ" not in line

    def test_no_internal_vocabulary_in_learner_facing_strings(self):
        """学習者に出る文字列（reason 事実文の辞書 + 前置き定数）に内部語彙が残っていない
        こと。モジュールのコメント・docstring は説明のため旧語彙に言及してよいので、
        検査対象は実際に配信される文字列だけに限る。"""
        learner_strings = list(opening._REVIEW_REASON_FACT_PHRASES.values())
        learner_strings.append(opening._SYSTEM_UNCONFIRMED_PREFIX)
        learner_strings.append(opening._ALTERNATIVE_ATTRIBUTION_LABEL)
        for item in (
            {"scope_count_is_zero": True},
            {"verification_status": "untested"},
            {"verification_status": "refuted"},
            {"verification_status": "indirectly_supported"},
        ):
            learner_strings.append(opening._assumption_fact_line(item))
        for text in learner_strings:
            assert_source_forbids(text, ("atomic claim", "レビュー待ち", "記帳"), context=text)


# ---------------------------------------------------------------------------
# Phase 0b: course_focus（教員の任意入力・AI 生成なし）
# ---------------------------------------------------------------------------


class TestCourseFocusAccessor:
    def test_accessor_reads_and_trims(self):
        assert course_data_module.course_focus({"course_focus": "  議論したいこと  "}) == "議論したいこと"

    def test_missing_or_invalid_returns_empty_string(self):
        assert course_data_module.course_focus(None) == ""
        assert course_data_module.course_focus({}) == ""
        assert course_data_module.course_focus({"course_focus": None}) == ""
        assert course_data_module.course_focus("not a dict") == ""

    def test_field_declared_on_course_data_model(self):
        validated = course_data_module.validate_course_data(
            {"id": "c1", "title": "t", "course_focus": "テーマ"}
        )
        assert validated.course_focus == "テーマ"

    def test_build_opening_includes_course_focus(self, monkeypatch):
        monkeypatch.setattr(opening, "_document_titles", lambda ids: {})
        monkeypatch.setattr(opening, "_compile_open_assumptions", lambda course_id: [])
        result = opening.build_opening("course1", [], course_focus="  このコースで議論したいこと  ")
        assert result["course_focus"] == "このコースで議論したいこと"
        # 教員の入力だけでも画面は成立する（投影が空でも黙って落とさない）。
        assert result["available"] is True

    def test_build_opening_course_focus_defaults_to_empty(self, monkeypatch):
        monkeypatch.setattr(opening, "_document_titles", lambda ids: {})
        monkeypatch.setattr(opening, "_compile_open_assumptions", lambda course_id: [])
        result = opening.build_opening("course1", [])
        assert result["course_focus"] == ""
        assert result["available"] is False

    def test_no_llm_generation_path_for_course_focus(self):
        """§9 非スコープ: course_focus の AI 生成はしない（教員の任意入力のみ）。"""
        assert "course_focus" in _OPENING_SRC
        assert_source_forbids(_OPENING_SRC, ("generate_text", "llm"))


class TestCourseFocusSaveRoute:
    def test_request_schema_has_course_focus(self):
        assert "course_focus: str | None = None" in _SCHEMAS_SRC

    def test_update_course_sets_and_clears_course_focus(self):
        fn_src = extract_function_source(_LEARNING_SRC, "update_course")
        assert "if body.course_focus is not None:" in fn_src
        assert 'data["course_focus"] = focus' in fn_src
        assert 'data.pop("course_focus", None)' in fn_src
        assert "_MAX_COURSE_FOCUS_CHARS" in fn_src
        assert "status_code=422" in fn_src

    def test_no_new_endpoint_added_for_course_focus(self):
        """既存のコース更新経路に乗せる（新 API を作らない）。"""
        assert "discuss/course-focus" not in _LEARNING_SRC
        assert "course-focus" not in _ADMIN_SRC

    def test_opening_route_passes_accessor_result(self):
        fn_src = extract_function_source(_LEARNING_SRC, "get_discussion_opening")
        assert "course_focus=course_focus(course_data)" in fn_src
        # course_data への素の dict アクセスを書かない（Tier 3-18）。
        assert 'course_data.get("course_focus")' not in fn_src

    def test_admin_course_list_projects_course_focus_for_prefill(self):
        assert '"course_focus": course_focus(data),' in _ADMIN_SRC


class TestCourseFocusAdminUi:
    def test_owner_row_has_focus_button(self):
        assert "cm-focus-btn" in _ADMIN_JS
        assert "議論テーマ" in _ADMIN_JS

    def test_modal_saves_via_existing_course_update_api(self):
        block = _extract_js_function(_ADMIN_JS, "function openCourseFocusModal(courseId, courseTitle) {")
        assert '"/learning/courses/" + encodeURIComponent(courseId)' in block
        assert '"PUT"' in block
        assert "course_focus: text" in block

    def test_modal_prefills_current_value(self):
        block = _extract_js_function(_ADMIN_JS, "function openCourseFocusModal(courseId, courseTitle) {")
        assert "course.course_focus" in block
        assert "input.value = currentFocus" in block

    def test_modal_states_no_ai_generation_and_optionality(self):
        block = _extract_js_function(_ADMIN_JS, "function openCourseFocusModal(courseId, courseTitle) {")
        assert "AI は生成しません" in block
        assert "空欄のままでも構いません" in block


# ---------------------------------------------------------------------------
# フロント（discuss.js）: 主語ごとの区画・出所ラベル・「くわしく見る」
# ---------------------------------------------------------------------------


class TestOpeningUiSubjectSections:
    def test_course_focus_section_is_first_and_hidden_when_empty(self):
        block = _extract_js_function(_DISCUSS_JS, "function buildOpeningHtml(data) {")
        idx_focus = block.index("renderCourseFocusSection(focus)")
        idx_first_move = block.index("renderFirstMoveSection()")
        assert idx_focus < idx_first_move
        focus_fn = _extract_js_function(_DISCUSS_JS, "function renderCourseFocusSection(focus) {")
        assert 'if (!focus) return ""' in focus_fn
        assert "このコースで議論したいこと" in focus_fn
        # 主語（教員）を明示する。
        assert "担当教員" in focus_fn

    def test_question_section_renders_central_question_and_paper_goal(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderQuestionSection(doc) {")
        assert "この論文が答えようとした問い" in fn
        assert "thesis.central_question" in fn
        assert "thesis.paper_goal" in fn
        assert 'if (!question && !goal) return ""' in fn

    def test_thesis_section_renders_synthesised_statement(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderThesisSection(doc) {")
        assert "thesis.central_thesis_text" in fn
        # 合成文は claim の生ラベルを置き換えない（両方描く）。
        assert "central_claims" in fn

    def test_two_fragile_sections_split_by_kind(self):
        paper_fn = _extract_js_function(_DISCUSS_JS, "function renderPaperUnverifiedSection(fragilePoints) {")
        system_fn = _extract_js_function(_DISCUSS_JS, "function renderSystemUnconfirmedSection(fragilePoints, documentId) {")
        assert 'f.kind === "assumption"' in paper_fn
        assert "この論文が確かめていないこと" in paper_fn
        assert 'f.kind === "backbone_node"' in system_fn
        assert "まだ確認できていないところ" in system_fn
        # システム側は「論文の弱点ではない」ことを本文で言う（主語の明示）。
        assert "論文の弱点ではありません" in system_fn
        assert "解析" in system_fn

    def test_course_level_assumptions_are_reachable_outside_the_documents_loop(self):
        """D層台帳由来の項目は document_id を持たないため、document 一致フィルタの中に
        置くと画面に一度も出ない（旧実装の欠落）。OA7: 到達できない情報を作らない。"""
        block = _extract_js_function(_DISCUSS_JS, "function buildOpeningHtml(data) {")
        idx_loop_end = block.index("renderPaperUnverifiedSection(fragile)")
        idx_docs = block.index("docs.forEach(")
        assert idx_docs < idx_loop_end
        docs_loop = block[idx_docs:idx_loop_end]
        assert "renderPaperUnverifiedSection" not in docs_loop

    def test_alternatives_section_labels_the_source(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderAlternativesSection(thesis) {")
        assert "別の見方（AI の提示）" in fn
        assert "alt.attribution_label" in fn
        assert "論文の主張そのものではありません" in fn

    def test_details_section_holds_the_rest(self):
        """開幕画面に一度に並べるのは少数。残りは同じ画面の「くわしく見る」から到達できる。"""
        block = _extract_js_function(_DISCUSS_JS, "function buildOpeningHtml(data) {")
        assert "くわしく見る" in block
        assert "<details" in block
        for renderer in (
            "renderBackboneSection(doc)",
            "renderSupportDetails(",
            "renderAlternativesSection(doc.thesis)",
            "renderSystemUnconfirmedSection(fragile, doc.document_id)",
        ):
            assert renderer in block, renderer

    def test_support_entries_rendered_as_sentences_with_chip_fallback(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderSupportDetails(sections) {")
        assert "sec.entries" in fn
        assert "supportEntryHtml(entry)" in fn
        # entries が無い旧データは従来のチップ表示へ縮退する（劣化許容・落とさない）。
        assert "discussChip(item && item.label)" in fn

    def test_new_text_is_escaped_not_summarised(self):
        for signature in (
            "function renderQuestionSection(doc) {",
            "function renderAlternativesSection(thesis) {",
            "function renderCourseFocusSection(focus) {",
            "function supportEntryHtml(entry) {",
        ):
            fn = _extract_js_function(_DISCUSS_JS, signature)
            assert "esc(" in fn
            for forbidden in ("summar", "translate", "slice(0,"):
                assert forbidden not in fn, signature

    def test_no_confidence_or_numeric_display_in_new_sections(self):
        for token in ("confidence", "スコア", "件"):
            assert token not in _extract_js_function(
                _DISCUSS_JS, "function renderAlternativesSection(thesis) {"
            ), token

    def test_seeds_section_is_in_the_prime_position_and_hidden_when_empty(self):
        """承認済み素材は「くわしく見る」の中ではなく問い・主張と並ぶ位置に出す（§7）。
        承認済みゼロなら区画ごと出さない（Phase 0 の画面と同一・OA4）。"""
        block = _extract_js_function(_DISCUSS_JS, "function buildOpeningHtml(data) {")
        idx_seeds = block.index("renderDiscussionSeedsSection(doc)")
        idx_thesis = block.index("renderThesisSection(doc)")
        idx_details = block.index('<details class="discuss-more">')
        assert idx_thesis < idx_seeds < idx_details

        fn = _extract_js_function(_DISCUSS_JS, "function renderDiscussionSeedsSection(doc) {")
        assert "doc.discussion_seeds" in fn
        assert 'if (!usable.length) return ""' in fn
        assert "議論のきっかけ" in fn

    def test_seed_click_sends_the_question_verbatim(self):
        """seed の body は「立場を求める問い」そのものなので askText() で包まない
        （既存の data-discuss-ask 経路に乗せ、観測イベント名も増やさない）。"""
        fn = _extract_js_function(_DISCUSS_JS, "function renderDiscussionSeedsSection(doc) {")
        assert "data-discuss-ask=" in fn
        assert "esc(s.body)" in fn
        assert "askText(" not in fn
        bind = _extract_js_function(_DISCUSS_JS, "function bindOpeningEvents(containerEl) {")
        assert "opening_starter_clicked" in bind

    def test_seeds_section_shows_the_server_signature(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderDiscussionSeedsSection(doc) {")
        assert "s.authored_by_label" in fn
        assert "discuss-seed-attribution" in fn
        # 文面はサーバ側の1定数が正本（フロントで組み立てない）。
        assert "担当教員" not in fn

    def test_seeds_text_is_escaped_and_not_summarised(self):
        fn = _extract_js_function(_DISCUSS_JS, "function renderDiscussionSeedsSection(doc) {")
        assert "esc(s.body)" in fn
        assert "esc(s.evidence_quote)" in fn
        for forbidden in ("summar", "translate", "slice(0,", "confidence", "スコア"):
            assert forbidden not in fn, forbidden

    def test_css_classes_for_new_sections_defined(self):
        for selector in (
            ".discuss-section-seeds",
            ".discuss-seed-body",
            ".discuss-seed-quote",
            ".discuss-seed-attribution",
            ".discuss-section-focus",
            ".discuss-focus-text",
            ".discuss-paper-goal",
            ".discuss-support-entry-text",
            ".discuss-alt-item",
            ".discuss-attribution",
            ".discuss-more",
        ):
            assert selector in _STYLES_CSS, selector

    def test_system_section_uses_no_alarm_colour(self):
        """解析の未確認は事実として並べる（警告色・強調色にしない）。"""
        rule = _STYLES_CSS.split(".discuss-attribution {")[1].split("}")[0]
        for alarm in ("red", "#f00", "warning"):
            assert alarm not in rule.lower()
