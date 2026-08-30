"""orchestrator → ComponentGraphAgent の claim 供給配線を固定する回帰テスト。

背景: `_stage_component_graph` は長らく `{"claim_id", "text"}` の2キーだけを
ComponentGraphAgent に渡していた。しかし agent 側 `_build_claim_index` は
`evidence_text` / `is_atomic` を読み、normalizer の `_atomic_claim_ids` は
「`is_atomic` が False でなく、かつ `evidence_text` が非空」の claim だけを
強い（atomic）backing として数える（issue #306 / #317）。`ClaimObjectRecord` は
evidence を `source_evidence_ids` の参照でしか持たないため、本文を
EvidenceRegistry から解決せずに渡すと **完璧な atomic claim でも必ず
`missing_atomic_claim` が付き、どのノードも `source_backed` に到達しない**。

単体テストはすべて手組みの claim_index を使っていたため、この配線欠落は
CI をすり抜けていた。本テストは
  ①orchestrator が組む flat_claims の中身（evidence 解決・is_atomic 伝播・
    解決不能時に捏造しないこと）
  ②その flat_claims を実際に `_build_claim_index` → normalizer に通したときの
    backing 判定（#317 の意味論を壊していないこと）
の両方を固定する。DB・LLM・FastAPI には触らない。
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
from episteme_graph.agents.evidence_registry.schema import (  # noqa: E402
    EvidenceRecord,
    EvidenceRegistryResult,
    EvidenceSource,
)


# --------------------------------------------------------------------------- #
# fixtures (本物の Result dataclass を使う。手組み dict は使わない)
# --------------------------------------------------------------------------- #

def _claim(claim_id: str, text: str, evidence_ids: list[str], **kwargs) -> ClaimObjectRecord:
    defaults = dict(
        claim_id=claim_id,
        document_id="doc",
        claim_type="result",
        text=text,
        source_evidence_ids=list(evidence_ids),
        source_span_ids=[],
        concepts=[],
    )
    defaults.update(kwargs)
    return ClaimObjectRecord(**defaults)


def _evidence(evidence_id: str, text: str) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        document_id="doc",
        source=EvidenceSource(page=4, section_id="sec_1", block_id="blk_1"),
        evidence_text=text,
    )


def _ctx(claims: list[ClaimObjectRecord], records: list[EvidenceRecord]):
    """`_component_graph_claims` が読む属性だけを備えた最小 ctx。"""
    return SimpleNamespace(
        claim_objects=ClaimObjectBuildResult(
            document_id="doc", cartridge_id=None, claims=list(claims)
        ),
        evidence=EvidenceRegistryResult(
            document_id="doc", cartridge_id=None, records=list(records)
        ),
    )


# --------------------------------------------------------------------------- #
# ① orchestrator が組む flat_claims
# --------------------------------------------------------------------------- #

def test_flat_claims_resolve_evidence_text_from_registry():
    ctx = _ctx(
        [_claim("claim_1", "second-order moment vanishes", ["ev_1"])],
        [_evidence("ev_1", "the second-order moment vanishes in this regime")],
    )
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["claim_id"] == "claim_1"
    assert entry["text"] == "second-order moment vanishes"
    assert entry["evidence_text"] == "the second-order moment vanishes in this regime"
    assert entry["is_atomic"] is True


def test_flat_claims_take_the_first_non_empty_evidence_text():
    ctx = _ctx(
        [_claim("claim_1", "c", ["ev_missing", "ev_blank", "ev_2"])],
        [_evidence("ev_blank", "   "), _evidence("ev_2", "real quote")],
    )
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == "real quote"


def test_flat_claims_do_not_fabricate_unresolvable_evidence():
    # 解決できない evidence 参照は空のまま（"true" 等のダミーで埋めない）。
    ctx = _ctx([_claim("claim_1", "c", ["ev_unknown"])], [_evidence("ev_1", "x")])
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == ""


def test_flat_claims_forward_is_atomic_but_not_atomicity():
    # `_build_claim_index` reads `is_atomic`; `atomicity` is never read, so it is
    # not forwarded (dead payload key).
    ctx = _ctx(
        [
            _claim("claim_1", "a", ["ev_1"], is_atomic=False, atomicity="split_required"),
            _claim("claim_2", "b", ["ev_1"], is_atomic=True, atomicity="atomic"),
        ],
        [_evidence("ev_1", "quote")],
    )
    first, second = orch._component_graph_claims(ctx)
    assert first["is_atomic"] is False
    assert second["is_atomic"] is True
    assert "atomicity" not in first and "atomicity" not in second


def test_flat_claims_do_not_invent_claim_level():
    # ClaimObjectRecord は claim_level を持たない。無い情報は入れない。
    ctx = _ctx([_claim("claim_1", "a", ["ev_1"])], [_evidence("ev_1", "quote")])
    (entry,) = orch._component_graph_claims(ctx)
    assert "claim_level" not in entry


def test_flat_claims_survive_missing_registry_and_claims():
    # 旧 artifact からの resume 等で evidence / claim_objects が None でも落ちない。
    assert orch._component_graph_claims(SimpleNamespace(claim_objects=None, evidence=None)) == []
    ctx = _ctx([_claim("claim_1", "a", ["ev_1"])], [])
    ctx.evidence = None
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == ""


def test_evidence_text_index_maps_every_evidence_id_to_its_text():
    # claim ごとに registry を線形走査しない（N+2 重ループにしない）ための索引。
    index = orch._evidence_text_index(
        EvidenceRegistryResult(
            document_id="doc",
            cartridge_id=None,
            records=[_evidence("ev_1", "a"), _evidence("ev_2", "b")],
        )
    )
    assert index == {"ev_1": "a", "ev_2": "b"}


def test_evidence_text_index_skips_blank_records():
    # 同じ evidence_id の先頭が空でも、後続の非空レコードを隠さない。
    index = orch._evidence_text_index(
        EvidenceRegistryResult(
            document_id="doc",
            cartridge_id=None,
            records=[
                _evidence("ev_1", "   "),
                _evidence("ev_1", "the real quote"),
                _evidence("ev_2", ""),
            ],
        )
    )
    assert index == {"ev_1": "the real quote"}


# --------------------------------------------------------------------------- #
# ①-b support_status ゲート（弱い/壊れた裏付けを強い backing に昇格させない）
# --------------------------------------------------------------------------- #

def test_review_required_claim_gets_no_evidence_text():
    # evidence_adequacy="broken" → support_status="review_required"。
    # is_atomic=True でも強い backing にしない。
    ctx = _ctx(
        [_claim("claim_1", "a", ["ev_1"], is_atomic=True, support_status="review_required")],
        [_evidence("ev_1", "quote")],
    )
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == ""
    assert entry["claim_id"] == "claim_1" and entry["is_atomic"] is True


def test_weakly_supported_and_non_source_backed_claims_get_no_evidence_text():
    for status in ("partially_source_backed", "derived", "inferred", "external", "unknown"):
        ctx = _ctx(
            [_claim("claim_1", "a", ["ev_1"], is_atomic=True, support_status=status)],
            [_evidence("ev_1", "quote")],
        )
        (entry,) = orch._component_graph_claims(ctx)
        assert entry["evidence_text"] == "", status


def test_unknown_support_status_vocabulary_is_fail_closed():
    ctx = _ctx(
        [_claim("claim_1", "a", ["ev_1"], is_atomic=True, support_status="brand_new_status")],
        [_evidence("ev_1", "quote")],
    )
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == ""


def test_missing_support_status_reads_as_the_record_default():
    # support_status を持たない旧 artifact / 別形の record は
    # ClaimObjectRecord の既定値（source_backed）として読む。
    ctx = _ctx([], [_evidence("ev_1", "quote")])
    ctx.claim_objects = SimpleNamespace(
        claims=[SimpleNamespace(
            claim_id="claim_1", text="a", source_evidence_ids=["ev_1"], is_atomic=True,
        )]
    )
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == "quote"


def test_contradictory_legacy_atomicity_blocks_evidence_text():
    # is_atomic=True でも atomicity が非 atomic に正規化される legacy artifact は
    # 厳しい側のシグナルを採る（"non_atomic" → split_required）。
    ctx = _ctx(
        [_claim("claim_1", "a", ["ev_1"], is_atomic=True, atomicity="non_atomic")],
        [_evidence("ev_1", "quote")],
    )
    (entry,) = orch._component_graph_claims(ctx)
    assert entry["evidence_text"] == ""


def test_warns_when_eligible_claims_resolve_no_evidence_text(caplog):
    # evidence registry の縮退などで enrich が空振りしたら黙らない。
    ctx = _ctx([_claim("claim_1", "a", ["ev_missing"], is_atomic=True)], [])
    with caplog.at_level("WARNING"):
        orch._component_graph_claims(ctx)
    assert any("resolved no evidence text" in r.message for r in caplog.records)


def test_no_warning_when_every_claim_is_gated_out(caplog):
    # 全 claim が review_required なだけなら異常ではない（警告を出さない）。
    ctx = _ctx(
        [_claim("claim_1", "a", ["ev_1"], support_status="review_required")],
        [_evidence("ev_1", "quote")],
    )
    with caplog.at_level("WARNING"):
        orch._component_graph_claims(ctx)
    assert not [r for r in caplog.records if "resolved no evidence text" in r.message]


# --------------------------------------------------------------------------- #
# ② flat_claims → _build_claim_index → normalizer の実配線
# --------------------------------------------------------------------------- #

def _derivations(claim_ids: list[str]) -> DerivationChainResult:
    # 式 backing はあるが evidence link は無いステップ。この条件下では
    # `source_backed` に届くかどうかが atomic claim の有無だけで決まる。
    step = DerivationStep(
        step_id="step_eliminate",
        input_equation_ids=["eq_c"],
        operation="eliminate_second_order_parameter",
        output_equation_ids=["eq_d"],
        review_status="teacher_review_required",
        required_claim_ids=list(claim_ids),
    )
    return DerivationChainResult(
        document_id="doc",
        cartridge_id=None,
        chains=[
            DerivationChainRecord(
                derivation_id="deriv",
                document_id="doc",
                source_section_ids=[],
                steps=[step],
            )
        ],
    )


def _normalize_flat(flat_claims: list[dict], claim_ids: list[str]):
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
        _derivations(claim_ids),
        claim_index=_build_claim_index(flat_claims),
    )


def _normalize_with(ctx, claim_ids: list[str]):
    return _normalize_flat(orch._component_graph_claims(ctx), claim_ids)


def _detail_node(result):
    return next(n for n in result.nodes if n.graph_layer == "equation_detail")


def test_wired_atomic_claim_makes_node_source_backed():
    ctx = _ctx(
        [_claim("claim_1", "second-order moment vanishes", ["ev_1"], is_atomic=True)],
        [_evidence("ev_1", "the second-order moment vanishes")],
    )
    node = _detail_node(_normalize_with(ctx, ["claim_1"]))
    assert node.source_backing_status == "source_backed"
    assert "missing_atomic_claim" not in node.review_reasons
    assert "claim_1" in node.linked_claim_ids


def test_wired_non_atomic_claim_is_not_strong_backing():
    # #317 の意味論: is_atomic=False の claim は evidence があっても強い backing に
    # しない（source_backed に昇格させない）。
    ctx = _ctx(
        [_claim("claim_1", "long compound claim", ["ev_1"], is_atomic=False)],
        [_evidence("ev_1", "quote")],
    )
    node = _detail_node(_normalize_with(ctx, ["claim_1"]))
    assert node.source_backing_status == "partially_source_backed"
    assert "missing_atomic_claim" in node.review_reasons


def test_wired_unresolvable_evidence_keeps_missing_atomic_claim():
    # 解決できない claim は従来どおり missing_atomic_claim（正常な信号）。
    ctx = _ctx([_claim("claim_1", "a", ["ev_unknown"], is_atomic=True)], [])
    node = _detail_node(_normalize_with(ctx, ["claim_1"]))
    assert node.source_backing_status == "partially_source_backed"
    assert "missing_atomic_claim" in node.review_reasons


def test_wired_review_required_claim_does_not_reach_source_backed():
    # 弱い/壊れた裏付けの claim は normalizer まで通しても強い backing にならない
    # （review_reasons が消えない）。
    ctx = _ctx(
        [_claim("claim_1", "a", ["ev_1"], is_atomic=True, support_status="review_required")],
        [_evidence("ev_1", "quote")],
    )
    node = _detail_node(_normalize_with(ctx, ["claim_1"]))
    assert node.source_backing_status == "partially_source_backed"
    assert "missing_atomic_claim" in node.review_reasons


def test_regression_two_key_claims_lose_source_backing():
    # バグ再現ガード: `{"claim_id", "text"}` だけを渡すと（＝修正前の配線）
    # 同じ claim でも source_backed に到達しない。
    ctx = _ctx(
        [_claim("claim_1", "second-order moment vanishes", ["ev_1"], is_atomic=True)],
        [_evidence("ev_1", "the second-order moment vanishes")],
    )
    legacy = [{"claim_id": c.claim_id, "text": c.text} for c in ctx.claim_objects.claims]
    legacy_node = _detail_node(_normalize_flat(legacy, ["claim_1"]))
    assert legacy_node.source_backing_status != "source_backed"
    assert "missing_atomic_claim" in legacy_node.review_reasons
    # 現在の配線では同じ claim が強い backing になる。
    assert _detail_node(_normalize_with(ctx, ["claim_1"])).source_backing_status == "source_backed"
