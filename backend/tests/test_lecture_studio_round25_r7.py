"""第 25 周 面 D 第 7 波（IK-1053 / IK-1054 / IK-0976 の残り / IK-0571 の studio 側）の再現テスト。

- IK-1053（オーナー判断: 発行は所有者の明示操作のまま）: 版ありのコースで、いまの内容（HEAD）が受講者の
  見ている版と違うかを事実文 ``unpublished_changes_note`` にし、所有者にだけ［版の管理を開く］（V層
  ``Versioning.openModal``）の入口 ``versions_entry`` を添える。自動発行・督促はしない。
- IK-1054: 「付録が古いトピック」の題名を 1 つの括弧に連結しない。知らせは「付録が古いトピックがあります
  （下の一覧）」、題名は折りたたみの一覧（1 行 1 題名・章.トピックの番号）。
- IK-0976 の残り: 導入・解説は 2 文まで本体と結ぶ。箇条書きの直後の解説は箇条書きと結ぶ。
- IK-0571 の studio 側: ⚓ の部品名は表示名（display_label）を優先し、原文の言語のままなら「（原文）」の印。
  主張・位置づけ・記号の使われ方の例文も同じ印（翻訳しない・LLM 0 回）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import component_context  # noqa: E402
from core import course_content_builder as ccb  # noqa: E402
from core import lecture  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402
from core import symbol_lookup  # noqa: E402
from core.label_vocab import ORIGINAL_TEXT_MARK  # noqa: E402
from core.text_excerpt import is_untranslated_text  # noqa: E402
from tests.guardrail_helpers import extract_function_source  # noqa: E402

FRONTEND = ROOT / "frontend" / "public"
STUDIO_JS = (FRONTEND / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
VERSIONING_JS = (FRONTEND / "js" / "versioning.js").read_text(encoding="utf-8")
TOPICS_SRC = (BACKEND / "api" / "routes" / "lecture_studio" / "topics.py").read_text(encoding="utf-8")


def _js_fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n  function [A-Za-z0-9_]+\(", src[start + 10:])
    return src[start: start + 10 + nxt.start()] if nxt else src[start:]


# ---------------------------------------------------------------------------
# IK-1053: 未発行の変更の事実と版の管理への入口
# ---------------------------------------------------------------------------


class TestUnpublishedChanges:
    SNAP = {"title": "T", "description": "", "data": {"topics": [{"id": "t1", "student_material": "a"}]}}

    def test_comparison_is_by_snapshot_content(self):
        same = {"title": "T", "description": "", "data": {"topics": [{"student_material": "a", "id": "t1"}]}}
        changed = {"title": "T", "description": "", "data": {"topics": [{"id": "t1", "student_material": "b"}]}}
        assert studio_state.course_has_unpublished_changes(self.SNAP, same) is False
        assert studio_state.course_has_unpublished_changes(self.SNAP, changed) is True
        assert studio_state.course_has_unpublished_changes(None, same) is None

    def test_release_delivery_carries_note_and_owner_entry(self):
        facts = studio_state.topic_save_facts(is_owner=True, release_version_no=2, unpublished_changes=True)
        delivery = facts["delivery"]
        assert delivery["status"] == "release"
        assert delivery["unpublished_changes"] is True
        assert delivery["unpublished_changes_note"] == (
            "版 2 の発行の後に保存した変更があります。受講者はまだ版 2 を見ています。"
        )
        assert facts["unpublished_changes_note"] == delivery["unpublished_changes_note"]
        assert delivery["versions_entry"]["label"] == "版の管理を開く"
        assert facts["versions_entry"] == delivery["versions_entry"]

    def test_no_changes_note(self):
        facts = studio_state.topic_save_facts(is_owner=True, release_version_no=2, unpublished_changes=False)
        # 第 8 波（IK-1070）: 事実文と指示文を同じ値から作る（「発行は要りません」・指示文なし）。
        assert facts["delivery"]["unpublished_changes_note"] == "いまの内容は版 2 と同じです。発行は要りません。"
        assert "発行してください" not in facts["delivery"]["message"]

    def test_unknown_says_nothing_and_editor_has_no_entry(self):
        facts = studio_state.topic_save_facts(is_owner=False, release_version_no=1, unpublished_changes=None)
        assert "unpublished_changes_note" not in facts["delivery"]
        assert "versions_entry" not in facts["delivery"]
        assert "versions_entry" not in facts

    def test_no_release_no_note(self):
        facts = studio_state.topic_save_facts(is_owner=True, delivered=True, unpublished_changes=True)
        assert facts["delivery"]["status"] == "immediate"
        assert "unpublished_changes_note" not in facts["delivery"]
        assert "versions_entry" not in facts

    def test_route_compares_active_release_with_head(self):
        src = extract_function_source(TOPICS_SRC, "_topic_save_facts_for")
        # 第 25 周 第 9 波（IK-1079）: 比較は core の 1 本（版の管理モーダルと共有）。
        assert "course_release_comparison(" in src
        assert "unpublished_changes=unpublished_changes" in src
        import inspect

        from core import lecture_studio_state as _st

        core_src = inspect.getsource(_st.course_release_comparison)
        assert "get_release(" in core_src and "build_course_snapshot(" in core_src
        assert "course_has_unpublished_changes(" in core_src
        # 自動発行しない（版の発行 API を呼ばない）。
        assert "publish_release" not in TOPICS_SRC

    def test_studio_draws_note_and_entry(self):
        fn = _js_fn(STUDIO_JS, "lsTopicDeliveryInnerHtml") + _js_fn(STUDIO_JS, "lsUnpublishedChangesHtml")
        assert "unpublished_changes_note" in fn
        assert 'data-ui-anchor="lecture-studio.open-versions"' in fn
        assert "versions_entry" in fn
        bind = _js_fn(STUDIO_JS, "lsBindOpenVersionsButton")
        assert 'window.Versioning.openModal("course"' in bind
        assert "onClose" in bind
        assert "data-topic-delivery-block" in STUDIO_JS

    def test_versioning_modal_calls_on_close(self):
        fn = _js_fn(VERSIONING_JS, "openModal")
        assert "options.onClose" in fn
        assert "closeModal()" in fn


# ---------------------------------------------------------------------------
# IK-1054: 付録が古いトピックは一覧で
# ---------------------------------------------------------------------------


class TestAppendixStaleList:
    def test_notice_points_to_list(self):
        assert studio_state.APPENDIX_STALE_COURSE_NOTICE.startswith("付録が古いトピックがあります（下の一覧）")
        assert "書き直さない" in studio_state.APPENDIX_STALE_COURSE_NOTICE

    def test_route_returns_list_summary(self):
        assert '"appendix_stale_list_summary": studio_state.APPENDIX_STALE_LIST_SUMMARY' in TOPICS_SRC

    def test_studio_renders_one_title_per_item(self):
        header = _js_fn(STUDIO_JS, "lsRenderCourseStructure")
        assert 'join(" / ")' not in header.split("appendixStaleHtml")[1].split("var html")[0]
        fn = _js_fn(STUDIO_JS, "lsAppendixStaleListHtml")
        assert "<details" in fn and "<li>" in fn
        assert 'data-ui-anchor="lecture-studio.appendix-stale-list"' in fn
        assert "(p.ci + 1)" in fn and "(p.ti + 1)" in fn
        assert ".ls-course-topic[data-chapter=" in _js_fn(STUDIO_JS, "lsBindAppendixStaleList")


# ---------------------------------------------------------------------------
# IK-0976 の残り: 2 文の導入・解説・箇条書きの直後の解説
# ---------------------------------------------------------------------------


class TestReadingBlocksTwoSentences:
    def test_heading_two_sentence_lead_joins_table(self):
        units = ["## 2. 向きはランダムではない", "観測は次の表です。向きの偏りに注目します。", "| a | b |\n|---|---|", "## 3. 次"]
        assert lecture._reading_blocks(units) == [(0, 3), (3, 4)]

    def test_two_sentence_explanation_after_formula(self):
        units = ["損失は次です。", "[[FORMULA_0]]", "大事なのは微分できることです。だから勾配で学べます。", "次の段落。"]
        assert lecture._reading_blocks(units) == [(0, 3), (3, 4)]

    def test_explanation_after_list_joins_list(self):
        units = ["本文です。", "- J=3–2 の線\n- J=1–0 の線", "ここで「J=3–2」は回転の遷移です。強度比に使います。", "次の段落。"]
        assert lecture._reading_blocks(units) == [(0, 1), (1, 3), (3, 4)]

    def test_three_sentences_are_not_a_lead(self):
        units = ["## 見出し", "一文目。二文目。三文目。", "- 項目"]
        assert lecture._reading_blocks(units) == [(0, 2), (2, 3)]

    def test_oversized_block_still_splits_by_paragraph(self):
        body = "あ" * 230 + "。"
        units = ["## 見出し", "導入です。続きです。", "- " + "い" * 1500]
        groups = lecture._group_units_by_size(units, 600)
        assert groups[0][0] == 0 and groups[-1][1] == 3
        assert len(groups) >= 2
        assert body  # 長さの前提


# ---------------------------------------------------------------------------
# IK-0571 の studio 側: 原文の印
# ---------------------------------------------------------------------------


class TestOriginalTextMark:
    def test_judgement(self):
        assert is_untranslated_text("TOV residual loss at collocation points")
        assert is_untranslated_text("A second network solves $P(r)$ for the profile.")
        assert not is_untranslated_text("重力の式")
        assert not is_untranslated_text("$P(r)$")
        assert not is_untranslated_text("P")
        assert ORIGINAL_TEXT_MARK == "（原文）"

    def test_evidence_item_prefers_display_label_and_marks_english(self):
        topic = {
            "evidence_links": [
                {"kind": "component", "id": "comp_1", "label": "TOV residual loss"},
                {"kind": "component", "id": "comp_2", "label": "重力の釣り合い"},
            ],
        }
        items = {i["id"]: i for i in ccb.build_topic_evidence_items(topic)}
        assert items["comp_1"]["title_original_mark"] == ORIGINAL_TEXT_MARK
        assert "title_original_mark" not in items["comp_2"]

    def test_component_claims_and_role_marks(self):
        claims = component_context._build_claims(
            {"linked_claim_ids": ["c1", "c2"]},
            {"c1": {"text": "A second network solves the TOV equations."}, "c2": {"text": "質量は半径で決まる。"}},
        )
        assert claims[0]["original_mark"] == ORIGINAL_TEXT_MARK
        assert "original_mark" not in claims[1]

    def test_narrative_role_prefers_existing_japanese_candidate(self):
        role = component_context._narrative_role(
            "db1",
            {"thesis_context": {"role_in_thesis": "Directly supports the central thesis."},
             "summary": "中心命題を支える損失の部品です。"},
            {},
            {},
        )
        assert role == "中心命題を支える損失の部品です。"
        role_en = component_context._narrative_role(
            "db1", {"thesis_context": {"role_in_thesis": "Directly supports."}}, {}, {}
        )
        assert role_en == "Directly supports."

    def test_build_context_sets_marks(self):
        src = extract_function_source(
            (BACKEND / "core" / "component_context.py").read_text(encoding="utf-8"), "build_component_context"
        )
        assert '"label_original_mark"' in src and '"narrative_role_original_mark"' in src

    def test_symbol_mentions_marked(self):
        class _Rows:
            def __init__(self, rows):
                self._rows = rows

            def mappings(self):
                return self

            def fetchall(self):
                return self._rows

        class _Session:
            def execute(self, *_a, **_k):
                return _Rows([
                    {"document_id": "d", "text": "The pressure P vanishes at the surface."},
                    {"document_id": "d", "text": "圧力 P は表面でゼロになる。"},
                ])

        mentions = symbol_lookup._claim_mentions(
            _Session(), ["11111111-1111-1111-1111-111111111111"], symbol="P"
        )
        assert mentions and mentions[0]["original_mark"] == ORIGINAL_TEXT_MARK
        assert all("original_mark" not in m for m in mentions[1:])

    def test_learner_ui_draws_server_mark(self):
        assert "evidenceItem.display_label || evidenceItem.title" in _js_fn(APP_JS, "renderMaterialEvidenceChip")
        assert "title_original_mark" in _js_fn(APP_JS, "renderMaterialEvidenceChip")
        assert "label_original_mark" in _js_fn(APP_JS, "renderComponentContextPanel")
        assert "data-display-label" in _js_fn(APP_JS, "renderComponentContextPanel")
        assert "original_mark" in _js_fn(APP_JS, "renderContextClaimSection")
        assert "mention.original_mark" in APP_JS
        # 日本語の印を JS に焼き込まない（サーバの値を素通し）。
        assert "（原文）" not in APP_JS
        assert "（原文）" not in STUDIO_JS


def test_anchors_registered_and_documented():
    from core.help_kb.admin_ui_anchors import ADMIN_UI_ANCHORS

    manual = (ROOT / "docs" / "manual" / "teacher" / "14-admin-lecture-studio.md").read_text(encoding="utf-8")
    for anchor, section in (
        ("lecture-studio.appendix-stale-list", "appendix-stale-list"),
        ("lecture-studio.open-versions", "open-versions"),
    ):
        assert ADMIN_UI_ANCHORS[anchor].endswith("#" + section)
        assert "{#" + section + "}" in manual
        assert f'data-ui-anchor="{anchor}"' in STUDIO_JS


def test_cache_busting_versions():
    admin_html = (FRONTEND / "admin.html").read_text(encoding="utf-8")
    index_html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert re.search(r"admin-lecture-studio\.js\?v=[^\"]*studio7", admin_html)
    assert re.search(r"versioning\.js\?v=[^\"]*studio7", admin_html)
    assert re.search(r"app\.js\?v=[^\"]*studio7", index_html)
