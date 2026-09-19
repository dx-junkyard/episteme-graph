"""画面文脈アダプター — route 配線（W層 messages / graph-sessions messages）のテスト。

正本: ``docs/features/assistant_screen_adapter_design.md`` §4.2 / §4.4 / §8。
DB / LLM への実接続は行わず、route 関数を直接呼んで monkeypatch で分離する
（``test_graph_review_api.py`` と同じ流儀）。

ここで固定するのは4点:

- 一致する ``selection.document_id`` のときだけ事実文ブロックが LLM 入力の先頭に付く。
- 保存される message content は**常に生の発話**（SA6）。
- 不一致・未知 screen・論文層の失敗では**静かに何も足さない**（SA2 fail-soft）。
- ``screen_context`` 無しのリクエストの LLM 入力が従来と完全に同一（回帰）。
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

import routes.deliberation as delib_routes  # noqa: E402
import routes.theory_components as tc  # noqa: E402
from core.assistant_context import BLOCK_HEADER  # noqa: E402
from core.deliberation import graph_dialogue as gd  # noqa: E402
from core.deliberation.dialogue import DialogueTurnResult  # noqa: E402
from core.deliberation.schema import (  # noqa: E402
    ELEMENT_SHARED_PART,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
)

_TEACHER = {"id": "22222222-2222-2222-2222-222222222222", "role": "TEACHER"}
_DOC = "11111111-1111-1111-1111-111111111111"
_OTHER_DOC = "99999999-9999-9999-9999-999999999999"
_SESSION = "55555555-5555-5555-5555-555555555555"
_NODE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

_NODE_LABEL = "Equation system"
_EQUATION_LABEL = "式 (12)"


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


def _paper_layer_dto():
    """解決器が事実文を作れる最小の論文層 DTO（`graph_paper_layer_design.md` §3 の形）。"""
    return {
        "document_id": _DOC,
        "available": True,
        "facts": [],
        "paper": {
            "title": "A Paper",
            "sections": [
                {"section_id": "s2", "title": "Method", "page_start": 4, "node_ids": [_NODE]},
            ],
        },
        "nodes": {
            _NODE: {
                "node_id": _NODE,
                "graph_layer": "main",
                "label": _NODE_LABEL,
                "narrative_role": "",
                "component": None,
                "explanation": None,
                "thesis_roles": [],
                "sections": [{"section_id": "s2", "title": "Method", "page_start": 4}],
                "equations": [
                    {
                        "equation_id": "eq_12",
                        "display_label": _EQUATION_LABEL,
                        "role": "input",
                        "plain_text": "x = y",
                        "section_id": "s2",
                    }
                ],
                "claims": [],
                "evidence": [],
                "figures": [],
                "tables": [],
                "symbols": [],
                "derivations": [],
                "unlocated": False,
            }
        },
        "edges": {},
        "coverage": {
            "unbound_sections": [],
            "unbound_equations": [],
            "unbound_figures": [],
            "unbound_claims": [],
        },
        "narrative": {"graph_summary": ""},
    }


def _screen_context(*, document_id=_DOC, node_id=_NODE, screen="graph_review", view=None):
    return {
        "screen": screen,
        "selection": {"document_id": document_id, "node_id": node_id},
        "view": view if view is not None else {},
        "visible_entities": [],
    }


def _graph_session(element_type="document_graph", element_id=_DOC):
    return {
        "id": _SESSION,
        "scope": "document",
        "element_type": element_type,
        "element_id": element_id,
        "document_id": _DOC,
        "domain_key": None,
        "title": "",
        "messages": [],
        "created_by": _TEACHER["id"],
        "created_at": "",
        "updated_at": "",
    }


class _Access:
    def __init__(self, found=True, can_view=True, document_id=_DOC):
        self.found = found
        self.can_view = can_view
        self.document_id = document_id


# ---------------------------------------------------------------------------
# グラフ全体対話（graph-sessions messages）
# ---------------------------------------------------------------------------


class _GraphHarness:
    """route を実行し、LLM 入力と保存 content を捕まえる。"""

    def __init__(self, monkeypatch, *, paper_layer=None):
        self.seen: dict = {}
        monkeypatch.setattr(
            delib_routes, "resolve_document_access", lambda uid, ref: _Access()
        )
        monkeypatch.setattr(
            delib_routes.delib_store, "get_session_by_id", lambda sid: _graph_session()
        )
        monkeypatch.setattr(gd, "load_latest_graph", lambda doc: {"nodes": [{"component_id": "m1"}]})
        monkeypatch.setattr(gd, "build_graph_grounding", lambda graph: {"nodes": []})
        monkeypatch.setattr(gd, "graph_grounding_to_text", lambda g: "GRAPH GROUNDING")
        monkeypatch.setattr(delib_routes.dialogue, "check_and_count_llm_call", lambda sid, uid: True)

        def _run(doc, **kwargs):
            self.seen["llm_user_content"] = kwargs["user_content"]
            return gd.GraphTurnResult(reply="仮説です", degraded=False)

        monkeypatch.setattr(gd, "run_graph_turn", _run)
        monkeypatch.setattr(
            delib_routes.delib_store,
            "append_messages",
            lambda sid, msgs: self.seen.__setitem__("persisted", msgs),
        )

        if paper_layer is not None:
            if isinstance(paper_layer, Exception):
                def _builder(_doc):
                    raise paper_layer
            else:
                def _builder(_doc):
                    self.seen["paper_layer_doc"] = _doc
                    return paper_layer
            monkeypatch.setattr(tc, "build_paper_layer_for_document", _builder)

    def post(self, **body_kwargs):
        body = delib_routes.GraphMessageCreateRequest(**body_kwargs)
        return delib_routes.post_graph_dialogue_message(
            _DOC, _SESSION, body, current_user=_TEACHER
        )


class TestGraphSessionScreenContext:
    def test_matching_document_prepends_the_block(self, monkeypatch):
        harness = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        result = harness.post(content="ここは何をしている？", screen_context=_screen_context())
        assert result["reply"] == "仮説です"
        llm_input = harness.seen["llm_user_content"]
        assert llm_input.startswith(BLOCK_HEADER)
        assert _NODE_LABEL in llm_input
        assert _EQUATION_LABEL in llm_input
        assert llm_input.endswith("ここは何をしている？")
        # 論文層は当該 document のものだけを引く。
        assert harness.seen["paper_layer_doc"] == _DOC

    def test_persisted_content_is_the_raw_utterance(self, monkeypatch):
        """SA6: 画面文脈は保存しない（message 行に混ぜない）。"""
        harness = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(content="ここは何をしている？", screen_context=_screen_context())
        persisted = harness.seen["persisted"]
        assert [m["role"] for m in persisted] == ["user", "assistant"]
        assert persisted[0]["content"] == "ここは何をしている？"
        assert BLOCK_HEADER not in persisted[0]["content"]

    def test_other_document_reference_is_ignored(self, monkeypatch):
        """他文書の参照で越境させない（§4.4）— LLM 入力は screen_context 無しと同一。"""
        baseline = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        baseline.post(content="質問")
        plain = baseline.seen["llm_user_content"]

        harness = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(content="質問", screen_context=_screen_context(document_id=_OTHER_DOC))
        assert harness.seen["llm_user_content"] == plain
        assert "paper_layer_doc" not in harness.seen  # 論文層すら引かない

    def test_unknown_screen_is_ignored_without_422(self, monkeypatch):
        harness = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        result = harness.post(content="質問", screen_context=_screen_context(screen="mystery_tab"))
        assert result["reply"] == "仮説です"
        assert harness.seen["llm_user_content"] == "質問"

    def test_paper_layer_failure_degrades_silently(self, monkeypatch):
        """SA2: 論文層が引けなくても対話は止めず、「見えないものがある」とも言わない。"""
        harness = _GraphHarness(monkeypatch, paper_layer=RuntimeError("db down"))
        result = harness.post(content="質問", screen_context=_screen_context())
        assert result["reply"] == "仮説です"
        assert harness.seen["llm_user_content"] == "質問"

    def test_paper_layer_failure_keeps_view_fact_only(self, monkeypatch):
        """論文層に依存しない事実（表示モード）だけは残る — それでも発話は末尾のまま。"""
        harness = _GraphHarness(monkeypatch, paper_layer=RuntimeError("db down"))
        harness.post(
            content="質問",
            screen_context=_screen_context(view={"mode": "paper"}),
        )
        llm_input = harness.seen["llm_user_content"]
        assert llm_input.startswith(BLOCK_HEADER)
        assert _NODE_LABEL not in llm_input
        assert llm_input.endswith("質問")

    def test_without_screen_context_input_is_unchanged(self, monkeypatch):
        """回帰: 従来のリクエストは LLM 入力が生の発話そのもの。"""
        harness = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(content="質問")
        assert harness.seen["llm_user_content"] == "質問"
        assert "paper_layer_doc" not in harness.seen

    def test_screen_context_is_not_resolved_before_the_cost_gate(self, monkeypatch):
        """CostGate 消費位置は不変（429 経路では論文層を引かない）。"""
        harness = _GraphHarness(monkeypatch, paper_layer=_paper_layer_dto())
        monkeypatch.setattr(delib_routes.dialogue, "check_and_count_llm_call", lambda sid, uid: False)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            harness.post(content="質問", screen_context=_screen_context())
        assert exc.value.status_code == 429
        assert "paper_layer_doc" not in harness.seen


# ---------------------------------------------------------------------------
# 要素対話（sessions messages）
# ---------------------------------------------------------------------------


def _element_session(element_type="theory_claim", element_id="c1"):
    return {
        "id": _SESSION,
        "created_by": _TEACHER["id"],
        "scope": "document",
        "element_type": element_type,
        "element_id": element_id,
        "document_id": _DOC,
        "domain_key": None,
        "title": "",
        "messages": [],
        "created_at": "",
    }


class _ElementHarness:
    def __init__(self, monkeypatch, *, paper_layer=None, ref=None):
        self.seen: dict = {}
        resolved = ref or ElementRef(
            scope=SCOPE_DOCUMENT, element_type="theory_claim", element_id="c1", document_id=_DOC
        )
        monkeypatch.setattr(
            delib_routes.delib_store, "get_session_by_id", lambda sid: _element_session()
        )
        monkeypatch.setattr(delib_routes.refs, "resolve", lambda *a, **k: resolved)
        monkeypatch.setattr(delib_routes, "_ensure_document_viewable", lambda *a, **k: None)
        monkeypatch.setattr(delib_routes.dialogue, "check_and_count_llm_call", lambda *a, **k: True)
        monkeypatch.setattr(
            delib_routes.dialogue,
            "build_grounding",
            lambda ref: {"breakdown": {}, "positioning": {"available": False}},
        )
        monkeypatch.setattr(delib_routes.dialogue, "grounding_to_text", lambda g: "GROUNDING")

        def _run(_ref, **kwargs):
            self.seen["llm_user_content"] = kwargs["user_content"]
            return DialogueTurnResult(reply="回答です", annotations=[], degraded=False)

        monkeypatch.setattr(delib_routes.dialogue, "run_turn", _run)
        monkeypatch.setattr(
            delib_routes.delib_store,
            "append_messages",
            lambda sid, msgs: self.seen.__setitem__("persisted", msgs),
        )
        monkeypatch.setattr(
            delib_routes.delib_annotations, "create_candidates_from_dialogue", lambda *a, **k: []
        )
        monkeypatch.setattr(delib_routes, "record_review_event", lambda *a, **k: None)

        if paper_layer is not None:
            if isinstance(paper_layer, Exception):
                def _builder(_doc):
                    raise paper_layer
            else:
                def _builder(_doc):
                    self.seen["paper_layer_doc"] = _doc
                    return paper_layer
            monkeypatch.setattr(tc, "build_paper_layer_for_document", _builder)

    def post(self, **body_kwargs):
        body = delib_routes.MessageCreateRequest(**body_kwargs)
        return delib_routes.post_deliberation_message(_SESSION, body, current_user=_TEACHER)


class TestElementSessionScreenContext:
    def test_matching_document_prepends_the_block(self, monkeypatch):
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto())
        result = harness.post(content="これは何ですか？", screen_context=_screen_context())
        assert result["reply"] == "回答です"
        llm_input = harness.seen["llm_user_content"]
        assert llm_input.startswith(BLOCK_HEADER)
        assert _NODE_LABEL in llm_input
        assert llm_input.endswith("これは何ですか？")

    def test_block_precedes_the_selected_context_hint(self, monkeypatch):
        """既存の selected_context 前置きは残し、画面文脈は独立ブロックとして先頭に置く。"""
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(
            content="これは何ですか？",
            selected_context={"kind": "part", "id": "sensor-1", "label": "センサー"},
            screen_context=_screen_context(),
        )
        llm_input = harness.seen["llm_user_content"]
        assert llm_input.startswith(BLOCK_HEADER)
        assert llm_input.index(BLOCK_HEADER) < llm_input.index("kind=part; id=sensor-1")
        assert llm_input.index("kind=part; id=sensor-1") < llm_input.index("これは何ですか？")

    def test_persisted_content_is_the_raw_utterance(self, monkeypatch):
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(
            content="これは何ですか？",
            selected_context={"kind": "part", "id": "sensor-1", "label": "センサー"},
            screen_context=_screen_context(),
        )
        persisted = harness.seen["persisted"]
        assert persisted[0]["content"] == "これは何ですか？"
        assert BLOCK_HEADER not in persisted[0]["content"]
        assert "sensor-1" not in persisted[0]["content"]

    def test_other_document_reference_is_ignored(self, monkeypatch):
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(content="質問", screen_context=_screen_context(document_id=_OTHER_DOC))
        assert harness.seen["llm_user_content"] == "質問"
        assert "paper_layer_doc" not in harness.seen

    def test_unknown_screen_is_ignored_without_422(self, monkeypatch):
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto())
        result = harness.post(content="質問", screen_context=_screen_context(screen="mystery_tab"))
        assert result["reply"] == "回答です"
        assert harness.seen["llm_user_content"] == "質問"

    def test_paper_layer_failure_degrades_silently(self, monkeypatch):
        harness = _ElementHarness(monkeypatch, paper_layer=RuntimeError("db down"))
        result = harness.post(content="質問", screen_context=_screen_context())
        assert result["reply"] == "回答です"
        assert harness.seen["llm_user_content"] == "質問"

    def test_without_screen_context_input_is_unchanged(self, monkeypatch):
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto())
        harness.post(content="質問")
        assert harness.seen["llm_user_content"] == "質問"
        assert "paper_layer_doc" not in harness.seen

    def test_domain_scope_element_never_resolves_a_paper_layer(self, monkeypatch):
        """共通部品（domain scope）は論文層を持たない — 参照があっても解決しない。"""
        shared = ElementRef(
            scope=SCOPE_DOMAIN,
            element_type=ELEMENT_SHARED_PART,
            element_id="sp-1",
            domain_key="particle_physics",
        )
        harness = _ElementHarness(monkeypatch, paper_layer=_paper_layer_dto(), ref=shared)
        monkeypatch.setattr(
            delib_routes.dialogue, "figure_image_bytes", lambda *a, **k: None
        )
        harness.post(content="質問", screen_context=_screen_context())
        assert harness.seen["llm_user_content"] == "質問"
        assert "paper_layer_doc" not in harness.seen


# ---------------------------------------------------------------------------
# リクエストモデル（§4.2）— 未知・上限超過で 422 にしない
# ---------------------------------------------------------------------------


class TestScreenContextPayload:
    def test_visible_entities_are_truncated_not_rejected(self):
        payload = delib_routes.ScreenContextPayload(
            screen="graph_review",
            visible_entities=[{"type": "node", "id": "a" * 500, "title": "t" * 200}] * 50,
        )
        from core.assistant_context.schema import (
            MAX_ID_CHARS,
            MAX_TITLE_CHARS,
            MAX_VISIBLE_ENTITIES,
        )

        assert len(payload.visible_entities) == MAX_VISIBLE_ENTITIES
        assert len(payload.visible_entities[0]["id"]) == MAX_ID_CHARS
        assert len(payload.visible_entities[0]["title"]) == MAX_TITLE_CHARS

    def test_long_screen_is_truncated_not_rejected(self):
        payload = delib_routes.ScreenContextPayload(screen="x" * 500)
        assert len(payload.screen) == 40

    def test_unknown_extra_field_is_rejected(self):
        """SA1: 描画テキスト・DTO 本体を送り込む口を開けない（extra="forbid"）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            delib_routes.ScreenContextPayload(screen="graph_review", rendered_text="本文丸ごと")

    def test_defaults_are_empty(self):
        payload = delib_routes.ScreenContextPayload(screen="graph_review")
        assert payload.selection == {}
        assert payload.view == {}
        assert payload.visible_entities == []

    def test_both_message_models_accept_the_field(self):
        assert "screen_context" in delib_routes.MessageCreateRequest.model_fields
        assert "screen_context" in delib_routes.GraphMessageCreateRequest.model_fields


# ---------------------------------------------------------------------------
# ガードレール（§8）— 保存 content と LLM 入力を別に保つ配線をソースで固定する
# ---------------------------------------------------------------------------


class TestRouteWiringGuardrails:
    ROUTE_SRC = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")

    def _body(self, name):
        from tests.guardrail_helpers import extract_function_source

        return extract_function_source(self.ROUTE_SRC, name)

    def _append_call(self, name):
        """``delib_store.append_messages(...)`` 呼び出しの引数部分だけを取り出す。"""
        import re

        body = self._body(name)
        match = re.search(r"append_messages\((.*?)\n    \)", body, re.S)
        assert match is not None, f"append_messages call not found in {name}"
        return match.group(1)

    def test_element_route_persists_the_raw_user_content(self):
        body = self._body("post_deliberation_message")
        assert '{"role": "user", "content": user_content' in body
        # 保存側に LLM 入力用の変数（画面文脈ブロック入り）を渡していない（SA6）。
        assert "llm_user_content" not in self._append_call("post_deliberation_message")

    def test_graph_route_persists_the_raw_user_content(self):
        body = self._body("post_graph_dialogue_message")
        assert '{"role": "user", "content": user_content' in body
        assert "run_graph_turn" in body
        assert "user_content=llm_user_content" in body
        assert "llm_user_content" not in self._append_call("post_graph_dialogue_message")

    def test_both_routes_use_the_shared_helper(self):
        for name in ("post_deliberation_message", "post_graph_dialogue_message"):
            assert "_screen_context_block(" in self._body(name)

    def test_helper_gates_on_the_session_document(self):
        body = self._body("_screen_context_block")
        assert "normalize_screen_context(" in body
        assert 'ctx.selection.get("document_id")' in body
        assert "build_paper_layer_for_document" in body

    def test_helper_does_not_write_or_audit(self):
        body = self._body("_screen_context_block")
        for forbidden in ("append_messages", "record_review_event", "INSERT", "UPDATE"):
            assert forbidden not in body

    def test_cost_gate_precedes_the_screen_context_resolution(self):
        """CostGate 消費位置は不変（§4.4）— 解決はゲート通過後にだけ走る。"""
        for name, gate in (
            ("post_deliberation_message", "dialogue.check_and_count_llm_call("),
            ("post_graph_dialogue_message", "dialogue.check_and_count_llm_call("),
        ):
            body = self._body(name)
            assert body.index(gate) < body.index("_screen_context_block(")
