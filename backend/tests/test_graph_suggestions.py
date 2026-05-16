"""Issue #160: チャンク別グラフサジェストのテスト。"""

from __future__ import annotations


def test_derive_graph_mentions_from_chunk_concepts_and_relationships():
    from api.services import _derive_graph_mentions

    graph = {
        "concepts": [
            {
                "id": "heavy_quark_limit",
                "name": "重いクォーク極限",
                "description": "重いクォーク質量を無限大に近づける近似。",
                "type": "definition",
            },
            {
                "id": "zero_recoil",
                "name": "ゼロ反跳極限",
                "description": "終状態粒子の反跳が消える運動学的極限。",
                "type": "definition",
            },
        ],
        "relationships": [
            {
                "source": "heavy_quark_limit",
                "target": "zero_recoil",
                "relation": "APPLIES_TO",
                "description": "両者を合わせると対称性関係が簡潔になる。",
            },
        ],
    }
    text = "重いクォーク極限とゼロ反跳極限から和則を導出する。"

    mentions = _derive_graph_mentions(text, graph, [])

    ids = {m["element_id"] for m in mentions}
    types = {m["element_type"] for m in mentions}
    assert "heavy_quark_limit" in ids
    assert "zero_recoil" in ids
    assert "heavy_quark_limit:APPLIES_TO:zero_recoil" in ids
    assert "concept" in types
    assert "relationship" in types


def test_derive_graph_mentions_includes_formulas():
    from api.services import _derive_graph_mentions

    mentions = _derive_graph_mentions(
        "式 [[FORMULA_1]] を使う。",
        {"concepts": [], "relationships": []},
        [{"id": "[[FORMULA_1]]", "latex": "R_D/R_D^{SM}"}],
    )

    assert mentions[0]["element_type"] == "formula"
    assert mentions[0]["label"] == "この数式"


def test_derive_graph_mentions_includes_tex_refs_and_citations():
    from api.services import _derive_graph_mentions

    mentions = _derive_graph_mentions(
        "Eq. eq:main follows from prior work Maldacena:2002vr.",
        {"concepts": [], "relationships": []},
        [],
        {
            "tex_refs": [{"key": "eq:main", "kind": "ref"}],
            "tex_citations": [
                {
                    "key": "Maldacena:2002vr",
                    "bib": {
                        "author": "Maldacena, Juan Martin",
                        "title": "Non-Gaussian features of primordial fluctuations",
                        "year": "2003",
                    },
                }
            ],
        },
    )

    by_type = {m["element_type"]: m for m in mentions}
    assert by_type["reference"]["element_id"] == "ref:eq:main"
    assert by_type["citation"]["element_id"] == "bib:Maldacena:2002vr"
    assert by_type["citation"]["label"] == "Maldacena, Juan Martin (2003)"


def test_learning_chat_request_accepts_graph_action_payload():
    from schemas import LearningChatRequest

    req = LearningChatRequest(
        message="重いクォーク極限を説明",
        history=[],
        action="EXPLAIN_GRAPH_ELEMENT",
        chunk_id="00000000-0000-0000-0000-000000000000",
        element_id="heavy_quark_limit",
        element_type="concept",
        element_label="重いクォーク極限",
    )

    assert req.action == "EXPLAIN_GRAPH_ELEMENT"
    assert req.element_id == "heavy_quark_limit"
