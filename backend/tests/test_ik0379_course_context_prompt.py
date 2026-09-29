"""IK-0379: コース生成が LLM へ渡す文脈の是正。

(a) コースビルダーの「コンポーネント依存関係」行は保存形
    ``source_component_id`` / ``target_component_id`` を読み、名前へ解決する。
    端点が空の辺は行にしない（``-  ->   (Type: derives)`` を渡さない）。
(b) トピック草案プロンプトの JSON は文字数予算で**要素単位**に間引き、常に読める。
    ``available_references`` は先頭に置き、最後まで保護される。
(c) 空の値（空の参照一覧など）はプロンプトへ渡す前に落とす。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

_backend_dir = str(Path(__file__).resolve().parents[1])
_api_dir = str(Path(__file__).resolve().parents[1] / "api")
for _p in (_backend_dir, _api_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# patch("routes.admin....") の前に明示ロードする（test_course_builder_context.py と同じ）。
import routes.admin as _routes_admin_mod  # noqa: E402,F401


# ---------------------------------------------------------------------------
# (a) 依存関係の端点
# ---------------------------------------------------------------------------

def _stored_graph():
    # persistence.persist_component_graph が保存する形（ComponentGraphAgent 経路）。
    return {
        "nodes": [
            {"id": "db-1", "component_id": "db-1", "label": "Theory basis",
             "display_label": "定義: 有効作用"},
            {"id": "db-2", "component_id": "db-2", "label": "Equation system"},
        ],
        "edges": [
            {"source_component_id": "db-1", "target_component_id": "db-2",
             "relation": "derives", "edge_type": "derives",
             "evidence": {"reason": "式 (3) から式 (5) を導く"}},
            {"source_component_id": "db-2", "target_component_id": "db-3",
             "relation": "constrains"},
            # 端点が空の辺（旧実装では ``-  ->   (Type: derives)`` になっていた）
            {"source_component_id": "", "target_component_id": "db-2",
             "relation": "derives"},
            {"relation": "derives"},
            "not-a-dict",
        ],
    }


def test_dependency_lines_read_stored_endpoint_keys_and_resolve_names():
    from routes.admin import _component_dependency_lines

    lines = _component_dependency_lines(
        _stored_graph(), component_names={"db-3": "制約コンポーネント"}, limit=80,
    )
    assert lines == [
        "- 定義: 有効作用 (db-1) -> Equation system (db-2)  (Type: derives)"
        "  Reason: 式 (3) から式 (5) を導く",
        "- Equation system (db-2) -> 制約コンポーネント (db-3)  (Type: constrains)",
    ]
    assert not any(line.startswith("-  ->") for line in lines)


def test_dependency_lines_keep_legacy_keys_and_fall_back_to_id():
    from routes.admin import _component_dependency_lines

    graph = {"nodes": [], "edges": [
        {"source": "comp-a", "target": "comp-b", "type": "requires"},
        {"from": "comp-b", "to": "comp-c"},
    ]}
    assert _component_dependency_lines(graph, limit=80) == [
        "- comp-a -> comp-b  (Type: requires)",
        "- comp-b -> comp-c",
    ]


def test_dependency_lines_limit_counts_rendered_lines_only():
    from routes.admin import _component_dependency_lines

    edges = [{"relation": "derives"}] * 5 + [
        {"source_component_id": f"c{i}", "target_component_id": f"c{i+1}"}
        for i in range(10)
    ]
    lines = _component_dependency_lines({"edges": edges}, limit=3)
    assert len(lines) == 3
    assert lines[0] == "- c0 -> c1"


def test_material_context_has_no_empty_endpoint_lines():
    """_build_material_context 経由でも、端点が空の行は出ず名前が付く。"""
    from unittest.mock import MagicMock
    from routes.admin import _build_material_context

    doc_uuid = "uuid-ik0379"
    doc_rows = [("mat-ik0379", "Doc", "doc.pdf", doc_uuid, "completed", {})]
    comp_rows = [(
        "db-3", doc_uuid, "制約コンポーネント", "theory", "", "要約",
        [], [], [], [], [], [], "teacher_review_required", "paper_claim",
    )]
    graph_rows = [(doc_uuid, _stored_graph())]
    session = MagicMock()
    session.execute.return_value.fetchall.side_effect = [
        doc_rows, comp_rows, graph_rows, [], [], [],
    ]
    with patch("routes.admin._pg_session", return_value=session):
        ctx = _build_material_context(["mat-ik0379"])
    assert ctx is not None
    assert "#### コンポーネント依存関係" in ctx
    assert "-  ->" not in ctx
    assert "Equation system (db-2) -> 制約コンポーネント (db-3)" in ctx


def test_material_context_omits_dependency_section_when_no_edge_survives():
    from unittest.mock import MagicMock
    from routes.admin import _build_material_context

    doc_uuid = "uuid-ik0379b"
    doc_rows = [("mat-b", "Doc", "doc.pdf", doc_uuid, "completed", {})]
    graph_rows = [(doc_uuid, {"nodes": [], "edges": [{"relation": "derives"}]})]
    session = MagicMock()
    session.execute.return_value.fetchall.side_effect = [
        doc_rows, [], graph_rows, [], [], [],
    ]
    with patch("routes.admin._pg_session", return_value=session):
        ctx = _build_material_context(["mat-b"])
    assert ctx is not None
    assert "コンポーネント依存関係" not in ctx


# ---------------------------------------------------------------------------
# (b) 予算内でも JSON は読める / available_references は保護される
# ---------------------------------------------------------------------------

def _big_topic():
    return {
        "summary": "要約 " * 400,
        "content": "本文 " * 3000,
        "content_blocks": [
            {"type": "text", "text": "ブロック本文 " * 800},
            {"type": "equations", "items": [
                {"id": f"eq_{i}", "latex": f"E_{{{i}}} = mc^2 " * 20, "plain_text": "説明 " * 30}  # IK-0429: 同じ式は重複として落ちるので本体を変える
                for i in range(60)
            ]},
        ],
        "source_excerpt": "原文 " * 2000,
        "linked_claim_ids": [f"clm_{i}" for i in range(300)],
        "evidence_links": [
            {"kind": "equation", "target_id": f"eq_{i}"} for i in range(10)
        ] + [{"kind": "component", "target_id": "comp_1"}],
    }


def test_topic_evidence_prompt_json_stays_parseable_under_budget():
    from core.course_content_builder import (
        PROMPT_JSON_OMITTED_KEY,
        _prompt_json,
        _topic_evidence_for_prompt,
    )

    started = time.monotonic()
    text = _prompt_json(_topic_evidence_for_prompt(_big_topic()), 8000)
    assert time.monotonic() - started < 5.0
    assert len(text) <= 8000
    parsed = json.loads(text)  # 途中で切れていない
    assert PROMPT_JSON_OMITTED_KEY in parsed
    refs = parsed["available_references"]
    assert ("component", "comp_1") in {(r["kind"], r["id"]) for r in refs}  # IK-0438: 各項目は短い本文 text も持つ
    assert ("source", "excerpt") in {(r["kind"], r["id"]) for r in refs}  # IK-0455: 原文抜粋は配信と同じ id
    assert len(refs) == 11 + 1  # 保護キーは他を間引くあいだ落とさない


def test_available_references_is_first_key():
    from core.course_content_builder import _topic_evidence_for_prompt

    payload = _topic_evidence_for_prompt({"evidence_links": [
        {"kind": "claim", "target_id": "c1"},
    ]})
    assert next(iter(payload)) == "available_references"


def test_prompt_json_within_budget_is_unchanged_except_empties():
    from core.course_content_builder import PROMPT_JSON_OMITTED_KEY, _prompt_json

    value = {"a": "x", "b": [], "c": "", "d": {"e": []}, "f": None, "g": 0, "h": [1, ""]}
    parsed = json.loads(_prompt_json(value, 8000))
    assert parsed == {"a": "x", "f": None, "g": 0, "h": [1]}
    assert PROMPT_JSON_OMITTED_KEY not in parsed


def test_prompt_json_tiny_budget_still_valid_json():
    from core.course_content_builder import _prompt_json

    for budget in (10, 200, 1000):
        text = _prompt_json(_big_topic(), budget)
        json.loads(text)


# ---------------------------------------------------------------------------
# (c) 空の参照一覧は渡さない
# ---------------------------------------------------------------------------

def test_empty_available_references_is_omitted_from_prompt():
    from core.course_content_builder import _generate_single_topic_draft

    topic = {"id": "t1", "title": "セクション1", "summary": "要約"}
    captured = {}

    def fake_structured(*args, **kwargs):
        messages = kwargs.get("messages") or []
        captured["prompt"] = "\n".join(str(m.get("content") or "") for m in messages)
        return {
            "key_concepts": ["概念"],
            "student_material": {"source_format": "eg-markdown-v1", "source_text": "本文"},
            "spoken_script": "読み上げ",
            "cautions": [],
            "check_questions": [],
        }

    with patch(
        "core.course_content_builder.generate_text_with_structured_output",
        side_effect=fake_structured,
    ):
        _generate_single_topic_draft(
            course_context={"title": "コース", "goal": ""},
            topics=[topic], topic=topic, index=0, reasoning_effort=None,
        )

    prompt = captured["prompt"]
    evidence_block = prompt.split("根拠候補:\n", 1)[1].split("\n\n現在の下書き:", 1)[0]
    evidence = json.loads(evidence_block)
    assert "available_references" not in evidence
    assert evidence == {"summary": "要約"}
    assert '"available_references": []' not in prompt
    # 信頼境界の固定文は残る
    from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE
    assert UNTRUSTED_SOURCE_NOTICE in prompt
