"""claim の ``source_scope.legacy_ids`` 一意化（知識構造の見直し 2026-09-12 P0-6）。

背景（S-3 / F-4）: rhetorical_role の ``span_id`` は block ごとに ``span_001`` から
振り直されるため、``legacy_ids = sorted(_claim_legacy_keys({"span_id": span_id}))``
だけだと同じ論文の全 claim 行が ``["claim_span_001", "span_001"]`` になり、
agent ID → DB 行の逆引きが N-way に曖昧だった。

ここでは
  ①同じ ``span_001`` を持つ2 block の span が**異なる** legacy_ids を持つこと
  ②``claim_objects`` 未指定でも ``"{block_id}:{span_id}"`` が入ること
  ③一意に決まらない claim object は**記録しない**（推測しない）こと
を、既存の session モックパターン（test_thesis_context_persistence.py 同型）で
INSERT パラメータを捕捉して検証する。DB も LLM も使わない。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.document_pipeline import persistence  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402


def _span(span_id="span_001", block_id="b1", section_id="sec1", text="claim text"):
    return types.SimpleNamespace(
        span_id=span_id,
        block_id=block_id,
        section_id=section_id,
        text=text,
        reason="",
        confidence=0.9,
        qualification={"decision": "accepted", "claim_type_candidate": "result"},
    )


def _claim_object(claim_id, span_ids, evidence_ids=(), subclaim_ids=()):
    return types.SimpleNamespace(
        claim_id=claim_id,
        source_span_ids=list(span_ids),
        source_evidence_ids=list(evidence_ids),
        subclaim_ids=list(subclaim_ids),
    )


def _claim_objects(*claims):
    return types.SimpleNamespace(claims=list(claims))


def _evidence(*pairs):
    """``(evidence_id, block_id)`` の列から EvidenceRegistryResult 相当を作る。"""
    return types.SimpleNamespace(
        records=[
            types.SimpleNamespace(
                evidence_id=ev_id,
                source=types.SimpleNamespace(block_id=block_id),
            )
            for ev_id, block_id in pairs
        ]
    )


def _run(spans, **kwargs):
    qualified_result = types.SimpleNamespace(qualified_spans=list(spans))
    chunk_index = [
        {"chunk_id": "chunk-1", "block_ids": ["b1"]},
        {"chunk_id": "chunk-2", "block_ids": ["b104"]},
    ]
    session = FakeKnowledgeSession()
    with patch.object(persistence, "_pg_session", return_value=session):
        saved = persistence.persist_qualified_claims(
            document_id="doc-1",
            qualified_result=qualified_result,
            chunk_index=chunk_index,
            **kwargs,
        )
    # 知識オブジェクト層では claim object も1行ずつ入るため、span 由来の行だけを見る
    # （origin='span'。knowledge_objects_design.md §5.4）。
    legacy = [
        set(json.loads(row["source_scope"])["legacy_ids"])
        for row in session.inserted_into("theory_claims")
        if row.get("origin") == "span"
    ]
    return saved, session, legacy


# ---------------------------------------------------------------------------
# claim_span_key / _claim_legacy_keys（純関数）
# ---------------------------------------------------------------------------


def test_claim_span_key_joins_block_and_span():
    assert persistence.claim_span_key("b1", "span_001") == "b1:span_001"


def test_claim_span_key_empty_when_either_side_missing():
    assert persistence.claim_span_key("", "span_001") == ""
    assert persistence.claim_span_key("b1", None) == ""
    assert persistence.claim_span_key(None, None) == ""


def test_claim_legacy_keys_keeps_existing_claim_id_and_span_id_branches():
    keys = persistence._claim_legacy_keys({"claim_id": "u-1", "span_id": "span-007"})
    assert {"u-1", "span-007", "claim_span_007"} <= keys


def test_claim_legacy_keys_adds_unique_key_only_with_block_id():
    without = persistence._claim_legacy_keys({"span_id": "span_001"})
    with_block = persistence._claim_legacy_keys(
        {"span_id": "span_001", "block_id": "b1"}
    )
    assert "b1:span_001" not in without
    assert with_block == without | {"b1:span_001"}


# ---------------------------------------------------------------------------
# ② claim_objects 未指定でも文書内一意キーが入る
# ---------------------------------------------------------------------------


def test_block_qualified_key_present_without_claim_objects():
    _saved, _session, legacy = _run([_span(span_id="span_001", block_id="b1")])
    assert legacy == [{"span_001", "claim_span_001", "b1:span_001"}]


def test_saved_rows_expose_block_id_for_claim_id_map():
    saved, _session, _legacy = _run([_span(span_id="span_001", block_id="b1")])
    assert saved[0]["block_id"] == "b1"
    # orchestrator の claim_id_map はこの dict を _claim_legacy_keys に渡す。
    assert "b1:span_001" in persistence._claim_legacy_keys(saved[0])


# ---------------------------------------------------------------------------
# ① 同じ span_001 を持つ2 block が異なる legacy_ids を持つ
# ---------------------------------------------------------------------------


def test_same_span_id_in_two_blocks_gets_distinct_legacy_ids():
    spans = [
        _span(span_id="span_001", block_id="b1", text="first block claim"),
        _span(span_id="span_001", block_id="b104", text="second block claim"),
    ]
    # builder の採番は「先頭 span が claim_span_001、衝突した2本目が
    # claim_span_001_{counter}」。前者は span_id 由来の派生キーと同じ文字列なので、
    # 区別の検証には後者（と子 claim）を使う。
    claim_objects = _claim_objects(
        _claim_object(
            "claim_span_001", ["span_001"],
            evidence_ids=["ev_0001"], subclaim_ids=["claim_span_001_sub01"],
        ),
        _claim_object("claim_span_001_sub01", ["span_001"], evidence_ids=["ev_0001"]),
        _claim_object("claim_span_001_9", ["span_001"], evidence_ids=["ev_0104"]),
    )
    evidence = _evidence(("ev_0001", "b1"), ("ev_0104", "b104"))

    _saved, _session, legacy = _run(
        spans, claim_objects=claim_objects, evidence_registry=evidence
    )

    assert legacy[0] != legacy[1]
    assert "b1:span_001" in legacy[0] and "b104:span_001" in legacy[1]
    # claim object の ID がそれぞれの block の行にだけ載る。
    assert "claim_span_001_sub01" in legacy[0]
    assert "claim_span_001_sub01" not in legacy[1]
    assert "claim_span_001_9" in legacy[1] and "claim_span_001_9" not in legacy[0]
    # 旧キーは落とさない（P4）。
    assert {"span_001", "claim_span_001"} <= legacy[0]


def test_subclaim_ids_ride_along_with_their_parent_span():
    spans = [_span(span_id="span_001", block_id="b1")]
    claim_objects = _claim_objects(
        _claim_object(
            "claim_span_001", ["span_001"],
            evidence_ids=["ev_0001"],
            subclaim_ids=["claim_span_001_sub01", "claim_span_001_sub02"],
        ),
        _claim_object("claim_span_001_sub01", ["span_001"], evidence_ids=["ev_0001"]),
        _claim_object("claim_span_001_sub02", ["span_001"], evidence_ids=["ev_0001"]),
    )
    _saved, _session, legacy = _run(
        spans,
        claim_objects=claim_objects,
        evidence_registry=_evidence(("ev_0001", "b1")),
    )
    assert {
        "claim_span_001", "claim_span_001_sub01", "claim_span_001_sub02"
    } <= legacy[0]


def test_span_unique_route_works_without_evidence_registry():
    """span_id が1 block にしか現れないなら evidence registry 無しでも結べる。"""
    spans = [_span(span_id="span_042", block_id="b7")]
    claim_objects = _claim_objects(_claim_object("claim_span_042", ["span_042"]))
    _saved, _session, legacy = _run(spans, claim_objects=claim_objects)
    assert {"b7:span_042", "claim_span_042"} <= legacy[0]


# ---------------------------------------------------------------------------
# ③ 曖昧な対応は記録しない（推測しない）
# ---------------------------------------------------------------------------


def test_ambiguous_span_without_evidence_records_no_claim_object_id():
    """同じ span_id が2 block にあり evidence が無ければ、どちらにも載せない。"""
    spans = [
        _span(span_id="span_001", block_id="b1"),
        _span(span_id="span_001", block_id="b104"),
    ]
    claim_objects = _claim_objects(_claim_object("claim_span_001_9", ["span_001"]))
    _saved, _session, legacy = _run(spans, claim_objects=claim_objects)
    for keys in legacy:
        assert "claim_span_001_9" not in keys
    # 一意キー自体は両方に入るので、行の区別は失われない。
    assert legacy[0] != legacy[1]


def test_unknown_evidence_id_is_ignored_and_nothing_is_guessed():
    spans = [_span(span_id="span_001", block_id="b1")]
    claim_objects = _claim_objects(
        _claim_object("claim_span_001", ["span_001"], evidence_ids=["ev_unknown"]),
    )
    # evidence registry に無い evidence_id → block 解決不能。span_id は1 block なので
    # 従経路で解決できる（情報を落とさない）。
    _saved, _session, legacy = _run(
        spans, claim_objects=claim_objects, evidence_registry=_evidence(),
    )
    assert "claim_span_001" in legacy[0]


def test_claim_object_for_a_block_without_a_span_row_is_not_recorded():
    """実在しない (block, span) の組にはキーを作らない。"""
    spans = [_span(span_id="span_001", block_id="b1")]
    claim_objects = _claim_objects(
        _claim_object("claim_other", ["span_777"], evidence_ids=["ev_0001"]),
    )
    _saved, _session, legacy = _run(
        spans,
        claim_objects=claim_objects,
        evidence_registry=_evidence(("ev_0001", "b1")),
    )
    assert "claim_other" not in legacy[0]


def test_index_helper_returns_empty_without_claim_objects():
    qualified = types.SimpleNamespace(qualified_spans=[_span()])
    assert persistence._claim_object_ids_by_span_key(qualified, None, None) == {}
    assert persistence._claim_object_ids_by_span_key(
        qualified, types.SimpleNamespace(claims=[]), None
    ) == {}
