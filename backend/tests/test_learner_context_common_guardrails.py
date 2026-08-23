"""``core/learner_context_common.py``（学習者向け文脈 API の共通プリミティブ）の
ガードレール。

このモジュールは ``core/component_context.py``（component）と
``core/element_context.py``（claim / equation）が**同じ責務を二度持っていた**状態を
解消するために新設された正本である。守るべきは次の3点:

1. 正本が1つであること（両モジュールの公開名が同一オブジェクトを指す = 片方だけに
   遮断が実装される状態へ戻らない）
2. core/ 共通ルール（FastAPI / routes を import しない）
3. 読み取り専用（書き込み SQL を持たない）

加えて、遮断層（内部 ID / 生 TeX を学習者に出さない）と ``navigable`` の
fail-closed 再計算が **component 経路にも効いている**ことを固定する（世代差の解消が
巻き戻らないようにするのがこのファイルの主目的）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import component_context, element_context, learner_context_common  # noqa: E402
from core import text_excerpt  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_does_not_import,
)


class TestCoreRules:
    def test_does_not_import_fastapi_or_routes(self):
        src = Path(learner_context_common.__file__).read_text(encoding="utf-8")
        assert_source_does_not_import(
            src, ["fastapi", "routes", "api."], context="core/learner_context_common.py"
        )

    def test_module_tree_helper_agrees_on_fastapi(self):
        assert_module_tree_does_not_import(
            BACKEND / "core", ["fastapi"], glob="learner_context_common.py"
        )

    def test_no_write_paths(self):
        """読み取り専用: INSERT / UPDATE / DELETE を発行しない。"""
        src = Path(learner_context_common.__file__).read_text(encoding="utf-8").upper()
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
            assert verb not in src, f"learner_context_common.py must not write: {verb}"

    def test_does_not_import_the_two_context_modules(self):
        """逆依存を作らない（従来 element_context → component_context の向きに
        あった import は、本モジュールの新設で解消した）。"""
        src = Path(learner_context_common.__file__).read_text(encoding="utf-8")
        assert_source_does_not_import(
            src,
            ["core.component_context", "core.element_context"],
            context="core/learner_context_common.py",
        )


class TestSingleSourceOfTruth:
    """公開名が同一オブジェクトを指す（実装が2つに分かれていない）。"""

    def test_strip_confidence_is_one_function(self):
        assert component_context.strip_confidence is learner_context_common.strip_confidence
        assert component_context._strip_confidence is learner_context_common.strip_confidence
        assert element_context.strip_confidence is learner_context_common.strip_confidence

    def test_item_projection_is_one_function(self):
        assert element_context._project_item is learner_context_common.project_item

    def test_blocking_helpers_are_one_function_each(self):
        pairs = (
            (element_context._is_internal_id_label, learner_context_common.is_internal_id_label),
            (element_context._contains_internal_id, learner_context_common.contains_internal_id),
            (element_context._generic_item_label, learner_context_common.generic_item_label),
            (element_context._safe_text, learner_context_common.safe_text),
            (element_context._learner_navigable, learner_context_common.learner_navigable),
            (element_context._equation_focus_label, learner_context_common.equation_focus_label),
        )
        for reexported, canonical in pairs:
            assert reexported is canonical, canonical

    def test_lane_max_is_one_value(self):
        assert element_context._LANE_MAX == learner_context_common.LANE_MAX
        assert component_context._GRAPH_LANE_MAX == learner_context_common.LANE_MAX

    def test_provenance_label_is_one_value(self):
        assert element_context.PROVENANCE_COURSE_FREEZE == "course_freeze"
        assert learner_context_common.PROVENANCE_COURSE_FREEZE == "course_freeze"

    def test_tex_detection_stays_bound_to_text_excerpt(self):
        """TeX 判定の正本は ``core/text_excerpt.py``（第2実装を作らない）。"""
        assert learner_context_common.looks_like_tex_math is text_excerpt.looks_like_tex_math


def _item(**kwargs) -> dict:
    base = {
        "element_id": "el-1",
        "element_type": "theory_claim",
        "label": "上位の主張",
        "relation_label": "を支持する",
        "relation_status": "source_backed",
        "navigable": True,
    }
    base.update(kwargs)
    return base


class TestLegacyKeysOnlyProjection:
    """component 文脈 API の ITEM は**旧6キーのまま**（世代差の意図的な維持）。

    ``group`` を足すと統一パーツカード（``element-card.js`` の ``hasGroupedItems``）が
    1件でも group を見つけた時点で4区画描画へ切り替わり、可視の UX 変更になる。
    """

    _LEGACY_KEYS = {"id", "element_type", "label", "relation_label", "relation_status", "navigable"}

    def test_legacy_projection_returns_exactly_six_keys(self):
        projected = learner_context_common.project_item(
            _item(sublabel="区別材料", qualifier="", group="claim", unresolved=False),
            legacy_keys_only=True,
        )
        assert set(projected) == self._LEGACY_KEYS

    def test_component_context_uses_the_legacy_key_set(self):
        projected = component_context._project_context_item(_item(group="claim", sublabel="x"))
        assert set(projected) == self._LEGACY_KEYS

    def test_v2_projection_keeps_the_item_v2_keys(self):
        projected = learner_context_common.project_item(_item(group="claim"))
        assert self._LEGACY_KEYS.issubset(set(projected))
        assert {"sublabel", "qualifier", "group", "unresolved"}.issubset(set(projected))


class TestBlockingReachesComponentLane:
    """A-3: 遮断層と ``navigable`` の fail-closed が component の graph レーンにも効く。

    従来 ``component_context._project_context_item`` は W層の ``label`` と
    ``navigable`` を素通しにしていたため、``comp_003`` のような内部 ID や、学習者向けの
    文脈取得口が無い型（figure / evidence / derivation）の ``navigable: true`` が
    DTO に出ていた（フロントの再ゲートが実害を吸収していたが API 契約としては誤り）。
    """

    def test_internal_component_id_label_is_replaced(self):
        projected = component_context._project_context_item(
            _item(element_type="theory_component", element_id=None, label="comp_003")
        )
        assert projected["label"] == "関連する論理要素"

    def test_uuid_label_is_replaced(self):
        projected = component_context._project_context_item(
            _item(element_type="figure", label="dddddddd-dddd-dddd-dddd-dddddddddddd")
        )
        assert projected["label"] == "図"

    def test_raw_tex_label_is_replaced(self):
        projected = component_context._project_context_item(
            _item(element_type="equation", element_id="eq_tex_b14", label=r"\frac{a}{b} = \sum_i x_i")
        )
        assert "\\frac" not in projected["label"]
        assert projected["label"] == "関連する数式"

    def test_paper_equation_number_survives(self):
        """``eq_2_7`` は論文由来の式番号なので置換しない（設計書 §4 の裁定）。"""
        projected = component_context._project_context_item(
            _item(element_type="equation", element_id="eq_2_7", label="eq_2_7")
        )
        assert projected["label"] == "eq_2_7"

    def test_navigable_is_recomputed_for_learner_reachable_types_only(self):
        for element_type in ("theory_claim", "equation", "theory_component"):
            projected = component_context._project_context_item(_item(element_type=element_type))
            assert projected["navigable"] is True, element_type
        for element_type in ("figure", "evidence", "derivation", "section", "thesis", "stage"):
            projected = component_context._project_context_item(
                _item(element_type=element_type, navigable=True)
            )
            assert projected["navigable"] is False, element_type

    def test_navigable_requires_an_id(self):
        projected = component_context._project_context_item(_item(element_id=None))
        assert projected["navigable"] is False


class TestAgentIdLabelBlockingScope:
    """``comp_003`` / ``theory_op_0001`` / ``eq_op_0007`` の遮断範囲は component レーンのみ。

    §2-5 A-3 の裁定範囲は「component の graph レーンへ遮断層を届ける」ことで、
    claim / equation 文脈 API の出力は**従来と同一**に保つ（純粋移設）。agent ID
    トークンの遮断を claim / equation 経路へも広げるかは、同一レーン内で複数の
    ``comp_00X`` が同じ一般ラベルへ collapse する UX の検討（RC6 と同型）を要する
    ため、オーナー判断待ちの繰り延べ項目（提案書 §2-5 実施記録参照）。
    """

    _AGENT_IDS = ("comp_003", "theory_op_0001", "eq_op_0007")

    def test_component_lane_blocks_agent_id_labels(self):
        for raw in self._AGENT_IDS:
            assert learner_context_common.is_internal_id_label(
                raw, "theory_component", None, include_agent_id_tokens=True
            ), raw
            assert learner_context_common.contains_internal_id(raw), raw

    def test_default_label_rule_keeps_the_previous_element_behavior(self):
        """既定（claim / equation 経路）では従来どおり素通り = 出力不変。"""
        for raw in self._AGENT_IDS:
            assert not learner_context_common.is_internal_id_label(
                raw, "theory_component", None
            ), raw

    def test_claim_equation_api_output_is_unchanged(self):
        for raw in self._AGENT_IDS:
            projected = element_context._visible_items([_item(label=raw)])[0]
            assert projected["label"] == raw, raw
            assert projected["unresolved"] is False, raw

    def test_component_lane_replaces_agent_id_labels_end_to_end(self):
        for raw in self._AGENT_IDS:
            projected = component_context._project_context_item(
                _item(element_type="theory_component", element_id=None, label=raw)
            )
            assert projected["label"] == "関連する論理要素", raw

    def test_ordinary_labels_are_untouched(self):
        for label in ("成分分解", "compton 散乱", "赤方偏移の定義", "eq_2_7"):
            assert not learner_context_common.is_internal_id_label(label, "theory_claim", None), label
            assert not learner_context_common.is_internal_id_label(
                label, "theory_claim", None, include_agent_id_tokens=True
            ), label


class TestProjectionSeamIsOnTheProductionPath:
    """``_project_context_item`` は本番経路（``_visible_lane``）が実際に通る seam。

    ``visible_lane_items(project=...)`` 注入により、この seam の期待値テストが
    死んだコードを叩く状態にならないことを固定する（レビュー指摘の再発防止）。
    """

    def test_visible_lane_routes_through_the_seam(self, monkeypatch):
        calls = []
        original = component_context._project_context_item

        def _spy(item):
            calls.append(item)
            return original(item)

        monkeypatch.setattr(component_context, "_project_context_item", _spy)
        lane = component_context._visible_lane([_item(label="通常ラベル")])
        assert len(calls) == 1
        assert lane and set(lane[0]) == {
            "id", "element_type", "label", "relation_label", "relation_status", "navigable",
        }


class TestSharedLaneRules:
    def test_candidate_relations_are_excluded_in_both_key_sets(self):
        items = [_item(element_id="keep"), _item(element_id="drop", relation_status="candidate")]
        for legacy in (True, False):
            projected = learner_context_common.visible_lane_items(items, legacy_keys_only=legacy)
            assert [i["id"] for i in projected] == ["keep"], legacy

    def test_lane_is_capped_at_lane_max(self):
        items = [_item(element_id=f"e{i}") for i in range(30)]
        assert (
            len(learner_context_common.visible_lane_items(items))
            == learner_context_common.LANE_MAX
        )

    def test_non_dict_entries_are_skipped(self):
        assert learner_context_common.visible_lane_items([None, "x", 1]) == []
        assert learner_context_common.visible_lane_items(None) == []

    def test_equation_detail_qualifier_is_dropped_in_both_key_sets(self):
        item = _item(qualifier=learner_context_common.QUALIFIER_EQUATION_DETAIL)
        for legacy in (True, False):
            assert learner_context_common.project_item(item, legacy_keys_only=legacy) is None


class TestScopedIdMatchSql:
    """コース document スコープの強制は SQL の WHERE 句で行う（後付けフィルタにしない）。"""

    def test_agent_id_only_binds_legacy_ids(self):
        where_clause, params = learner_context_common.scoped_id_match_sql("comp_001", ["doc-a"])
        assert where_clause == "source_scope->'legacy_ids' ? :raw_id"
        assert params == {"raw_id": "comp_001", "doc_ids": ["doc-a"]}
        assert "uuid_id" not in params

    def test_uuid_also_binds_the_id_column(self):
        uuid_value = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        where_clause, params = learner_context_common.scoped_id_match_sql(uuid_value, ["doc-a"])
        assert "id = CAST(:uuid_id AS uuid)" in where_clause
        assert params["uuid_id"] == uuid_value

    def test_both_resolvers_use_the_shared_builder(self):
        """component / claim の解決関数が WHERE 断片を各自で組み立て直していない。"""
        for module in (component_context, element_context):
            src = Path(module.__file__).read_text(encoding="utf-8")
            assert "scoped_id_match_sql(" in src, module.__name__
            assert "source_scope->'legacy_ids' ? :raw_id" not in src, module.__name__
