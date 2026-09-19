"""derivation step ⇄ claim の順序欠陥（鶏と卵）を塞ぐバックフィルの回帰テスト。

背景: パイプラインの並びは `claim_object_builder` → `derivation_chain` →
（フック）equation claim 合成 である。式由来の合成 claim（``synth_claim_*``）は
derivation_chain より**後**に生まれるため agent 自身には決して結べず、散文 claim に
``equation_ids`` が付かない文書では全 step の ``required_claim_ids`` が空のままになり、
理論操作グラフのノードが claim 未接続で出る（restart 時だけ、前回 artifact に
残った合成 claim で偶然結べていた）。

`_hook_equation_claim_synthesis` は合成の後に
`_backfill_derivation_claim_refs` で chains を最終 claim 集合に対して
結び直す。本テストはその契約（新規/resume の両方で結ばれる・冪等・
未解決参照の掃除が canonicalization に委譲され ValidationIssue に残ること・
合成失敗/空合成時に既存参照を壊さないこと・非致命・グラフまでの実配線）を固定する。
DB・LLM・FastAPI には触らない。

なお本バックフィルが回復するのはノードの **claim 接続** であって強い backing では
ない（合成 claim は ``equation_backed`` なので ``missing_atomic_claim`` は残る）。
これは #306 の意図した帰結で、末尾の documented-limitation テストが固定する。
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_SRC_DIR = BACKEND.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from core.document_pipeline import orchestrator as orch  # noqa: E402
from episteme_graph.agents.claim_object_builder.schema import (  # noqa: E402
    ClaimObjectBuildResult,
    ClaimObjectRecord,
)
from episteme_graph.agents.component_assembly.schema import (  # noqa: E402
    ComponentAssemblyResult,
)
from episteme_graph.agents.component_graph.agent import _build_claim_index  # noqa: E402
from episteme_graph.agents.component_graph.normalizer import (  # noqa: E402
    ComponentGraphNormalizer,
)
from episteme_graph.agents.component_graph.schema import (  # noqa: E402
    GRAPH_SCHEMA_VERSION,
    ComponentGraphResult,
)
from episteme_graph.agents.derivation_chain.schema import (  # noqa: E402
    DerivationChainRecord,
    DerivationChainResult,
    DerivationStep,
)
from episteme_graph.agents.equation_semantics.schema import (  # noqa: E402
    DefinedSymbol,
    EquationConfidencePolicy,
    EquationReconstruction,
    EquationRecord,
    EquationSemantics,
    EquationSemanticsResult,
    EquationSourceExtraction,
)
from episteme_graph.agents.evidence_registry.schema import (  # noqa: E402
    EvidenceRecord,
    EvidenceRegistryResult,
    EvidenceSource,
)


# --------------------------------------------------------------------------- #
# fixtures (本物の Result dataclass を使う。手組み dict は使わない)
# --------------------------------------------------------------------------- #

def _equation(eq_id: str, *, role: str = "definition", defined=None, evidence=None):
    src = EquationSourceExtraction(
        raw_text="x",
        latex="x=y",
        plain_text="x=y",
        source_location={"page": 1, "section_id": "doc:sec", "block_id": f"blk_{eq_id}", "bbox": []},
        extraction_source="pdf_text_layer",
        extraction_status="complete",
        needs_math_review=False,
        review_reason=[],
    )
    rec = EquationReconstruction.make_none()
    sem = EquationSemantics(
        equation_type=role,
        secondary_types=[],
        semantic_status="source_backed",
        confidence=0.85,
        reason="",
        defined_symbols=[
            DefinedSymbol(symbol=s, definition_status="defined") for s in (defined or ["S3"])
        ],
        used_symbols=[],
        assumptions=[],
        input_equation_ids=[],
        output_equation_ids=[],
        linked_text_spans=[],
        source_evidence_ids=list(evidence or []),
        linked_claim_ids=[],
        summary="",
        review_flags=[],
    )
    return EquationRecord(
        equation_id=eq_id,
        document_id="doc",
        label=eq_id,
        candidate_trace_ids=[f"eqcand_{eq_id}"],
        source_extraction=src,
        reconstruction=rec,
        semantics=sem,
        confidence_policy=EquationConfidencePolicy.derive(src, rec, sem),
    )


def _equations(records) -> EquationSemanticsResult:
    return EquationSemanticsResult(
        document_id="doc",
        cartridge_id=None,
        equation_candidates=[],
        equations=list(records),
        validation_issues=[],
    )


def _claim(claim_id: str, *, equation_ids=(), evidence_ids=("ev_1",), **kwargs) -> ClaimObjectRecord:
    defaults = dict(
        claim_id=claim_id,
        document_id="doc",
        claim_type="result",
        text=f"prose claim {claim_id}",
        source_evidence_ids=list(evidence_ids),
        source_span_ids=[],
        concepts=[],
        equation_ids=list(equation_ids),
    )
    defaults.update(kwargs)
    return ClaimObjectRecord(**defaults)


def _step(step_id: str, *, outputs: list[str], inputs=("eq_in",), required=()) -> DerivationStep:
    return DerivationStep(
        step_id=step_id,
        input_equation_ids=list(inputs),
        operation="eliminate_second_order_parameter",
        output_equation_ids=list(outputs),
        required_claim_ids=list(required),
        review_status="teacher_review_required",
    )


def _derivations(steps: list[DerivationStep]) -> DerivationChainResult:
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv",
                document_id="doc",
                source_section_ids=[],
                steps=list(steps),
            )
        ],
        validation_issues=[],
    )


def _ctx(*, equations, derivations, claims, evidence=()):
    """フックが読む属性だけを備えた最小 ctx（save_artifact は記録スパイ）。"""
    saved: list[tuple[str, object]] = []
    return SimpleNamespace(
        document_id="doc",
        material_id="mat",
        equations=equations,
        derivations=derivations,
        claim_objects=ClaimObjectBuildResult(
            document_id="doc", cartridge_id=None, claims=list(claims)
        ),
        evidence=EvidenceRegistryResult(
            document_id="doc", cartridge_id=None, records=list(evidence)
        ),
        saved=saved,
        save_artifact=lambda stage, value: saved.append((stage, value)),
    )


def _step_of(ctx, index: int = 0) -> DerivationStep:
    return ctx.derivations.chains[0].steps[index]


def _saved_stages(ctx) -> list[str]:
    return [stage for stage, _ in ctx.saved]


def _synth_claim_ids(ctx) -> list[str]:
    return [
        c.claim_id for c in ctx.claim_objects.claims
        if str(c.claim_id).startswith("synth_claim_")
    ]


# --------------------------------------------------------------------------- #
# ① 新規実行相当（合成の前に derivation が走っている＝claim 参照が空）
# --------------------------------------------------------------------------- #

def test_fresh_run_links_steps_to_the_synthesised_equation_claims():
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"])]),
        claims=[_claim("claim_prose_1")],
    )
    assert _step_of(ctx).required_claim_ids == []

    orch._hook_equation_claim_synthesis(ctx)

    synth_ids = _synth_claim_ids(ctx)
    assert synth_ids, "合成 claim が作られていないと前提が崩れる"
    assert _step_of(ctx).required_claim_ids == sorted(synth_ids)
    assert "derivation_chain" in _saved_stages(ctx)


def test_backfill_adds_only_to_required_claim_ids():
    # 追加先は required_claim_ids のみ。input/output claim 参照は agent 側
    # （_build_claim_chains / system_derivation）の領分なので書き足さない。
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"])]),
        claims=[],
    )
    orch._hook_equation_claim_synthesis(ctx)
    step = _step_of(ctx)
    assert step.required_claim_ids
    assert step.input_claim_ids == [] and step.output_claim_ids == []


def test_step_without_matching_output_equation_stays_unlinked():
    # 出力式に対応する claim が無い step を無理に結ばない（捏造しない）。
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_other"])]),
        claims=[],
    )
    orch._hook_equation_claim_synthesis(ctx)
    assert _step_of(ctx).required_claim_ids == []
    assert "derivation_chain" not in _saved_stages(ctx)


# --------------------------------------------------------------------------- #
# ② restart 相当（artifact 由来の derivations + 合成 claim が既にある）
# --------------------------------------------------------------------------- #

def test_resumed_run_backfills_even_though_synth_claims_already_exist():
    # 前回 run の claim artifact に合成 claim が残っている状態から resume。
    # 合成は冪等に作り直され（同一 ID）、step 参照はそれでも結び直される。
    stale_synth = _claim("synth_claim_0001", equation_ids=["eq_1"], support_status="equation_backed")
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"])]),
        claims=[_claim("claim_prose_1"), stale_synth],
    )
    orch._hook_equation_claim_synthesis(ctx)

    assert _synth_claim_ids(ctx) == ["synth_claim_0001"], "合成 ID が重複・増殖していない"
    assert _step_of(ctx).required_claim_ids == ["synth_claim_0001"]


# --------------------------------------------------------------------------- #
# ③ 冪等性（2回目は書き換えゼロ＝derivation_chain artifact を保存しない）
# --------------------------------------------------------------------------- #

def test_second_pass_is_a_no_op_and_saves_no_derivation_artifact():
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"])]),
        claims=[_claim("claim_prose_1")],
    )
    orch._hook_equation_claim_synthesis(ctx)
    first = list(_step_of(ctx).required_claim_ids)
    assert "derivation_chain" in _saved_stages(ctx)

    ctx.saved.clear()
    orch._hook_equation_claim_synthesis(ctx)

    assert _step_of(ctx).required_claim_ids == first
    assert "derivation_chain" not in _saved_stages(ctx)


def test_backfill_helper_reports_no_change_on_the_second_call():
    claims = ClaimObjectBuildResult(
        document_id="doc",
        cartridge_id=None,
        claims=[_claim("claim_1", equation_ids=["eq_1"])],
    )
    derivations = _derivations([_step("step_001", outputs=["eq_1"])])

    first = orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)
    assert first == {"steps_changed": 1, "refs_added": 1, "refs_dropped": 0}

    second = orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)
    assert second == {"steps_changed": 0, "refs_added": 0, "refs_dropped": 0}


# --------------------------------------------------------------------------- #
# ④ stale 参照の掃除（解決できる既存参照は保持する）
# --------------------------------------------------------------------------- #

def test_dropped_refs_are_recorded_as_validation_issues():
    # silent drop にしない（P4）: canonicalization と同じ rule_id で残す。
    claims = ClaimObjectBuildResult(
        document_id="doc", cartridge_id=None, claims=[_claim("claim_prose_1")],
    )
    derivations = _derivations([_step("step_001", outputs=["eq_1"], required=["synth_claim_999"])])

    orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)

    issues = [i for i in derivations.validation_issues if i.rule_id == "unresolved_claim_ref_dropped"]
    assert issues, "落とした参照が validation_issues に記録されていない"
    assert "synth_claim_999" in issues[0].message


def test_system_level_step_without_output_equations_is_backfilled():
    # chain_type="system_level" の step は output_equation_ids が空になり得る。
    # 出力式だけを見ていると結線が丸ごと漏れる。
    claims = ClaimObjectBuildResult(
        document_id="doc", cartridge_id=None, claims=[_claim("claim_1", equation_ids=["eq_1"])],
    )
    derivations = _derivations([_step("step_sys", outputs=[], inputs=["eq_1", "eq_2"])])

    report = orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)

    assert derivations.chains[0].steps[0].required_claim_ids == ["claim_1"]
    assert report["steps_changed"] == 1 and report["refs_added"] == 1


def test_unsorted_and_duplicated_existing_refs_are_normalised_once():
    # 冪等判定が「集合として同じなら何もしない」に弱体化すると、未ソート・重複が
    # そのまま残る（このアサートが落ちる）。
    claims = ClaimObjectBuildResult(
        document_id="doc",
        cartridge_id=None,
        claims=[_claim("claim_a", equation_ids=["eq_1"]), _claim("claim_b")],
    )
    derivations = _derivations([
        _step("step_001", outputs=["eq_1"], required=["claim_b", "claim_a", "claim_b"]),
    ])

    first = orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)
    assert derivations.chains[0].steps[0].required_claim_ids == ["claim_a", "claim_b"]
    assert first["steps_changed"] == 1 and first["refs_added"] == 0

    second = orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)
    assert second == {"steps_changed": 0, "refs_added": 0, "refs_dropped": 0}


def test_unresolvable_refs_are_dropped_and_resolvable_ones_kept():
    claims = ClaimObjectBuildResult(
        document_id="doc",
        cartridge_id=None,
        # equation link を持たない prose claim（＝index からは引けないが実在する）
        claims=[_claim("claim_prose_1")],
    )
    derivations = _derivations([
        _step("step_001", outputs=["eq_1"], required=["synth_claim_999", "claim_prose_1"]),
    ])

    report = orch._backfill_derivation_claim_refs(derivations=derivations, claim_objects=claims)

    step = derivations.chains[0].steps[0]
    assert step.required_claim_ids == ["claim_prose_1"]
    assert report["refs_dropped"] == 1 and report["refs_added"] == 0


def test_stale_synth_ref_is_replaced_by_the_regenerated_one():
    # 旧版合成の ID を指したままの step が、フック通過後に現行 ID を指す。
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"], required=["synth_claim_0042"])]),
        claims=[],
    )
    orch._hook_equation_claim_synthesis(ctx)
    refs = _step_of(ctx).required_claim_ids
    assert "synth_claim_0042" not in refs
    assert refs == sorted(_synth_claim_ids(ctx))


# --------------------------------------------------------------------------- #
# ④-b 合成が失敗 / 空を返したときに既存の参照・claim を壊さない
# --------------------------------------------------------------------------- #

def _break_synthesizer(monkeypatch):
    import episteme_graph.agents.claim_object_builder.equation_claim_synthesis as ecs

    def _boom(*args, **kwargs):
        raise RuntimeError("synthesizer exploded")

    monkeypatch.setattr(ecs, "synthesize_equation_claims", _boom)


def test_failed_synthesis_leaves_claims_and_derivation_artifact_untouched(monkeypatch):
    # 合成が落ちた run で claim_objects から synth を剥がしたまま先へ進むと、
    # バックフィルが生きた参照を全部「解決不能」として落とし、derivation_chain を
    # 破壊保存してしまう。合成失敗時はバックフィルごとスキップする。
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"], required=["synth_claim_0001"])]),
        claims=[_claim("synth_claim_0001", equation_ids=["eq_1"], support_status="equation_backed")],
    )
    _break_synthesizer(monkeypatch)

    orch._hook_equation_claim_synthesis(ctx)

    assert _synth_claim_ids(ctx) == ["synth_claim_0001"], "合成失敗で claim が消えている"
    assert _step_of(ctx).required_claim_ids == ["synth_claim_0001"]
    assert ctx.saved == [], "artifact を保存してはならない"


def test_empty_synthesis_does_not_strip_existing_synth_claims():
    # 式が無い（＝合成が正常に空を返す）文書で、前回 run の合成 claim を消さない。
    ctx = _ctx(
        equations=_equations([]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"], required=["synth_claim_0001"])]),
        claims=[_claim("synth_claim_0001", equation_ids=["eq_1"], support_status="equation_backed")],
    )
    orch._hook_equation_claim_synthesis(ctx)

    assert _synth_claim_ids(ctx) == ["synth_claim_0001"]
    assert _step_of(ctx).required_claim_ids == ["synth_claim_0001"]
    assert "derivation_chain" not in _saved_stages(ctx)


# --------------------------------------------------------------------------- #
# ⑤ 非致命（derivations 不在・chains 空で落ちない）
# --------------------------------------------------------------------------- #

def test_missing_or_empty_derivations_are_a_silent_no_op():
    empty = {"steps_changed": 0, "refs_added": 0, "refs_dropped": 0}
    claims = ClaimObjectBuildResult(document_id="doc", cartridge_id=None, claims=[])
    assert orch._backfill_derivation_claim_refs(derivations=None, claim_objects=claims) == empty
    assert orch._backfill_derivation_claim_refs(
        derivations=orch._empty_derivation_chain_result("doc", None), claim_objects=claims,
    ) == empty


def test_hook_survives_derivations_none():
    ctx = _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=None,
        claims=[],
    )
    orch._hook_equation_claim_synthesis(ctx)  # 例外にならない
    assert "derivation_chain" not in _saved_stages(ctx)


def test_hook_survives_claim_objects_without_claims():
    ctx = _ctx(
        equations=_equations([]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"])]),
        claims=[],
    )
    orch._hook_equation_claim_synthesis(ctx)
    assert _step_of(ctx).required_claim_ids == []


# --------------------------------------------------------------------------- #
# ⑥ 共通ヘルパー（agent 契約と同じ index を両者が使う）
# --------------------------------------------------------------------------- #

def test_claim_equation_link_index_transposes_equation_refs():
    claims = ClaimObjectBuildResult(
        document_id="doc",
        cartridge_id=None,
        claims=[
            _claim("claim_1", equation_ids=["eq_1", "eq_2"]),
            _claim("claim_2", equation_ids=["eq_1"]),
            _claim("claim_3"),
        ],
    )
    assert orch._claim_equation_link_index(claims) == {
        "eq_1": ["claim_1", "claim_2"],
        "eq_2": ["claim_1"],
    }
    assert orch._claim_equation_link_index(None) == {}


# --------------------------------------------------------------------------- #
# ⑦ エンドツーエンド配線（バックフィル後の derivations → 理論操作グラフ）
# --------------------------------------------------------------------------- #

def _normalize(ctx):
    empty_graph = ComponentGraphResult(
        document_id="doc",
        graph_schema_version=GRAPH_SCHEMA_VERSION,
        cartridge_id=None,
        nodes=[],
        edges=[],
        review_notes=[],
        confidence=0.0,
    )
    empty_components = ComponentAssemblyResult(
        document_id="doc",
        components_version="v1",
        cartridge_id=None,
        components=[],
        assembly_hints=[],
        review_notes=[],
        confidence=0.8,
    )
    return ComponentGraphNormalizer().normalize(
        empty_graph,
        empty_components,
        ctx.derivations,
        claim_index=_build_claim_index(orch._component_graph_claims(ctx)),
    )


def _fresh_run_ctx():
    return _ctx(
        equations=_equations([_equation("eq_1", evidence=["ev_1"])]),
        derivations=_derivations([_step("step_001", outputs=["eq_1"])]),
        claims=[],
        evidence=[EvidenceRecord(
            evidence_id="ev_1",
            document_id="doc",
            source=EvidenceSource(page=4, section_id="sec_1", block_id="blk_1"),
            evidence_text="the second-order moment vanishes",
        )],
    )


def _synthesis_only(ctx):
    """フックのうち合成だけを再現（バックフィル前の状態を作る）。"""
    synthesized = orch._synthesize_equation_claims(
        equations=ctx.equations, derivations=ctx.derivations, claim_objects=ctx.claim_objects,
    )
    if synthesized:
        ctx.claim_objects.claims = list(ctx.claim_objects.claims) + synthesized
    return synthesized


def test_graph_nodes_stay_claim_less_without_the_backfill():
    # バグ再現ガード: 合成だけ（バックフィルなし）だと step 参照が空のままなので
    # グラフのノードは claim 未接続で出る。
    ctx = _fresh_run_ctx()
    assert _synthesis_only(ctx), "合成 claim が作られていないと前提が崩れる"
    result = _normalize(ctx)
    assert result.nodes, "ノードが1つも出ないとテストの前提が崩れる"
    assert all(not n.linked_claim_ids for n in result.nodes)


def test_backfill_restores_claim_links_but_not_atomic_backing():
    """documented limitation: 回復するのは claim 接続だけ（#306 の意図した帰結）。

    合成 claim は ``support_status="equation_backed"`` で strong backing ゲート
    （``source_backed`` のみ）の外なので ``evidence_text`` が供給されず、ノードの
    ``missing_atomic_claim`` は前後で変わらない。
    """
    before_ctx = _fresh_run_ctx()
    _synthesis_only(before_ctx)
    before = {n.component_id: n for n in _normalize(before_ctx).nodes}

    after_ctx = _fresh_run_ctx()
    orch._hook_equation_claim_synthesis(after_ctx)
    after = {n.component_id: n for n in _normalize(after_ctx).nodes}

    assert before.keys() == after.keys()
    changed_links = False
    for node_id, node in after.items():
        assert list(node.review_reasons) == list(before[node_id].review_reasons)
        assert node.source_backing_status == before[node_id].source_backing_status
        assert "missing_atomic_claim" in node.review_reasons
        if list(node.linked_claim_ids) != list(before[node_id].linked_claim_ids):
            changed_links = True
    assert changed_links, "claim 接続が1つも変わっていないとテストの前提が崩れる"


def test_graph_nodes_link_claims_after_the_backfill():
    ctx = _fresh_run_ctx()
    orch._hook_equation_claim_synthesis(ctx)

    result = _normalize(ctx)
    synth_ids = set(_synth_claim_ids(ctx))
    main_nodes = [n for n in result.nodes if n.graph_layer == "main"]
    assert main_nodes, "main ノードが出ないとテストの前提が崩れる"
    for node in main_nodes:
        assert synth_ids & set(node.linked_claim_ids)
    detail_nodes = [n for n in result.nodes if n.graph_layer == "equation_detail"]
    for node in detail_nodes:
        assert synth_ids & set(node.linked_claim_ids)
