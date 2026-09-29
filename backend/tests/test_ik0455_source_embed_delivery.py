"""教材本文の埋め込み記法を学習者・モデルに生で渡さない（IK-0455）。

サンドボックスのコース（20 トピック）で見つかったこと:

1. 生成の閉世界（``available_references``）は ``![[source:topic_summary]]`` を「原文抜粋」
   として渡していたが、配信（``build_topic_evidence_items``）と原稿スタジオは同じ id を
   「トピック概要」（``topic.summary``）に解決していた。原文抜粋は配信と同じ id で渡す。
2. 閉世界の外の ``![[component|claim|source:id]]`` は学習画面で「未解決」カードに内部 ID を
   出す。生成直後と配信時に、学習画面と同じ引き方で引けないものを外す。
3. 教材が 2 区画以上に割れると、本文は ``build_topic_slides`` が振った ``[[FORMULA_N]]`` なのに
   formulas は content_blocks の式を渡していた（``$w$`` が別の式に引かれる / 「この数式は教材に
   載せられていません」に潰れる）。区画の本文と formulas を同じ関数から受け取る。
4. latex の無い inline 式候補の埋め込みを、英語の意味要約（「Equation semantics could not be
   inferred.」）を数式として描いていた。短い原文を本文に書く。
5. 学習チャット・確認問題のプロンプトに載せる「現在表示中の教材」から埋め込み記法を外す。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import core.course_content_builder as ccb
import core.lecture as lecture
from core import label_vocab

BACKEND = Path(__file__).resolve().parents[1]


def _topic():
    return {
        "summary": "States the inferred radius.",
        "source_excerpt": "We present the first application of PINNs.",
        "linked_chunk_ids": ["chunk-uuid-1"],
        "evidence_links": [
            {"kind": "claim", "target_id": "claim_span_001", "summary": "PINNs solve the EOS."},
            {"kind": "source", "target_id": "ev_0077", "summary": "Softening is reproduced."},
            {"kind": "equation", "target_id": "eq_eqcand_inline_blk_1_2_ab", "latex": "",
             "summary": "Equation semantics could not be inferred."},
        ],
        "content_blocks": [{"type": "equations", "items": [
            {"equation_id": "eq_eqcand_inline_blk_1_2_ab", "latex": None, "raw_text": "z < 2"},
            {"equation_id": "eq_6", "latex": r"c_s^2 = \frac{dP}{d\epsilon}"},
        ]}],
    }


class TestAvailableReferencesMatchDelivery:
    def test_excerpt_is_offered_with_the_delivery_id(self):
        refs = ccb._topic_available_references(_topic(), set())
        keys = {(r["kind"], r["id"]) for r in refs}
        assert ("source", "chunk-uuid-1") in keys
        assert ("source", "topic_summary") not in keys
        delivered = {(i["kind"], i["id"]) for i in ccb.build_topic_evidence_items(_topic())}
        assert ("source", ccb.source_excerpt_evidence_id(_topic())) in delivered

    def test_excerpt_id_without_chunks(self):
        assert ccb.source_excerpt_evidence_id({"source_excerpt": "x"}) == "excerpt"

    def test_prompt_no_longer_offers_topic_summary(self):
        src = inspect.getsource(ccb)
        assert "`![[source:topic_summary]]` のみを使う" not in src
        assert "`topic_summary` / `summary` のような一覧に無い id を書かない" in src

    def test_legacy_topic_summary_still_resolves_on_delivery(self):
        # 保存済みコースの ``![[source:topic_summary]]`` は従来どおりトピック概要に解決する
        # （原稿スタジオと同じ。落とさない）。
        items = {(i["kind"], i["id"]) for i in ccb.build_topic_evidence_items(_topic())}
        assert ("source", "topic_summary") in items


class TestDropUnresolvedEvidenceEmbeds:
    def test_drops_only_unresolvable(self):
        items = ccb.build_topic_evidence_items(_topic())
        text = (
            "本文。\n\n![[claim:claim_span_001]]\n\n![[source:ev_9999]]\n\n"
            "![[component:comp_missing]] と ![[source:ev_0077]]\n\n![[equation:eq_6]] ![[figure:abc]]"
        )
        out, dropped = ccb.drop_unresolved_evidence_embeds(text, items)
        assert dropped
        assert "![[claim:claim_span_001]]" in out
        assert "![[source:ev_0077]]" in out
        assert "ev_9999" not in out and "comp_missing" not in out
        # 数式・図は別経路の責務なので触れない。
        assert "![[equation:eq_6]]" in out and "![[figure:abc]]" in out
        assert "\n\n\n" not in out

    def test_kind_fallback_matches_app_js(self):
        # 学習画面と同じく、同じ ID の別 kind には引ける。
        items = [{"kind": "component", "id": "comp_1"}]
        out, dropped = ccb.drop_unresolved_evidence_embeds("![[claim:comp_1]]", items)
        assert not dropped and out == "![[claim:comp_1]]"

    def test_no_embeds_is_noop(self):
        assert ccb.drop_unresolved_evidence_embeds("本文だけ", []) == ("本文だけ", False)

    def test_sanitize_topic_leaves_fact_note(self):
        topic = _topic()
        topic["student_material"] = {"source_text": "本文 ![[source:ev_9999]] 続き ![[claim:claim_span_001]]"}
        assert ccb._sanitize_topic_evidence_embeds(topic)
        assert "ev_9999" not in topic["student_material"]["source_text"]
        assert "claim_span_001" in topic["student_material"]["source_text"]
        assert topic["grounding_note"] == label_vocab.UNRESOLVED_EMBED_GROUNDING_NOTE
        assert label_vocab.UNRESOLVED_EMBED_GROUNDING_NOTE in ccb.GENERATED_DRAFT_NOTES

    def test_sanitize_is_wired_after_generation(self):
        src = inspect.getsource(ccb)
        idx = src.index("_sanitize_topic_formula_placeholders(topic)\n")
        assert "_sanitize_topic_evidence_embeds(topic)" in src[idx: idx + 400]


class TestLectureEquationEmbedsUseShortRawText:
    def test_inline_candidate_uses_raw_text_not_summary(self):
        topic = _topic()
        text, formulas = lecture._resolve_equation_embeds(
            "宇宙のスケール因子 ![[equation:eq_eqcand_inline_blk_1_2_ab]] とともに",
            topic["evidence_links"], [], lecture._topic_block_formula_items(topic),
        )
        assert "z < 2" in text
        assert "eqcand" not in text
        assert not any("Equation semantics" in f.get("latex", "") for f in formulas)

    def test_block_latex_is_used_when_link_missing(self):
        topic = _topic()
        text, formulas = lecture._resolve_equation_embeds(
            "![[equation:eq_6]]", [], [], lecture._topic_block_formula_items(topic),
        )
        assert "[[FORMULA_0]]" in text
        assert formulas[0]["latex"].startswith("c_s^2")


class TestDeliverySegmentsShareFormulas:
    def _long_topic(self):
        para = "説明の段落です。" * 40
        text = (
            f"## 一\n\n{para} 状態方程式 $w$ とスケール因子 $a$。\n\n"
            f"## 二\n\n{para} 音速 ![[equation:eq_6]] と ![[source:ev_9999]]。\n\n"
            f"## 三\n\n{para} 候補 ![[equation:eq_eqcand_inline_blk_1_2_ab]]。"
        )
        topic = _topic()
        topic["student_material"] = {"source_text": text}
        return topic, text

    def test_segmented_formulas_match_placeholders(self):
        topic, text = self._long_topic()
        items = ccb.build_topic_evidence_items(topic)
        segments, formulas = lecture.topic_material_delivery_segments(topic, {}, text, [], items)
        assert len(segments) >= 2
        joined = "\n".join(segments)
        assert label_vocab.UNRESOLVED_FORMULA_PLACEHOLDER_TEXT not in joined
        latex = {f["id"]: f["latex"] for f in formulas}
        ids = re.findall(r"\[\[FORMULA_\d+\]\]", joined)
        assert ids and all(i in latex for i in ids)
        assert "w" in latex.values() and "a" in latex.values()
        assert "ev_9999" not in joined and "eqcand" not in joined
        assert "z < 2" in joined

    def test_short_material_keeps_whole_text_and_formulas(self):
        topic = _topic()
        whole = [{"id": "eq_6", "latex": "x"}]
        segments, formulas = lecture.topic_material_delivery_segments(
            topic, {}, "短い ![[source:ev_9999]] ![[equation:eq_6]]", whole,
            ccb.build_topic_evidence_items(topic),
        )
        assert len(segments) == 1 and segments[0].startswith("短い")
        assert "![[equation:eq_6]]" in segments[0]
        assert "ev_9999" not in segments[0]
        assert formulas == whole

    def test_get_topic_material_delegates(self):
        src = (BACKEND / "api/routes/learning.py").read_text(encoding="utf-8")
        body = src[src.index("def get_topic_material("):src.index("def _topic_material_fallback_notice(")]
        assert "topic_material_delivery_segments(" in body
        assert "_topic_material_segment_texts(" not in body


class TestMaterialTextForPrompt:
    def test_embeds_are_resolved_or_removed(self):
        topic = _topic()
        text = (
            "音速は ![[equation:eq_6]]。候補 ![[equation:eq_eqcand_inline_blk_1_2_ab]]。\n\n"
            "![[source:topic_summary]]\n\n![[claim:claim_span_001]] 図 ![[figure:f-1]] [[FORMULA_3]]"
        )
        out = ccb.material_text_for_prompt(topic, text)
        assert "![[" not in out and "[[FORMULA" not in out
        assert r"$c_s^2 = \frac{dP}{d\epsilon}$" in out
        assert "z < 2" in out
        assert "eqcand" not in out and "claim_span" not in out
        assert "（図）" in out

    def test_chat_and_check_prompts_use_it(self):
        src = (BACKEND / "api/routes/learning.py").read_text(encoding="utf-8")
        assert "topic_material = material_text_for_prompt(topic_info, _topic_student_material(topic_info))" in src
        assert "material_text = material_text_for_prompt(topic, _topic_student_material(topic))" in src

    def test_empty(self):
        assert ccb.material_text_for_prompt({}, "") == ""
