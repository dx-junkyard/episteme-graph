"""知識オブジェクトの永続化契約（knowledge_objects_design.md §5.4 / KO3・KO4）。

検査する契約:
  ①``persist_qualified_claims`` は DELETE を発行せず、stable_key 一致で UUID を保つ
  ②claim object（親 / atomic 子 / 式由来合成）も ``origin`` 付きで1行ずつ保存される
  ③親子は2パスで ``parent_claim_id`` に解決される
  ④語彙外の型は丸め、自称は ``*_text`` に残す（KO7）
  ⑤``persist_knowledge_objects`` が equation / evidence / derivation step / symbol を
    専用テーブルへ同期し、素材の無い種別だけを正直にスキップする
DB も LLM も使わない。
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
from core.knowledge_objects import stable_key as ko_keys  # noqa: E402
from tests.knowledge_object_fakes import FakeKnowledgeSession  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _span(span_id="span_001", block_id="b1", text="A span claim", tier="paper_core"):
    return types.SimpleNamespace(
        span_id=span_id, block_id=block_id, section_id="sec1", text=text,
        reason="", confidence=0.8,
        qualification={"decision": "accepted", "claim_type_candidate": "result",
                       "tier": tier},
    )


def _claim_object(claim_id, *, text="A span claim", normalized_text="",
                  claim_type="result", evidence_ids=("ev_1",), span_ids=("span_001",),
                  parent_claim_id=None, synthesis_method="", subclaim_ids=(),
                  content_hash="", equation_ids=()):
    return types.SimpleNamespace(
        claim_id=claim_id, document_id="doc-1", claim_type=claim_type, text=text,
        normalized_text=normalized_text, source_evidence_ids=list(evidence_ids),
        source_span_ids=list(span_ids), subclaim_ids=list(subclaim_ids),
        parent_claim_id=parent_claim_id, synthesis_method=synthesis_method,
        concepts=[], equation_ids=list(equation_ids), support_status="source_backed",
        section_id="sec1", content_hash=content_hash,
    )


def _evidence_registry(*pairs):
    return types.SimpleNamespace(records=[
        types.SimpleNamespace(
            evidence_id=ev_id, document_id="doc-1",
            source=types.SimpleNamespace(
                block_id=block_id, section_id="sec1", page=1,
                span_start=0, span_end=5,
            ),
            evidence_text=f"quote for {ev_id}",
            evidence_role="source_quote",
            public_export_policy="location_only",
            parent_evidence_id=None,
        )
        for ev_id, block_id in pairs
    ])


def _run_claims(spans, session=None, **kwargs):
    session = session or FakeKnowledgeSession(id_prefix="claim")
    qualified = types.SimpleNamespace(qualified_spans=list(spans))
    with patch.object(persistence, "_pg_session", return_value=session):
        saved = persistence.persist_qualified_claims(
            document_id="doc-1",
            qualified_result=qualified,
            chunk_index=[{"chunk_id": "chunk-1", "block_ids": ["b1"]}],
            **kwargs,
        )
    return saved, session


def _claim_rows(session):
    return session.inserted_into("theory_claims")


# ---------------------------------------------------------------------------
# ① DELETE しない / stable_key 一致で UUID を保つ
# ---------------------------------------------------------------------------


def test_persist_claims_never_deletes():
    _saved, session = _run_claims([_span()])
    assert not any("DELETE FROM theory_claims" in sql for sql in session.sql)


def test_matching_stable_key_keeps_the_same_uuid_and_supersedes_the_rest():
    span = _span()
    key = ko_keys.claim_stable_key("doc-1", span.text, ["b1"])
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": key, "agent_id": "b1:span_001",
         "review_status": "teacher_approved", "created_by": None},
        {"id": "stale-uuid", "stable_key": "k1:gone", "agent_id": "b1:span_777",
         "review_status": "teacher_review_required", "created_by": None},
    ])
    saved, session = _run_claims([span], session=session, run_id="run-9")
    assert saved[0]["claim_id"] == "kept-uuid"
    assert _claim_rows(session) == []
    assert session.superseded == ["stale-uuid"]


def test_review_status_is_not_overwritten_on_match():
    span = _span()
    key = ko_keys.claim_stable_key("doc-1", span.text, ["b1"])
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": key, "agent_id": "b1:span_001",
         "review_status": "teacher_approved", "created_by": None},
    ])
    _saved, session = _run_claims([span], session=session)
    updated = [v for table, _w, v in session.updates if table == "theory_claims"]
    assert updated and all("review_status" not in values for values in updated)


def test_touched_claim_text_is_not_overwritten_on_match():
    """教員がレビューした claim の本文は、同じキーの再解析で書き換えない（§5.3 / P1-R4）。"""
    span = _span()
    key = ko_keys.claim_stable_key("doc-1", span.text, ["b1"])
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": key, "agent_id": "b1:span_001",
         "review_status": "teacher_approved", "created_by": None},
    ])
    _saved, session = _run_claims([span], session=session)
    updated = [v for table, _w, v in session.updates if table == "theory_claims"]
    assert updated
    assert all("text" not in values for values in updated)
    assert all("normalized_text" not in values for values in updated)


def test_untouched_claim_text_is_updated_on_match():
    span = _span()
    key = ko_keys.claim_stable_key("doc-1", span.text, ["b1"])
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": key, "agent_id": "b1:span_001",
         "review_status": "teacher_review_required", "created_by": None},
    ])
    _saved, session = _run_claims([span], session=session)
    updated = [v for table, _w, v in session.updates if table == "theory_claims"]
    assert any("text" in values for values in updated)


def test_approximate_backfill_key_is_reconciled_by_the_claim_text():
    """近似キーの旧行を supersede + 新規 INSERT に割らない（§5.6 / P1-R3）。"""
    span = _span()
    session = FakeKnowledgeSession(live_rows=[
        {"id": "kept-uuid", "stable_key": "k1:approximate", "agent_id": "b1:span_old",
         "review_status": "teacher_approved", "created_by": None,
         "normalized_text": span.text},
    ])
    saved, session = _run_claims([span], session=session, run_id="run-9")
    assert saved[0]["claim_id"] == "kept-uuid"
    assert _claim_rows(session) == []
    assert session.superseded == []
    remap_rows = session.inserted_into("element_id_remap")
    assert any(row.get("old_id") == "k1:approximate" for row in remap_rows)


# ---------------------------------------------------------------------------
# ② claim object も1行ずつ / origin 付き
# ---------------------------------------------------------------------------


def test_claim_objects_are_persisted_with_their_origin():
    claim_objects = types.SimpleNamespace(claims=[
        _claim_object("claim_span_001", subclaim_ids=["claim_span_001_sub01"]),
        _claim_object("claim_span_001_sub01", text="An atomic child",
                      parent_claim_id="claim_span_001"),
        _claim_object("synth_claim_1", text="Equation derived statement",
                      synthesis_method="equation_semantics", evidence_ids=()),
    ])
    _saved, session = _run_claims(
        [_span(text="A different span text")],
        claim_objects=claim_objects,
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    origins = {row["agent_claim_id"]: row["origin"] for row in _claim_rows(session)}
    assert origins["claim_span_001"] == "claim_object"
    assert origins["claim_span_001_sub01"] == "atomic_rewrite"
    assert origins["synth_claim_1"] == "equation_synthesis"
    assert origins["b1:span_001"] == "span"


def test_saved_rows_cover_every_agent_claim_id():
    """orchestrator の claim_id_map が全 claim の agent ID を覆えること（§5.4-4）。"""
    claim_objects = types.SimpleNamespace(claims=[
        _claim_object("claim_span_001"),
        _claim_object("claim_span_001_sub01", text="child",
                      parent_claim_id="claim_span_001"),
    ])
    saved, _session = _run_claims(
        [_span(text="A different span text")],
        claim_objects=claim_objects,
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    agent_ids = {row["agent_claim_id"] for row in saved}
    assert {"claim_span_001", "claim_span_001_sub01", "b1:span_001"} <= agent_ids
    keys = set()
    for row in saved:
        keys |= set(persistence._claim_legacy_keys(row)) | set(row["legacy_ids"])
    assert {"claim_span_001", "claim_span_001_sub01", "b1:span_001", "span_001"} <= keys


def test_span_sharing_a_claim_objects_key_is_merged_not_duplicated():
    """同じ命題を span 行と claim object 行の2本に分けない（§5.4-2）。"""
    claim_objects = types.SimpleNamespace(claims=[_claim_object("claim_span_001")])
    _saved, session = _run_claims(
        [_span(text="A span claim")],
        claim_objects=claim_objects,
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    rows = _claim_rows(session)
    assert len(rows) == 1
    scope = json.loads(rows[0]["source_scope"])
    # span 側の突合キーは claim object の行に合流する（解決を落とさない）。
    assert {"claim_span_001", "span_001", "b1:span_001"} <= set(scope["legacy_ids"])


# ---------------------------------------------------------------------------
# ③ 親子の2パス解決
# ---------------------------------------------------------------------------


def test_parent_claim_id_is_resolved_in_a_second_pass():
    claim_objects = types.SimpleNamespace(claims=[
        _claim_object("claim_span_001"),
        _claim_object("claim_span_001_sub01", text="child",
                      parent_claim_id="claim_span_001"),
    ])
    _saved, session = _run_claims(
        [_span(text="A different span text")],
        claim_objects=claim_objects,
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    parent_updates = [
        values for table, _w, values in session.updates
        if table == "theory_claims" and "parent_claim_id" in values
    ]
    assert len(parent_updates) == 1
    assert parent_updates[0]["parent_claim_id"] == "claim-1"


# ---------------------------------------------------------------------------
# ④ 型語彙（KO7）と階層
# ---------------------------------------------------------------------------


def test_unknown_claim_type_is_rounded_and_the_self_report_is_kept():
    claim_objects = types.SimpleNamespace(claims=[
        _claim_object("claim_x", text="odd", claim_type="not_a_known_type"),
    ])
    _saved, session = _run_claims(
        [], claim_objects=claim_objects,
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    row = _claim_rows(session)[0]
    assert row["claim_type"] == "unknown"
    assert row["claim_type_text"] == "not_a_known_type"


def test_claim_tier_comes_from_the_span_qualification():
    _saved, session = _run_claims([_span(tier="paper_supporting")])
    assert _claim_rows(session)[0]["claim_tier"] == "paper_supporting"


def test_unknown_claim_tier_is_dropped_not_invented():
    _saved, session = _run_claims([_span(tier="not_a_tier")])
    assert _claim_rows(session)[0]["claim_tier"] == ""


def test_claim_tier_reads_the_artifact_key_claim_tier():
    """実 artifact のキーは ``claim_tier``（``tier`` だけを見て全件空だった = V-3）。"""
    span = _span()
    span.qualification = {
        "decision": "accepted", "claim_type_candidate": "result",
        "claim_tier": "paper_core",
    }
    _saved, session = _run_claims([span])
    assert _claim_rows(session)[0]["claim_tier"] == "paper_core"


def test_equation_stable_keys_are_recorded_next_to_the_agent_ids():
    equations = types.SimpleNamespace(equations=[
        types.SimpleNamespace(
            equation_id="eq_1", label="(1)", content_hash="h",
            source_extraction=types.SimpleNamespace(
                raw_text="E = mc^2", latex="E = mc^2", plain_text="E = mc^2",
                source_location={"block_id": "b1", "section_id": "sec1", "page": 1},
                extraction_status="complete", needs_math_review=False,
            ),
            reconstruction=types.SimpleNamespace(latex=None, plain_text=None),
            semantics=types.SimpleNamespace(
                equation_type="definition", semantic_status="source_backed",
                defined_symbols=[], used_symbols=[], input_equation_ids=[],
                output_equation_ids=[], linked_claim_ids=[], source_evidence_ids=[],
            ),
        ),
    ])
    claim_objects = types.SimpleNamespace(claims=[
        _claim_object("claim_eq", text="uses eq", equation_ids=("eq_1",)),
    ])
    _saved, session = _run_claims(
        [], claim_objects=claim_objects, equations=equations,
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    equation = json.loads(_claim_rows(session)[0]["equation"])
    assert equation["equation_ids"] == ["eq_1"]
    assert equation["equation_stable_keys"][0].startswith("k1:")


# ---------------------------------------------------------------------------
# ⑤ persist_knowledge_objects
# ---------------------------------------------------------------------------


def _knowledge_session(**kwargs):
    session = FakeKnowledgeSession(id_prefix="ko")
    with patch.object(persistence, "_pg_session", return_value=session):
        summary = persistence.persist_knowledge_objects(
            document_id="doc-1", run_id="run-1", **kwargs
        )
    return summary, session


def test_missing_material_is_skipped_per_kind_not_treated_as_empty():
    summary, session = _knowledge_session(
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    assert set(summary["skipped"]) == {"equations", "derivation_steps", "symbols"}
    assert summary["evidence"]["inserted"] == 1
    assert session.inserted_into("knowledge_equations") == []


def test_evidence_rows_carry_location_and_agent_payload():
    _summary, session = _knowledge_session(
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    row = session.inserted_into("knowledge_evidence")[0]
    assert row["agent_evidence_id"] == "ev_1"
    assert row["block_id"] == "b1"
    assert row["evidence_text"] == "quote for ev_1"
    assert json.loads(row["agent_payload"])["evidence_id"] == "ev_1"
    assert row["stable_key"].startswith("k1:")


def test_derivation_steps_use_equation_stable_keys_as_key_material():
    equations = types.SimpleNamespace(equations=[
        types.SimpleNamespace(
            equation_id="eq_1", label="(1)", content_hash="",
            source_extraction=types.SimpleNamespace(
                raw_text="a=b", latex="a=b", plain_text="a=b",
                source_location={"block_id": "b1", "section_id": "sec1", "page": 1},
                needs_math_review=False,
            ),
            reconstruction=types.SimpleNamespace(latex=None, plain_text=None),
            semantics=types.SimpleNamespace(
                equation_type="relation", semantic_status="source_backed",
                defined_symbols=[], used_symbols=[], input_equation_ids=[],
                output_equation_ids=[], linked_claim_ids=[], source_evidence_ids=[],
            ),
        ),
    ])
    derivations = types.SimpleNamespace(chains=[
        types.SimpleNamespace(
            derivation_id="der_1", document_id="doc-1", chain_type="equation_chain",
            teaching_takeaway="", source_section_ids=[],
            steps=[types.SimpleNamespace(
                step_id="step_1", operation="linearize_field",
                operation_subtype=None, input_equation_ids=["eq_1"],
                output_equation_ids=[], input_claim_ids=[], output_claim_ids=[],
                required_claim_ids=[], assumption_ids=[], source_evidence_ids=[],
            )],
        ),
    ])
    _summary, session = _knowledge_session(equations=equations, derivations=derivations)
    step = session.inserted_into("knowledge_derivation_steps")[0]
    # agent ID はチェーン ID で文書内一意にする（V-2）。
    assert step["agent_step_id"] == "der_1:step_1"
    assert step["agent_derivation_id"] == "der_1"
    assert step["operation"] == "linearize_field"
    equation_key = session.inserted_into("knowledge_equations")[0]["stable_key"]
    assert step["stable_key"] == ko_keys.derivation_step_stable_key(
        "doc-1", "linearize_field", [equation_key], [],
        derivation_id="der_1", step_index=0,
    )


def test_same_step_id_in_two_chains_does_not_collide():
    """``step_001`` はチェーン内でしか一意でない（V-2 の回帰）。"""
    def _chain(derivation_id):
        return types.SimpleNamespace(
            derivation_id=derivation_id, document_id="doc-1", chain_type="equation_chain",
            teaching_takeaway="", source_section_ids=[],
            steps=[types.SimpleNamespace(
                step_id="step_001", operation="linearize_field",
                operation_subtype=None, input_equation_ids=[],
                output_equation_ids=[], input_claim_ids=[], output_claim_ids=[],
                required_claim_ids=[], assumption_ids=[], source_evidence_ids=[],
            )],
        )

    derivations = types.SimpleNamespace(chains=[_chain("der_1"), _chain("der_2")])
    _summary, session = _knowledge_session(derivations=derivations)
    rows = session.inserted_into("knowledge_derivation_steps")
    assert len(rows) == 2
    assert {row["agent_step_id"] for row in rows} == {"der_1:step_001", "der_2:step_001"}
    keys = [row["stable_key"] for row in rows]
    assert len(set(keys)) == 2, "別チェーンの同名 step が同じ stable_key に潰れない"
    assert not any("#" in key for key in keys), "素キーで分かれる（#n に頼らない）"


def test_system_level_chain_without_steps_still_gets_a_row():
    derivations = types.SimpleNamespace(chains=[
        types.SimpleNamespace(
            derivation_id="der_sys", document_id="doc-1", chain_type="system_level",
            teaching_takeaway="", source_section_ids=[], steps=[],
            system_id="sys_1", operation="solve_system", operation_family="solve",
            operation_subtype=None, input_equation_ids=["eq_1"],
            output_equation_ids=[], input_claim_ids=[], output_claim_ids=[],
            assumption_ids=[], source_evidence_ids=[],
        ),
    ])
    _summary, session = _knowledge_session(derivations=derivations)
    rows = session.inserted_into("knowledge_derivation_steps")
    assert len(rows) == 1
    assert rows[0]["agent_step_id"] == "der_sys:sys_1"
    assert rows[0]["chain_type"] == "system_level"


def test_evidence_and_symbol_rows_do_not_write_review_status():
    """078 は evidence / symbol に review_status 列を作らない（V-1 の回帰）。"""
    _summary, session = _knowledge_session(
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    for row in session.inserted_into("knowledge_evidence"):
        assert "review_status" not in row
    for _table, _where, values in session.updates:
        assert "review_status" not in values


def test_symbols_are_keyed_by_canonical_symbol_and_scope():
    symbols = types.SimpleNamespace(records=[
        types.SimpleNamespace(
            symbol_id="sym_1", document_id="doc-1", canonical_symbol="alpha",
            notation_variants=["a"], kind="parameter", unit=None,
            scope="document", defining_equation_ids=[], used_in_equation_ids=[],
            source_evidence_ids=[], definition_status="defined",
            definition_evidence_texts=["alpha is ..."], domain_constraints=[],
            review_reasons=[], maturity_source="deterministic", confidence=0.4, reason="",
        ),
    ])
    _summary, session = _knowledge_session(symbol_registry=symbols)
    row = session.inserted_into("knowledge_symbols")[0]
    assert row["canonical_symbol"] == "alpha"
    assert row["stable_key"] == ko_keys.symbol_stable_key("doc-1", "alpha", "document", [])
    # confidence は列に昇格させず agent_payload の中にだけ残す（原則4）。
    assert "confidence" not in row
    assert json.loads(row["agent_payload"])["confidence"] == 0.4


def test_knowledge_sync_never_deletes():
    _summary, session = _knowledge_session(
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    assert not any(sql.strip().upper().startswith("DELETE") for sql in session.sql)


def test_audit_row_is_recorded_for_the_run():
    _summary, session = _knowledge_session(
        evidence_registry=_evidence_registry(("ev_1", "b1")),
    )
    audit = session.inserted_into("theory_review_events")[0]
    assert audit["entity_type"] == "knowledge_object"
    assert audit["entity_id"] == "doc-1"
    assert audit["new_status"] == "synced"
    assert json.loads(audit["metadata"])["run_id"] == "run-1"


def _equation_record(equation_id, *, label, latex, block_id="b1"):
    return types.SimpleNamespace(
        equation_id=equation_id, label=label, content_hash="",
        source_extraction=types.SimpleNamespace(
            raw_text=latex, latex=latex, plain_text=latex,
            source_location={"block_id": block_id, "section_id": "sec1", "page": 1},
            needs_math_review=False,
        ),
        reconstruction=types.SimpleNamespace(latex=None, plain_text=None),
        semantics=types.SimpleNamespace(
            equation_type="definition", semantic_status="source_backed",
            defined_symbols=[], used_symbols=[], input_equation_ids=[],
            output_equation_ids=[], linked_claim_ids=[], source_evidence_ids=[],
        ),
    )


def test_same_equation_id_in_two_blocks_does_not_collide():
    """式 ID は印字番号由来（``eq_7``）で文書内一意ではない（2026-09-17 の回帰）。

    同じ ID の2件が同じ stable_key で INSERT されると
    ``uq_knowledge_equations_stable_key_live`` に当たって解析全体が落ちていた。
    """
    equations = types.SimpleNamespace(equations=[
        _equation_record("eq_7", label="7", latex="a=b", block_id="b1"),
        _equation_record("eq_7", label="7", latex="c=d", block_id="b2"),
    ])
    summary, session = _knowledge_session(equations=equations)
    rows = session.inserted_into("knowledge_equations")
    assert len(rows) == 2
    assert summary["equations"]["inserted"] == 2
    keys = [row["stable_key"] for row in rows]
    assert len(set(keys)) == 2, keys
    # 行ごとのキーは **その行自身の内容**から引く（片方の内容で両方を名乗らせない）。
    assert keys[0] == ko_keys.equation_stable_key("doc-1", "a=b", "a=b", "b1", "7")
    assert keys[1] == ko_keys.equation_stable_key("doc-1", "c=d", "c=d", "b2", "7")


def test_identical_duplicate_equation_records_are_renumbered():
    """内容まで同じ重複レコードでも ``#2`` で行を分ける（情報を落とさない）。"""
    equations = types.SimpleNamespace(equations=[
        _equation_record("eq_7", label="7", latex="a=b"),
        _equation_record("eq_7", label="7", latex="a=b"),
    ])
    _summary, session = _knowledge_session(equations=equations)
    keys = [row["stable_key"] for row in session.inserted_into("knowledge_equations")]
    base = ko_keys.equation_stable_key("doc-1", "a=b", "a=b", "b1", "7")
    assert keys == [base, f"{base}#2"]


def test_duplicate_equation_id_references_resolve_to_the_first_record():
    """曖昧な ID の参照は先頭レコード（素のキーを持つ行）に着地させる。"""
    equations = types.SimpleNamespace(equations=[
        _equation_record("eq_7", label="7", latex="a=b", block_id="b1"),
        _equation_record("eq_7", label="7", latex="c=d", block_id="b2"),
    ])
    keys = persistence._equation_stable_key_map("doc-1", equations)
    assert keys["eq_7"] == ko_keys.equation_stable_key("doc-1", "a=b", "a=b", "b1", "7")
