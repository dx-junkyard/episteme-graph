"""freeze（``build_course_content``）が「学ぶ単位」で成果を束ねることのテスト。

正本: ``docs/features/learning_units_design.md`` §5.1 / §6.3 / §6.5。ここで固定するのは:

1. topic に ``units`` があれば **units 優先**で component / 式 / claim を束ね、
   ``content_source="learning_units"`` / ``content_confidence="unit_selection"``。
2. ``units`` が空のときだけ従来の文字列一致（救済）。救済で当たった component が
   unit の子なら ``source="title_match"`` で後付けし、教員が選んだ
   ``teacher_selected`` と**区別**する。
3. unit 経由の component item は親 unit の label を ``display_label`` として持つ
   （内部名は ``label`` に残る = 情報を落とさない）。
4. blueprint の語りの弧が ``topic.narrative`` に落ち、散文生成のプロンプト文脈にだけ渡る。
5. 学習者向け DTO 射影に ``stable_key`` / ``unit_id`` が出ない（KO10）。
"""

from __future__ import annotations

import core.course_content_builder as ccb
from core.course_data import (
    UNIT_SOURCE_TEACHER_SELECTED,
    UNIT_SOURCE_TITLE_MATCH,
    learner_topic_units_projection,
    topic_unit_keys,
    topic_units,
)


DOC = "doc-1"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------

def _component(component_id: str, label: str, *, equations=None, claims=None) -> dict:
    return {
        "component_id": component_id,
        "document_id": DOC,
        "label": label,
        "summary": f"{label} の要約",
        "teaching_takeaway": "",
        "linked_equation_ids": list(equations or []),
        "linked_claim_ids": list(claims or []),
        "linked_evidence_ids": [],
    }


def _equation(equation_id: str) -> dict:
    return {
        "equation_id": equation_id,
        "document_id": DOC,
        "latex": r"E = mc^2",
        "plain_text": "E は m と c で決まる",
    }


def _bundle(*, components=None, mapping_topics=None, equations=None, claims=None, narrative=None) -> dict:
    return {
        "mapping_topics": list(mapping_topics or []),
        "components": dict(components or {}),
        "equations": dict(equations or {}),
        "claims": dict(claims or {}),
        "evidence": {},
        "figure_claim_links": {},
        "narrative_by_component": dict(narrative or {}),
    }


def _unit_row(
    stable_key: str,
    label: str,
    *,
    unit_kind: str = "parent_component",
    component_agent_ids=None,
    equation_ids=None,
    claim_agent_ids=None,
) -> dict:
    payload: dict = {}
    if component_agent_ids is not None:
        payload["linked_component_agent_ids"] = list(component_agent_ids)
    if claim_agent_ids is not None:
        payload["linked_claim_agent_ids"] = list(claim_agent_ids)
    return {
        "unit_id": f"uuid-{stable_key}",
        "document_id": DOC,
        "stable_key": stable_key,
        "unit_kind": unit_kind,
        "label": label,
        "summary": "",
        "linked_claim_ids": [],
        "linked_equation_ids": list(equation_ids or []),
        "linked_component_ids": ["db-uuid-ignored"],
        "linked_figure_ids": [],
        "agent_payload": payload,
        "order_index": 0,
    }


def _topic_with_units(*keys, title: str = "トピック") -> dict:
    return {
        "id": "t1",
        "title": title,
        "units": [
            {
                "kind": "parent_component",
                "stable_key": key,
                "unit_id": f"uuid-{key}",
                "label": f"unit {key}",
                "source": UNIT_SOURCE_TEACHER_SELECTED,
            }
            for key in keys
        ],
    }


# ---------------------------------------------------------------------------
# 1. units 優先の束ね
# ---------------------------------------------------------------------------

def test_units_bind_components_through_agent_payload():
    """束ねは ``agent_payload.linked_component_agent_ids`` 経由（DB UUID は使わない）。"""
    bundle = _bundle(components={
        "comp_001": _component("comp_001", "Transform representation: 観測量"),
        "comp_002": _component("comp_002", "Transform representation: 補正"),
        "comp_999": _component("comp_999", "無関係"),
    })
    units = {"k1:parent": _unit_row("k1:parent", "観測量の構成", component_agent_ids=["comp_001", "comp_002"])}

    topic = ccb._enrich_topics([_topic_with_units("k1:parent")], bundle, {}, None, units)[0]

    assert topic["linked_component_ids"] == ["comp_001", "comp_002"]
    assert topic["content_source"] == ccb.UNIT_SELECTION_CONTENT_SOURCE == "learning_units"
    assert topic["content_confidence"] == ccb.UNIT_SELECTION_CONFIDENCE == "unit_selection"
    assert topic.get("grounding_note") is None


def test_units_win_over_title_similarity():
    """タイトル一致で別の component が当たる状況でも units が勝つ。"""
    bundle = _bundle(
        components={
            "comp_unit": _component("comp_unit", "単位の子"),
            "comp_title": _component("comp_title", "共振条件の導出"),
        },
        mapping_topics=[{
            "title": "共振条件の導出",
            "description": "タイトル一致のマッピング",
            "linked_component_ids": ["comp_title"],
        }],
    )
    units = {"k1:u": _unit_row("k1:u", "教員が選んだ単位", component_agent_ids=["comp_unit"])}

    topic = ccb._enrich_topics(
        [_topic_with_units("k1:u", title="共振条件の導出")], bundle, {}, None, units
    )[0]

    assert topic["linked_component_ids"] == ["comp_unit"]
    assert topic["content_confidence"] == "unit_selection"


def test_units_add_equations_the_components_do_not_reach():
    """unit が直接指す式も束ねる（component 経由で拾えない式を落とさない）。"""
    bundle = _bundle(
        components={"comp_001": _component("comp_001", "子", equations=["eq_1"])},
        equations={"eq_1": _equation("eq_1"), "eq_2": _equation("eq_2"), "eq_x": _equation("eq_x")},
    )
    units = {"k1:u": _unit_row(
        "k1:u", "単位", component_agent_ids=["comp_001"], equation_ids=["eq_2", "eq_missing"]
    )}

    topic = ccb._enrich_topics([_topic_with_units("k1:u")], bundle, {}, None, units)[0]

    assert topic["linked_equation_ids"] == ["eq_1", "eq_2"]
    # artifact に無い式 ID は束ねない（推測しない）。
    assert "eq_missing" not in topic["linked_equation_ids"]


def test_unit_claims_only_join_when_they_exist_in_the_artifact_namespace():
    """DB UUID の claim を agent 名前空間へ流さない（``![[claim:id]]`` が壊れない）。"""
    bundle = _bundle(
        components={"comp_001": _component("comp_001", "子", claims=["claim_a"])},
        claims={"claim_a": {"claim_id": "claim_a"}, "claim_b": {"claim_id": "claim_b"}},
    )
    row = _unit_row("k1:u", "単位", component_agent_ids=["comp_001"], claim_agent_ids=["claim_b"])
    row["linked_claim_ids"] = ["550e8400-e29b-41d4-a716-446655440000"]

    topic = ccb._enrich_topics([_topic_with_units("k1:u")], bundle, {}, None, {"k1:u": row})[0]

    assert topic["linked_claim_ids"] == ["claim_a", "claim_b"]
    assert "550e8400-e29b-41d4-a716-446655440000" not in topic["linked_claim_ids"]


def test_units_that_do_not_resolve_fall_back_to_the_rescue_path():
    """supersede 等で live に居ない unit しか無ければ従来の文字列一致へ落ちる。"""
    bundle = _bundle(
        components={"comp_title": _component("comp_title", "共振条件の導出")},
        mapping_topics=[{
            "title": "共振条件の導出", "description": "d", "linked_component_ids": ["comp_title"],
        }],
    )
    topic = ccb._enrich_topics(
        [_topic_with_units("k1:gone", title="共振条件の導出")], bundle, {}, None, {}
    )[0]

    assert topic["linked_component_ids"] == ["comp_title"]
    assert topic["content_source"] == "agent_mapping"
    assert topic["content_confidence"] == "exact_title"


# ---------------------------------------------------------------------------
# 2. 救済（title_match）の区別
# ---------------------------------------------------------------------------

def test_rescue_backfills_units_with_title_match_source():
    """units を選んでいないトピックでも、当たった component の親 unit を後付けする。"""
    bundle = _bundle(
        components={"comp_title": _component("comp_title", "共振条件の導出")},
        mapping_topics=[{
            "title": "共振条件の導出", "description": "d", "linked_component_ids": ["comp_title"],
        }],
    )
    units = {"k1:parent": _unit_row("k1:parent", "共振の理論", component_agent_ids=["comp_title"])}

    topic = ccb._enrich_topics([{"id": "t1", "title": "共振条件の導出"}], bundle, {}, None, units)[0]

    assert topic_unit_keys(topic) == ["k1:parent"]
    assert topic_units(topic)[0]["source"] == UNIT_SOURCE_TITLE_MATCH
    # 救済経路は units 経由ではないので content_source は従来のまま。
    assert topic["content_source"] == "agent_mapping"


def test_rescue_does_not_invent_units_when_no_parent_exists():
    bundle = _bundle(
        components={"comp_title": _component("comp_title", "共振条件の導出")},
        mapping_topics=[{
            "title": "共振条件の導出", "description": "d", "linked_component_ids": ["comp_title"],
        }],
    )
    topic = ccb._enrich_topics([{"id": "t1", "title": "共振条件の導出"}], bundle, {}, None, {})[0]
    assert topic.get("units", []) == []


def test_unlinked_topic_is_unchanged_by_the_units_layer():
    """units も mapping も無いトピックは従来どおり事実文だけ（LU1）。"""
    topic = ccb._enrich_topics([{"id": "t1", "title": "対応の無い項目"}], _bundle(), {}, None, {})[0]
    assert topic["content_source"] == "unlinked"
    assert topic["grounding_note"] == ccb.UNLINKED_TOPIC_GROUNDING_NOTE
    assert topic.get("units", []) == []


# ---------------------------------------------------------------------------
# 3. display_label（§5.1）
# ---------------------------------------------------------------------------

def test_unit_bound_components_carry_the_parent_unit_label():
    bundle = _bundle(components={
        "comp_001": _component("comp_001", "Transform representation: 観測量"),
    })
    units = {"k1:u": _unit_row("k1:u", "観測量の構成", component_agent_ids=["comp_001"])}

    topic = ccb._enrich_topics([_topic_with_units("k1:u")], bundle, {}, None, units)[0]

    block = next(b for b in topic["content_blocks"] if b["type"] == "components")
    item = block["items"][0]
    assert item["display_label"] == "観測量の構成"
    # 内部名は落とさない（情報を落とさない）。
    assert item["label"] == "Transform representation: 観測量"

    # 学習画面向け DTO にも伝わる。
    evidence_items = ccb.build_topic_evidence_items(topic)
    component_item = next(i for i in evidence_items if i["kind"] == "component")
    assert component_item["display_label"] == "観測量の構成"


def test_components_without_units_have_no_display_label():
    bundle = _bundle(
        components={"comp_title": _component("comp_title", "共振条件の導出")},
        mapping_topics=[{
            "title": "共振条件の導出", "description": "d", "linked_component_ids": ["comp_title"],
        }],
    )
    topic = ccb._enrich_topics([{"id": "t1", "title": "共振条件の導出"}], bundle, {}, None, {})[0]
    block = next(b for b in topic["content_blocks"] if b["type"] == "components")
    assert block["items"][0]["display_label"] == ""

    component_item = next(i for i in ccb.build_topic_evidence_items(topic) if i["kind"] == "component")
    assert "display_label" not in component_item


# ---------------------------------------------------------------------------
# 4. blueprint（語りの弧, §6.5）
# ---------------------------------------------------------------------------

def test_collect_structured_content_indexes_the_blueprint_arc():
    bundle = ccb._collect_structured_content({
        DOC: {
            "blueprint": {
                "narrative_arc": [
                    {
                        "step": 1,
                        "role": "problem_setup",
                        "visual_strategy": "none",
                        "rationale": "この rationale は投影に載せない",
                        "linked_component_ids": ["comp_001"],
                    },
                    {
                        "step": 2,
                        "role": "core_relation",
                        "visual_strategy": "equation_map",
                        "linked_component_ids": ["comp_002"],
                    },
                ]
            }
        }
    })
    index = bundle["narrative_by_component"]
    assert index["comp_001"]["role"] == "problem_setup"
    assert index["comp_002"]["visual_strategy"] == "equation_map"
    assert index["comp_001"]["order"] < index["comp_002"]["order"]
    assert "rationale" not in index["comp_001"]


def test_missing_or_malformed_blueprint_is_skipped():
    for artifacts in ({}, {"blueprint": None}, {"blueprint": {"narrative_arc": "x"}},
                      {"blueprint": {"narrative_arc": ["not a dict"]}}):
        assert ccb._collect_structured_content({DOC: artifacts})["narrative_by_component"] == {}


def test_topic_narrative_is_derived_in_arc_order_without_numbers():
    bundle = _bundle(
        components={
            "comp_001": _component("comp_001", "A"),
            "comp_002": _component("comp_002", "B"),
        },
        narrative={
            "comp_002": {"role": "core_relation", "visual_strategy": "equation_map", "order": 2},
            "comp_001": {"role": "problem_setup", "visual_strategy": "none", "order": 1},
        },
    )
    units = {"k1:u": _unit_row("k1:u", "単位", component_agent_ids=["comp_001", "comp_002"])}

    topic = ccb._enrich_topics([_topic_with_units("k1:u")], bundle, {}, None, units)[0]

    assert topic["narrative"] == {
        "roles": ["problem_setup", "core_relation"],
        "visual_strategy": "equation_map",
    }
    # プロンプト文脈にも role / visual_strategy だけが渡る（rationale / 数値なし）。
    context = ccb._topic_context_for_prompt(topic)
    assert context["narrative"]["roles"] == ["problem_setup", "core_relation"]
    assert set(context["narrative"]) == {"roles", "visual_strategy"}


def test_topic_without_narrative_material_has_no_narrative_key():
    bundle = _bundle(components={"comp_001": _component("comp_001", "A")})
    units = {"k1:u": _unit_row("k1:u", "単位", component_agent_ids=["comp_001"])}
    topic = ccb._enrich_topics([_topic_with_units("k1:u")], bundle, {}, None, units)[0]
    assert "narrative" not in topic
    assert "narrative" not in ccb._topic_context_for_prompt(topic)


# ---------------------------------------------------------------------------
# 5. 学習者向け DTO 射影（KO10）
# ---------------------------------------------------------------------------

def test_learner_projection_hides_stable_key_and_unit_id():
    topic = _topic_with_units("k1:a", "k1:b")
    projected = learner_topic_units_projection(topic)

    assert projected == [
        {"kind": "parent_component", "label": "unit k1:a"},
        {"kind": "parent_component", "label": "unit k1:b"},
    ]
    flattened = str(projected)
    assert "k1:a" not in flattened.replace("unit k1:a", "")
    for item in projected:
        assert set(item) == {"kind", "label"}


def test_learner_projection_drops_units_without_a_label():
    topic = {"units": [{"kind": "figure", "stable_key": "k1:x", "label": "  "}]}
    assert learner_topic_units_projection(topic) == []


def test_topic_units_accessors_are_defensive():
    assert topic_units(None) == []
    assert topic_units({"units": "nope"}) == []
    # stable_key の無い要素は落とす（freeze 側で何も束ねられないため）。
    assert topic_units({"units": [{"label": "x"}, "str", None]}) == []
    assert topic_unit_keys({"units": [{"stable_key": "k"}, {"stable_key": "k"}]}) == ["k"]


# ---------------------------------------------------------------------------
# 6. 読みは live ビューのみ・fail-soft
# ---------------------------------------------------------------------------

def test_load_learning_units_is_fail_soft_and_skips_without_documents():
    from unittest.mock import MagicMock

    session = MagicMock()
    assert ccb._load_learning_units(session, []) == {}
    session.execute.assert_not_called()

    broken = MagicMock()
    broken.execute.side_effect = RuntimeError("relation does not exist")
    assert ccb._load_learning_units(broken, [DOC]) == {}
    broken.rollback.assert_called_once()


def test_load_learning_units_reads_the_live_view():
    from unittest.mock import MagicMock

    session = MagicMock()
    session.execute.return_value.fetchall.return_value = []
    ccb._load_learning_units(session, [DOC])
    assert "learning_units_live" in str(session.execute.call_args.args[0])
