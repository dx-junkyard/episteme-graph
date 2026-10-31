"""第 25 周 面 D 第 8 波（IK-1070 / IK-1071 / IK-0976 の残り / IK-0571 の区画そろえ）の再現テスト。

- IK-1070: 版ありで「いまの内容は版と同じ」とき、保存の知らせは「新しい版を発行してください」を言わない
  （指示文と事実文を同じ ``unpublished_changes`` から作る）。
- IK-1071: 共有教員の音声の依頼の出口は押せない「→ 所有者に依頼する」ではなく、頼む相手の名前入りの文と
  ［依頼の文をコピー］（宛先・中身を運ぶ。通知は送らない・DB 非変更）。
- IK-0976 の残り: 文中の小さな式（``is_display`` が偽）は実文字数で数え、表示式だけ 60 字換算。⚓ の埋め込み
  だけの段落は直前の段落と結ぶ。``===`` の明示分割の優先は不変。
- IK-0571: 部品の文脈の前提・入力・出力・注意・関連にも「（原文）」の印（主張と揃える）。
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
from core import lecture  # noqa: E402
from core import lecture_studio_state as studio_state  # noqa: E402
from core.help_kb import admin_ui_anchors  # noqa: E402
from core.label_vocab import ORIGINAL_TEXT_MARK  # noqa: E402

FRONTEND = ROOT / "frontend" / "public"
STUDIO_JS = (FRONTEND / "js" / "admin-lecture-studio.js").read_text(encoding="utf-8")
APP_JS = (FRONTEND / "js" / "app.js").read_text(encoding="utf-8")
MANUAL = (ROOT / "docs" / "manual" / "teacher" / "14-admin-lecture-studio.md").read_text(encoding="utf-8")


def _js_fn(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    nxt = re.search(r"\n  function [A-Za-z0-9_]+\(", src[start + 10:])
    return src[start: start + 10 + nxt.start()] if nxt else src[start:]


# ---------------------------------------------------------------------------
# IK-1070
# ---------------------------------------------------------------------------


class TestDeliveryImperativeFollowsComparison:
    def test_same_as_release_has_no_imperative(self):
        facts = studio_state.topic_save_facts(is_owner=True, release_version_no=2, unpublished_changes=False)
        delivery = facts["delivery"]
        assert "発行してください" not in delivery["message"]
        assert "版 2" in delivery["message"]
        assert delivery["unpublished_changes_note"] == "いまの内容は版 2 と同じです。発行は要りません。"
        joined = delivery["message"] + delivery["unpublished_changes_note"]
        assert not ("発行してください" in joined and "発行は要りません" in joined)

    def test_changed_keeps_imperative(self):
        facts = studio_state.topic_save_facts(is_owner=True, release_version_no=2, unpublished_changes=True)
        assert "新しい版を発行してください" in facts["delivery"]["message"]

    def test_unknown_keeps_previous_sentence(self):
        facts = studio_state.topic_save_facts(is_owner=True, release_version_no=3)
        assert "新しい版を発行してください" in facts["delivery"]["message"]


# ---------------------------------------------------------------------------
# IK-1071
# ---------------------------------------------------------------------------


class TestAudioRequestExit:
    def test_editor_gets_named_recipient_and_parts(self):
        facts = studio_state.topic_save_facts(is_owner=False, owner_display_name="水野 早紀")
        assert "所有者（水野 早紀）に依頼してください" in facts["audio_generate_notice"]
        req = facts["audio_request"]
        assert req["label"] == "依頼の文をコピー"
        assert "水野 早紀" in req["copy_notice"]
        assert set(req["section_labels"]) == {"course", "request", "language", "audio_state", "topics"}
        assert req["language_labels"]["ja"] == "日本語"

    def test_without_name_says_owner(self):
        facts = studio_state.topic_save_facts(is_owner=False)
        assert facts["audio_generate_notice"] == studio_state.TOPIC_AUDIO_GENERATE_OWNER_ONLY_NOTICE
        assert facts["audio_request"]["recipient"] == "所有者"

    def test_owner_has_no_request(self):
        assert "audio_request" not in studio_state.topic_save_facts(is_owner=True)

    def test_studio_has_copy_button_not_dead_label(self):
        guidance = _js_fn(STUDIO_JS, "lsAudioReadinessGuidance")
        assert '"所有者に依頼する"' not in guidance
        assert "audio_request" in guidance
        note = _js_fn(STUDIO_JS, "lsRenderAudioReadinessNote")
        assert "lecture-studio.audio-request-copy" in note
        assert "lsCopyAudioRequest" in note
        copy = _js_fn(STUDIO_JS, "lsCopyAudioRequest")
        assert "navigator.clipboard" in copy and "copy_notice" in copy
        assert "apiFetch" not in copy  # 通知も保存もしない

    def test_anchor_three_point_set(self):
        assert "lecture-studio.audio-request-copy" in admin_ui_anchors.KNOWN_ADMIN_UI_ANCHOR_IDS
        assert admin_ui_anchors.ADMIN_UI_ANCHORS["lecture-studio.audio-request-copy"].endswith("#audio-request-copy")
        assert "{#audio-request-copy}" in MANUAL


# ---------------------------------------------------------------------------
# IK-0976 の残り
# ---------------------------------------------------------------------------


_INLINE = [{"id": f"[[FORMULA_{i}]]", "latex": "r", "is_display": False} for i in range(1, 5)]
_DISPLAY = [{"id": "[[FORMULA_0]]", "latex": "N_{TOV}=\\sum_i r_i^2", "is_display": True}]


class TestInlineFormulaLength:
    def test_map_only_inline(self):
        m = lecture.formula_length_map(_DISPLAY + _INLINE)
        assert "[[FORMULA_0]]" not in m and m["[[FORMULA_1]]"] == 1

    def test_display_length_counts_inline_by_text(self):
        text = "a [[FORMULA_1]] b [[FORMULA_0]]"
        m = lecture.formula_length_map(_DISPLAY + _INLINE)
        assert lecture._display_length(text, m) == len("a  b ") + 1 + 60
        assert lecture._display_length(text) == len("a  b ") + 120

    def test_explanation_with_inline_formulas_joins_formula(self):
        expl = (
            "ここで [[FORMULA_1]] は半径、[[FORMULA_2]] は質量の勾配で、" + "あ" * 60 + "。"
            "[[FORMULA_3]] と [[FORMULA_4]] を微分できるので勾配で学べます。"
        )
        units = ["損失は次です。", "[[FORMULA_0]]", expl, "次の段落。"]
        m = lecture.formula_length_map(_DISPLAY + _INLINE)
        assert lecture._display_length(expl) > 240  # 旧い換算では「短い解説」にならなかった
        assert lecture._reading_blocks(units, m) == [(0, 3), (3, 4)]

    def test_auto_paginate_keeps_explanation_with_formula(self):
        expl = (
            "ここで [[FORMULA_1]] は半径、[[FORMULA_2]] は質量の勾配で、" + "あ" * 60 + "。"
            "[[FORMULA_3]] と [[FORMULA_4]] を微分できるので勾配で学べます。"
        )
        display = "\n\n".join(["い" * 380, "損失は次です。", "[[FORMULA_0]]", expl, "う" * 300])
        slides, _ = lecture.auto_paginate_slides(display, "", _DISPLAY + _INLINE)
        for slide in slides:
            if "[[FORMULA_0]]" in slide["display_text"]:
                assert "微分できる" in slide["display_text"]
                break
        else:
            raise AssertionError("式の枚が無い")

    def test_anchor_embed_only_joins_previous(self):
        units = ["TOV 残差で制約します。", "![[component:comp_1]]\n![[claim:c_2]]", "## 次の節"]
        assert lecture._reading_blocks(units) == [(0, 2), (2, 3)]
        assert lecture._is_anchor_embed_only_unit("![[component:x]]")
        assert not lecture._is_anchor_embed_only_unit("説明 ![[component:x]]")

    def test_explicit_markers_still_win(self):
        slides, _ = lecture.auto_paginate_slides("前\n===\n![[component:x]]", "", [])
        assert len(slides) == 2


# ---------------------------------------------------------------------------
# IK-0571
# ---------------------------------------------------------------------------


class TestOriginalMarkAcrossSections:
    def test_field_refs_cautions_dependencies(self):
        record = {
            "preconditions": [{"text": "Profile outputs are differentiable with respect to the radius."}],
            "cautions": ["Constraint is soft and only approximately enforced."],
            "dependencies": [{"dependency_type": "requires", "reason": "Needs the TOV equations to hold.",
                              "component_refs": ["c1"]}],
        }
        pre = component_context._build_field_refs(record, "preconditions")
        assert pre[0]["original_mark"] == ORIGINAL_TEXT_MARK
        cau = component_context._build_cautions(record)
        assert cau[0]["original_mark"] == ORIGINAL_TEXT_MARK
        dep = component_context._build_dependencies(record, {"c1": {"label": "TOV 方程式"}})
        assert dep[0]["original_mark"] == ORIGINAL_TEXT_MARK

    def test_japanese_has_no_mark(self):
        record = {"cautions": ["制約は弱く、近似的にしか満たされません。"]}
        assert "original_mark" not in component_context._build_cautions(record)[0]

    def test_app_draws_marks(self):
        assert "original_mark" in _js_fn(APP_JS, "renderSupportsTextSection")
        assert "original_mark" in _js_fn(APP_JS, "renderContextDependencySection")
