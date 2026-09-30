"""表示投影層の単体挙動（``core/display_projection.py``）。"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api"), str(BACKEND.parent / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import display_projection as dp  # noqa: E402


class _Inner(BaseModel):
    label: str
    confidence: float = 0.5


class _Outer(BaseModel):
    answer: str
    items: list[_Inner] = []


class TestProject:
    def test_pydantic_input_is_dumped_and_projected(self):
        out = dp.project_for_learner(_Outer(answer="式 eq_tex_b14 を見る", items=[_Inner(label="comp_003")]))
        assert out == {
            "answer": "式 " + dp.UNIDENTIFIED_ELEMENT_TEXT + " を見る",
            "items": [{"label": dp.UNIDENTIFIED_ELEMENT_TEXT}],
        }

    def test_list_input(self):
        out = dp.project_for_learner([{"text": "see ev_0001"}, {"k": 3, "text": "ok"}])
        assert out == [{"text": "see " + dp.UNIDENTIFIED_ELEMENT_TEXT}, {"text": "ok"}]

    def test_input_is_not_mutated(self):
        payload = {"label": "claim_span_1", "confidence": 0.9, "nested": [{"score": 1, "text": "sym_0"}]}
        before = copy.deepcopy(payload)
        dp.project_for_learner(payload)
        assert payload == before

    def test_label_of_known_element_type_uses_generic_label(self):
        out = dp.project_for_learner({"element_type": "equation", "label": "eq_tex_b14", "id": "eq_tex_b14"})
        assert out == {"element_type": "equation", "label": "関連する数式", "id": "eq_tex_b14"}

    def test_paper_equation_number_label_is_kept(self):
        out = dp.project_for_learner({"element_type": "equation", "label": "eq_2_7"})
        assert out["label"] == "eq_2_7"

    def test_bare_token_under_non_display_key_is_an_address(self):
        out = dp.project_for_learner({"chain": ["step_001"], "footprints": ["node_3"]})
        assert out == {"chain": ["step_001"], "footprints": ["node_3"]}

    def test_generated_english_label_is_replaced_for_learner_only(self):
        payload = {
            "element_type": "derivation",
            "label": "Logical progression through 41 claims in section 3",
            "title": "Logical progression through 41 claims in section 3",
        }
        learner = dp.project_for_learner(payload)
        assert learner["label"] == "導出の流れ"
        assert learner["title"] == payload["title"]  # 本文キーは置き換えない
        assert dp.project_for_teacher(payload)["label"] == payload["label"]

    def test_paper_title_label_is_not_replaced(self):
        title = "Dark Matter Halo Profiles of Dwarf Galaxies"
        assert dp.project_for_learner({"label": title})["label"] == title

    def test_teacher_audience_keeps_staged_labels(self):
        payload = {"confidence": 0.7, "confidence_label": "高", "limit": 5, "count": 3, "n": 4}
        assert dp.project_for_teacher(payload) == {"confidence_label": "高", "n": 4}
        assert dp.project_for_learner(payload) == {"confidence_label": "高", "limit": 5}

    def test_privacy_range_strings_survive(self):
        payload = {"range_label": "3-5", "count_range": "6-10"}
        assert dp.project_for_learner(payload) == payload

    def test_unknown_audience_is_rejected(self):
        with pytest.raises(ValueError):
            dp.project({}, audience="student")  # type: ignore[arg-type]


class TestRoute:
    def test_wrapper_projects_and_passes_responses_through(self):
        from starlette.responses import Response

        from api.display_route import project_learner_payload, wrap_learner_endpoint

        assert project_learner_payload({"text": "comp_003 です"}) == {"text": dp.UNIDENTIFIED_ELEMENT_TEXT + " です"}
        response = Response("x")
        assert project_learner_payload(response) is response
        assert project_learner_payload("comp_003") == "comp_003"

        def endpoint(course_id: str) -> dict:
            return {"course_id": course_id, "score": 1}

        wrapped = wrap_learner_endpoint(endpoint)
        assert wrapped("c1") == {"course_id": "c1"}
        assert wrapped.__wrapped__ is endpoint
        assert wrap_learner_endpoint(wrapped) is wrapped

    def test_async_endpoint_stays_async(self):
        import asyncio
        import inspect

        from api.display_route import wrap_learner_endpoint

        async def endpoint() -> dict:
            return {"confidence": 1, "text": "ok"}

        wrapped = wrap_learner_endpoint(endpoint)
        assert inspect.iscoroutinefunction(wrapped)
        assert asyncio.run(wrapped()) == {"text": "ok"}

    def test_projection_failure_does_not_stop_the_response(self, monkeypatch):
        from api import display_route

        def boom(_value):
            raise RuntimeError("x")

        monkeypatch.setattr(display_route, "project_for_learner", boom)
        payload = {"text": "a"}
        assert display_route.project_learner_payload(payload) is payload


class TestAddressesSurvive:
    """番地（埋め込み参照・URL）は射影しても描画できる形で残る（レビュー C1 / C2）。"""

    def test_embed_markers_in_topic_material_survive(self):
        text = (
            "要点は ![[component:comp_001]] にある。主張 ![[claim:0f8fad5b-d9cb-469f-a165-70867728950e]]、"
            "式 ![[equation:eq_blk_004_0084]]、入れ子 ![[equation: [[eq_tex_b14]] ]]、"
            "図 ![[figure:fig_3]]、出典 ![[source:ev_0001]]、[[FORMULA_3]]。"
            "ただし裸の eq_blk_004_0084 は隠す。"
        )
        payload = {
            "topic_id": "t1",
            "student_material": text,
            "chunks": [{"chunk_id": "c1", "text": text}],
            "slides": [{"display_text": text, "spoken_text": "読み上げ"}],
            "evidence_items": [{"id": "comp_001", "element_type": "theory_component", "label": "comp_001"}],
        }
        out = dp.project_for_learner(payload)
        for projected in (out["student_material"], out["chunks"][0]["text"], out["slides"][0]["display_text"]):
            for marker in (
                "![[component:comp_001]]",
                "![[claim:0f8fad5b-d9cb-469f-a165-70867728950e]]",
                "![[equation:eq_blk_004_0084]]",
                "![[equation: [[eq_tex_b14]] ]]",
                "![[figure:fig_3]]",
                "![[source:ev_0001]]",
                "[[FORMULA_3]]",
            ):
                assert marker in projected, (marker, projected)
            assert "裸の " + dp.UNIDENTIFIED_ELEMENT_TEXT + " は隠す" in projected
        assert out["evidence_items"][0]["id"] == "comp_001"

    def test_mask_internal_ids_skips_markers_only(self):
        assert dp.mask_internal_ids("![[claim:claim_0004]] claim_0004") == (
            "![[claim:claim_0004]] " + dp.UNIDENTIFIED_ELEMENT_TEXT
        )

    def test_url_suffixed_keys_are_addresses(self):
        url = "/api/learning/courses/0f8fad5b-d9cb-469f-a165-70867728950e/figures/1b4e28ba-2fa1-11d2-883f-0016d3cca427/image"
        payload = {"figures": [{"image_url": url, "thumb_src": "x/comp_003.png", "audio_path": "a/ev_0001.mp3", "href": "h/comp_003"}]}
        assert dp.project_for_learner(payload) == payload

    def test_api_and_http_values_are_addresses_under_any_key(self):
        payload = {
            "link": "/api/learning/courses/0f8fad5b-d9cb-469f-a165-70867728950e/figures/comp_003/image",
            "text": "https://example.org/papers/0f8fad5b-d9cb-469f-a165-70867728950e",
        }
        assert dp.project_for_learner(payload) == payload


class TestOptionLabelsStayDistinct:
    """選択肢のラベルを一般ラベルへ畳まない（レビュー M1）。"""

    LABELS = (
        "Define the potential: V(r) for 3 steps",
        "Derive the rotation curve: v(r) from 2 equations",
        "Logical progression through 41 claims in section 3",
    )

    @pytest.mark.parametrize("list_key", ["response_space", "next_actions", "sources", "options"])
    def test_distinct_english_option_labels_stay_distinct(self, list_key):
        payload = {list_key: [{"label": label, "element_type": "derivation"} for label in self.LABELS]}
        out = dp.project_for_learner(payload)
        labels = [item["label"] for item in out[list_key]]
        assert labels == list(self.LABELS)
        assert len(set(labels)) == 3

    def test_labels_without_known_element_type_are_not_collapsed(self):
        payload = {"items": [{"label": label} for label in self.LABELS]}
        out = dp.project_for_learner(payload)
        assert [item["label"] for item in out["items"]] == list(self.LABELS)

    def test_known_element_type_outside_options_is_still_replaced(self):
        out = dp.project_for_learner({"element_type": "derivation", "label": self.LABELS[2]})
        assert out["label"] == "導出の流れ"


class TestExtraVocabulary:
    """m9: 図・節・TeX ブロックの生成 ID。"""

    @pytest.mark.parametrize("token", ["figure_3", "section_3", "tex_b14", "blk_004"])
    def test_generated_forms_are_caught(self, token):
        assert dp.is_internal_id_token(token)
        assert token not in dp.mask_internal_ids(f"見出し {token} の説明")

    @pytest.mark.parametrize("text", ["Figure 3", "Section 3", "latex_b14", "eq_2_7", "eq_12", "figure", "sections"])
    def test_legitimate_display_text_is_not_caught(self, text):
        assert not dp.contains_internal_id(text)
