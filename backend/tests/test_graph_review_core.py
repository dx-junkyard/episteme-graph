"""グラフ対話レビュー — core（graph_dialogue）の単体テスト。

正本: ``docs/features/graph_dialogue_review_design.md`` §5。
grounding の決定論構築・テキスト整形・メッセージ組み立て・degraded 縮退を、
DB / LLM への実接続なしで検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.deliberation import graph_dialogue as gd  # noqa: E402


def _graph(nodes=None, edges=None, **extra):
    payload = {"nodes": nodes or [], "edges": edges or []}
    payload.update(extra)
    return payload


def _node(component_id, **overrides):
    node = {
        "component_id": component_id,
        "label": "Theory basis",
        "description": "",
        "graph_layer": "main",
        "display_order": 0,
        "source_backing_status": "source_backed",
        "review_status": "teacher_review_required",
        "review_reasons": [],
    }
    node.update(overrides)
    return node


class TestBuildGraphGrounding:
    def test_empty_graph_raises(self):
        with pytest.raises(gd.GraphNotAvailableError):
            gd.build_graph_grounding(_graph())

    def test_nodes_without_id_are_ignored(self):
        with pytest.raises(gd.GraphNotAvailableError):
            gd.build_graph_grounding(_graph(nodes=[{"label": "no id"}]))

    def test_layers_are_split_and_counted(self):
        grounding = gd.build_graph_grounding(_graph(nodes=[
            _node("m1"),
            _node("d1", graph_layer="equation_detail"),
            _node("d2", graph_layer="equation_detail"),
            _node("x1", graph_layer="debug"),
        ]))
        assert [gd._node_id(n) for n in grounding["main_nodes"]] == ["m1"]
        assert grounding["detail_node_count"] == 2
        assert grounding["debug_node_count"] == 1

    def test_main_nodes_sorted_by_display_order(self):
        grounding = gd.build_graph_grounding(_graph(nodes=[
            _node("b", display_order=2, label="B"),
            _node("a", display_order=1, label="A"),
        ]))
        assert [gd._node_id(n) for n in grounding["main_nodes"]] == ["a", "b"]

    def test_unreviewed_excludes_approved_rejected_and_debug(self):
        grounding = gd.build_graph_grounding(_graph(nodes=[
            _node("pending"),
            _node("ok", review_status="teacher_approved"),
            _node("ok2", review_status="teacher_reviewed"),
            _node("ng", review_status="rejected"),
            _node("dbg", graph_layer="debug"),
        ]))
        assert [gd._node_id(n) for n in grounding["unreviewed_nodes"]] == ["pending"]


class TestGroundingToText:
    def test_contains_labels_and_backing_vocab(self):
        grounding = gd.build_graph_grounding(_graph(
            nodes=[
                _node("m1", label="Theory basis", description="基礎方程式の前提",
                      source_backing_status="partially_source_backed"),
                _node("m2", label="Elimination", display_order=1,
                      review_status="review_required",
                      review_reasons=["missing_atomic_claim"]),
            ],
            edges=[{
                "source_component_id": "m1", "target_component_id": "m2",
                "edge_type": "derives", "source_backing_status": "source_backed",
                # confidence の生値は grounding テキストに出してはならない（GR3）
                "confidence": 0.87,
            }],
        ))
        text = gd.graph_grounding_to_text(grounding)
        assert "[主グラフ（理論構成のバックボーン）]" in text
        assert "Theory basis" in text and "基礎方程式の前提" in text
        assert "部分的な裏付け" in text
        assert "[主グラフの関係]" in text and "derives" in text
        assert "[未レビューのノード]" in text and "missing_atomic_claim" in text
        assert "0.87" not in text  # 数値 confidence 非漏洩

    def test_main_node_cap_reports_omission(self):
        nodes = [_node("n%d" % i, display_order=i) for i in range(45)]
        text = gd.graph_grounding_to_text(gd.build_graph_grounding(_graph(nodes=nodes)))
        assert "省略" in text

    def test_narrative_marked_as_ai_proposal(self):
        grounding = gd.build_graph_grounding(_graph(
            nodes=[_node("m1")],
            narrative={"summary": "この論文は…"},
        ))
        text = gd.graph_grounding_to_text(grounding)
        assert "AI提案・未確認" in text


class TestBuildLlmMessages:
    def test_grounding_injected_only_into_first_user_message(self):
        messages = gd.build_llm_messages(
            [{"role": "user", "content": "1st"}, {"role": "assistant", "content": "a"}],
            "2nd",
            "GROUNDING",
        )
        assert "GROUNDING" in messages[0]["content"]
        assert "GROUNDING" not in messages[2]["content"]
        # 契約フレーズ（仮説文体・承認判断の非代行）がヘッダに含まれる
        assert "承認・却下の判断は教員が行います" in messages[0]["content"]

    def test_no_prior_messages_injects_into_current(self):
        messages = gd.build_llm_messages([], "こんにちは", "G")
        assert len(messages) == 1
        assert "G" in messages[0]["content"]


class TestRunGraphTurn:
    def test_degraded_on_llm_failure(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("llm down")

        monkeypatch.setattr(gd, "generate_conversation_turn", _boom)
        monkeypatch.setattr(gd.dialogue, "resolve_turn_model", lambda feature: "fast-model")
        result = gd.run_graph_turn(
            "doc-1", prior_messages=[], user_content="q", grounding_text="g",
        )
        assert result.degraded is True
        assert result.reply  # 縮退でも空応答にしない

    def test_reply_returned_without_annotations(self, monkeypatch):
        class _Out:
            reply = "仮説の応答"

        monkeypatch.setattr(gd, "generate_conversation_turn", lambda *a, **k: _Out())
        monkeypatch.setattr(gd.dialogue, "resolve_turn_model", lambda feature: "fast-model")
        result = gd.run_graph_turn(
            "doc-1", prior_messages=[], user_content="q", grounding_text="g",
        )
        assert result.reply == "仮説の応答"
        assert result.degraded is False
        # GraphTurnResult は注釈フィールド自体を持たない（グラフ全体対話は注釈なし）
        assert not hasattr(result, "annotations")
