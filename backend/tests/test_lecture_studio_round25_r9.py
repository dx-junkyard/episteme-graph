"""第 25 周 面 D 第 9 波（IK-1079 / IK-1080 / IK-1081 / IK-0872 t11 / IK-0571 記号）の再現テスト。

- IK-1079: 未発行の変更の事実文と［版の管理を開く］を描く部品は 1 つで、コースの見出し（所有者・変更あり）・
  一括付け直しの知らせ・版の管理モーダル（version-state）が同じ正本の文を通す。
- IK-1080: 一括付け直しの知らせは結果の分岐（外した / 作り直した / 新しく付けた）ごとに段を分ける。
- IK-1081: 是正前に保存された「注」の段落の作業メモは受講者向けの本文（``topic_student_material``）から
  外し（保存物は書き直さない）、教員には事実文で言う。書き換えのプロンプトにも写さない。
- IK-0872 t11: 学習回数 ``N = 15`` を、本文の ``N_c`` / ``\\mathcal{N}_{\\mathrm{TOV}}`` を理由に付録へ選ばない。
- IK-0571: 記号の吹き出しの定義文にも「（原文）」の印。
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

from core import course_content_builder as ccb  # noqa: E402
from core import lecture  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402

FRONTEND = ROOT / "frontend" / "public"
STUDIO_JS = (FRONTEND / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
VERSIONING_JS = (FRONTEND / "js" / "versioning.js").read_text(encoding="utf-8")
TOPICS_SRC = (BACKEND / "api" / "routes" / "lecture_studio" / "topics.py").read_text(encoding="utf-8")
VERSIONING_SRC = (BACKEND / "api" / "routes" / "versioning.py").read_text(encoding="utf-8")
LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
SYMBOL_SRC = (BACKEND / "core" / "symbol_lookup.py").read_text(encoding="utf-8")


def _js_fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n  function [A-Za-z0-9_]+\(", src[start + 10:])
    return src[start: start + 10 + nxt.start()] if nxt else src[start:]


# ---------------------------------------------------------------------------
# IK-1079
# ---------------------------------------------------------------------------


class TestUnpublishedFactOneSource:
    def test_facts_from_one_function(self):
        assert studio_state.unpublished_changes_facts(5, True)["unpublished_changes_note"] == (
            studio_state.TOPIC_UNPUBLISHED_CHANGES_NOTES[True].format(version=5)
        )
        assert studio_state.unpublished_changes_facts(5, None) == {}
        assert studio_state.unpublished_changes_facts(None, True) == {}
        facts = studio_state.topic_save_facts(is_owner=True, release_version_no=4, unpublished_changes=True)
        assert facts["delivery"]["unpublished_changes_note"] == studio_state.unpublished_changes_facts(4, True)[
            "unpublished_changes_note"
        ]

    def test_version_state_uses_same_comparison_for_course_owner(self):
        # 第 10 波（IK-1127）: version-state の事実文は studio_state.version_state_course_facts に集約
        # （同じ比較 course_release_comparison・同じ文 unpublished_changes_facts を通す）。
        assert "version_state_course_facts(" in VERSIONING_SRC
        assert 'perms.get("is_owner")' in VERSIONING_SRC
        import inspect

        src = inspect.getsource(studio_state.version_state_course_facts)
        assert "course_release_comparison(" in src and "unpublished_changes_facts(" in src

    def test_versioning_modal_draws_note_for_owner(self):
        fn = _js_fn(VERSIONING_JS, "_renderModal")
        assert "state.unpublished_changes_note" in fn and "isOwner" in fn

    def test_studio_header_and_reattach_notice(self):
        helper = _js_fn(STUDIO_JS, "lsUnpublishedChangesHtml")
        assert "versions_entry" in helper and "data-ls-open-versions" in helper
        header = _js_fn(STUDIO_JS, "lsRenderCourseStructure")
        assert "lsUnpublishedChangesHtml(s.delivery, true)" in header
        assert "lsBindOpenVersionsButton(unpublishedRow)" in header
        assert "lsUnpublishedChangesHtml(" in _js_fn(STUDIO_JS, "lsTopicDeliveryInnerHtml")
        reattach = _js_fn(STUDIO_JS, "lsBindReattachAllAppendicesButton")
        assert "unpublished_changes_note" in reattach


# ---------------------------------------------------------------------------
# IK-1080
# ---------------------------------------------------------------------------


class TestReattachAllOutcomeGroups:
    def test_groups_split_by_outcome(self):
        notice = studio_state.appendix_reattach_all_notice(
            [("replaced", "B"), ("removed", "A"), ("removed", "C"), ("unchanged", "D"), ("added", "E")]
        )
        labels = studio_state.APPENDIX_REATTACH_ALL_OUTCOME_LABELS
        assert notice.startswith(studio_state.APPENDIX_REATTACH_ALL_LEAD)
        assert f"{labels['removed']}: A / C。" in notice
        assert f"{labels['replaced']}: B。" in notice
        assert f"{labels['added']}: E。" in notice
        assert "D" not in notice
        # 外したもの（受講者の付録が消える）を先に言う。
        assert notice.index(labels["removed"]) < notice.index(labels["replaced"])

    def test_none(self):
        assert studio_state.appendix_reattach_all_notice([]) == studio_state.APPENDIX_REATTACH_ALL_NONE_NOTICE

    def test_route_uses_groups(self):
        assert "appendix_reattach_all_notice(outcome_items)" in TOPICS_SRC
        assert '"outcome_groups"' in TOPICS_SRC


# ---------------------------------------------------------------------------
# IK-1081
# ---------------------------------------------------------------------------

_BODY = (
    "## 推定\n\nDCF 法で磁場を見積もります。根拠の文はここ。\n\n"
    "![[equation:eq_4]]\n\n"
    "> 注: 紐づく主張（claim）6件は本文が根拠候補に含まれていないため、教材には埋め込んでいません。\n\n"
    "## 次のトピックへ\n\n次は主結果です。"
)


class TestWorkNoteResidue:
    def test_note_paragraph_removed_for_learner(self):
        topic = {"student_material": {"source_text": _BODY}}
        text = lecture.topic_student_material(topic)
        assert "紐づく主張" not in text
        assert "![[equation:eq_4]]" in text and "次は主結果です。" in text
        assert "\n\n## 次のトピックへ" in text

    def test_saved_data_not_rewritten(self):
        topic = {"student_material": {"source_text": _BODY}}
        lecture.topic_student_material(topic)
        assert topic["student_material"]["source_text"] == _BODY

    def test_plain_prose_is_kept(self):
        prose = "論文は前提を述べます。ただし根拠候補には含まれていません。"
        assert ccb.split_work_note_paragraphs(prose) == (prose, [])

    def test_learning_route_delegates(self):
        assert "from core.lecture import topic_student_material" in LEARNING_SRC

    def test_cautions_and_state(self):
        topic = {
            "student_material": {"source_text": _BODY},
            "cautions": [
                "式の適用範囲は低温の極限に限ります。",
                "eq_blk_003_0065 と eq_blk_003_0066 は同じ式です。",
                "Claim は本文が根拠候補に載っていないため埋め込みを見送りました。",
            ],
        }
        residue = ccb.topic_work_note_residue(topic)
        assert len(residue["body_notes"]) == 1 and len(residue["caution_notes"]) == 2
        state = studio_state.topic_work_note_state(topic)
        assert state and "受講者には表示していません" in state["message"]
        assert studio_state.topic_work_note_state({"cautions": ["低温でのみ成り立ちます。"]}) is None
        keep, moved = ccb.split_cautions_and_handoff(topic["cautions"])
        assert keep == ["式の適用範囲は低温の極限に限ります。"] and len(moved) == 2

    def test_rewrite_prompt_filters(self):
        assert "_rewrite_prompt_draft(topic, draft)" in TOPICS_SRC
        from routes.lecture_studio.topics import _rewrite_prompt_draft

        draft = {
            "student_material": {"source_text": _BODY},
            "cautions": ["低温でのみ成り立ちます。", "eq_blk_003_0065 は断片です。"],
        }
        out = _rewrite_prompt_draft({}, draft)
        assert "紐づく主張" not in out["student_material"]["source_text"]
        assert out["cautions"] == ["低温でのみ成り立ちます。"]
        assert draft["cautions"][1] == "eq_blk_003_0065 は断片です。"

    def test_studio_shows_state(self):
        assert '"work_note_state"' in TOPICS_SRC and '"work_note_topics"' in TOPICS_SRC
        assert "topic.work_note_state.message" in STUDIO_JS
        assert "s.work_note_course_notice" in STUDIO_JS


# ---------------------------------------------------------------------------
# IK-0872 t11
# ---------------------------------------------------------------------------


class TestParameterValueSelection:
    def _item(self):
        return {"equation_id": "eq_x", "latex": "N = 15", "raw_text": "N = 15"}

    def test_subscripted_symbol_is_not_mention(self):
        body = r"コロケーション点 $r_i$（$i = 1, \dots, N_c$）と $\mathcal{N}_{\mathrm{TOV}}$ を使います。"
        assert ccb._is_unmentioned_parameter_value(self._item(), body) is True

    def test_bare_symbol_is_mention(self):
        assert ccb._is_unmentioned_parameter_value(self._item(), "学習は $N$ 回繰り返します。") is False
        assert ccb._is_unmentioned_parameter_value(self._item(), "回数は N") is False


# ---------------------------------------------------------------------------
# IK-0571
# ---------------------------------------------------------------------------


class TestSymbolDefinitionOriginalMark:
    def test_server_and_screen(self):
        assert 'definition["original_mark"] = ORIGINAL_TEXT_MARK' in SYMBOL_SRC
        fn = _js_fn(APP_JS, "renderSymbolLookupPopover")
        assert "definition.original_mark" in fn
