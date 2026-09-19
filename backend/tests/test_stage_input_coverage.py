"""入力上限を持つステージの coverage 報告（P0-10 の続き・2026-09-19）。

`test_pipeline_coverage_report.py` が「形式」と「_attach_coverage 一本化」を固定するのに
対し、こちらは **paper_skeleton / claim_qualification / equation_semantics /
thesis_reconstruction の 4 ステージが実際に母集合を報告すること**と、**resume でも
前回 run 由来の印つきで報告が残ること**を固定する。

背景（実測）: これらのステージは先頭 N 件で打ち切っていたうえ coverage を持たず、
resume した run では既存の coverage すら stage_outputs から消えていた
（`upsert_analysis_run` は stage の dict を丸ごと置換する）。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@dataclass
class _Block:
    block_id: str
    page: int
    order: int
    text: str
    block_type: str = "body_paragraph"
    section_id: str | None = None
    confidence: float = 0.9
    bbox: list | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class _Section:
    section_id: str
    title: str
    level: int = 1
    order: int = 0
    page_start: int = 1
    page_end: int | None = None
    parent_section_id: str | None = None


@dataclass
class _Structure:
    document_id: str = "doc-1"
    blocks: list = field(default_factory=list)
    sections: list = field(default_factory=list)


@dataclass
class _Span:
    span_id: str
    text: str = "claim"
    role_labels: list = field(default_factory=lambda: ["relation"])
    is_claim_candidate: bool = True
    is_reject_candidate: bool = False


@dataclass
class _Annotation:
    block_id: str
    section_id: str
    span_annotations: list


@dataclass
class _Roles:
    role_annotations: list = field(default_factory=list)


@dataclass
class _Qualified:
    qualified_spans: list = field(default_factory=list)
    rejected_spans: list = field(default_factory=list)
    deferred_spans: list = field(default_factory=list)
    summary_stats: dict = field(default_factory=dict)


def _ctx(captured: dict, *, resumed: bool, **overrides) -> SimpleNamespace:
    base = dict(
        document_id="doc-1",
        material_id="mat-1",
        cartridge_id=None,
        agent_classes={},
        structure=_Structure(),
        skeleton=None,
        roles=None,
        qualified=None,
        equations=None,
        claim_objects=None,
        thesis=None,
        artifact=lambda stage: {},
        should_use_artifact=lambda stage: resumed,
        report_start=lambda stage, **kwargs: None,
        report_done=lambda stage, payload: captured.__setitem__(stage, payload),
        report_item=lambda *a, **k: None,
        save_artifact=lambda stage, value: None,
        finish_target_stage=lambda stage, payload=None: False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── paper_skeleton ───────────────────────────────────────────────────────────

def _skeleton_structure():
    return _Structure(
        sections=[
            _Section("s1", "Introduction"),
            _Section("s2", "Method"),
            _Section("s_app", "Appendix A"),
        ],
    )


def test_paper_skeleton_reports_section_population(monkeypatch):
    monkeypatch.delenv("PAPER_SKELETON_MAX_SECTIONS", raising=False)
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    ctx = _ctx(
        captured,
        resumed=True,
        structure=_skeleton_structure(),
        artifact=lambda stage: {
            "document_id": "doc-1",
            "skeleton_version": "v1",
            "cartridge_id": None,
            "paper_goal": {},
            "central_question": {},
            "headline_claim": {},
            "supporting_subclaims": [],
            "logical_blocks": [],
            "excluded_regions": [],
            "review_notes": [],
            "confidence": 0.5,
        },
    )
    orchestrator._stage_paper_skeleton(ctx)

    report = captured["paper_skeleton"]["coverage"]
    assert report["unit"] == "sections"
    assert report["population"] == 3          # level-1 節（appendix 含む）
    assert report["processed"] == 2           # appendix は見せない
    assert report["reasons"] == ["appendix_excluded"]
    assert report["details"]["source"] == "artifact"
    # 既存キーは変えない
    assert captured["paper_skeleton"]["total"] == 1


# ── claim_qualification ──────────────────────────────────────────────────────

def _roles_with(n: int) -> _Roles:
    return _Roles(role_annotations=[
        _Annotation(f"b{i}", "sec_1", [_Span(f"s{i}")]) for i in range(n)
    ])


def test_claim_qualification_reports_agent_coverage():
    """agent（input_builder）が出した coverage をそのまま stage payload に移す。"""
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    agent_report = {
        "population": 28, "processed": 6, "truncated": 22,
        "reasons": ["max_spans"], "unit": "spans",
        "details": {"sort_key": "role_annotation_order"},
    }
    ctx = _ctx(
        captured,
        resumed=True,
        roles=_roles_with(28),
        qualified=_Qualified(summary_stats={"coverage": agent_report}),
        artifact=lambda stage: {"document_id": "doc-1"},
    )
    ctx.qualified.summary_stats["coverage"] = agent_report

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orchestrator, "_from_agent_dict", lambda stage, value: ctx.qualified)
        orchestrator._stage_claim_qualification(ctx)

    report = captured["claim_qualification"]["coverage"]
    assert report["population"] == 28
    assert report["processed"] == 6
    assert report["truncated"] == 22
    assert report["reasons"] == ["max_spans"]
    # resume では前回 run 由来と明示する
    assert report["details"]["source"] == "artifact"
    assert report["details"]["sort_key"] == "role_annotation_order"


def test_claim_qualification_derives_coverage_for_old_artifacts():
    """coverage を持たない旧 run でも roles と採否結果から母集合を導く。"""
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    ctx = _ctx(
        captured,
        resumed=True,
        roles=_roles_with(10),
        qualified=_Qualified(
            qualified_spans=[object(), object()],
            rejected_spans=[{}],
            deferred_spans=[],
        ),
        artifact=lambda stage: {"document_id": "doc-1"},
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orchestrator, "_from_agent_dict", lambda stage, value: ctx.qualified)
        orchestrator._stage_claim_qualification(ctx)

    report = captured["claim_qualification"]["coverage"]
    assert report["population"] == 10
    assert report["processed"] == 3
    assert report["truncated"] == 7
    assert report["details"]["source"] == "artifact"


def test_claim_qualification_reports_nothing_when_population_is_unknown():
    """roles が無い run では母集合をでっち上げない。"""
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    ctx = _ctx(
        captured,
        resumed=True,
        roles=None,
        qualified=_Qualified(),
        artifact=lambda stage: {"document_id": "doc-1"},
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orchestrator, "_from_agent_dict", lambda stage, value: ctx.qualified)
        orchestrator._stage_claim_qualification(ctx)
    assert "coverage" not in captured["claim_qualification"]


# ── equation_semantics ───────────────────────────────────────────────────────

def _equation_ctx(captured: dict, blocks: list) -> SimpleNamespace:
    return _ctx(
        captured,
        resumed=True,
        structure=_Structure(blocks=blocks, sections=[_Section("s1", "Formulation")]),
        artifact=lambda stage: {
            "document_id": "doc-1",
            "cartridge_id": None,
            "equations": [],
            "equation_candidates": [],
            "validation_issues": [],
        },
    )


def test_equation_semantics_reports_the_inline_cap(monkeypatch):
    """inline 数式の既定上限（32）に当たったら理由コードで必ず出す。"""
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_EQUATIONS", raising=False)
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS", raising=False)
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    blocks = [_Block("e1", 1, 0, "a = 1 (1)", block_type="equation_block", section_id="s1")]
    blocks += [
        _Block(f"p{i}", 1, i + 1, f"Here N_{i} = {i} holds for the sample.", section_id="s1")
        for i in range(50)
    ]
    orchestrator._stage_equation_semantics(_equation_ctx(captured, blocks))

    report = captured["equation_semantics"]["coverage"]
    assert report["population"] == 51      # display 1 + inline 50
    # 効いた弁だけを書く（全体上限は既定「無し」なので max_equations は並ばない — 2026-09-19 レビュー）。
    assert report["reasons"] == ["max_inline_equations"]


def test_equation_semantics_population_counts_inline_candidates(monkeypatch):
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_EQUATIONS", raising=False)
    monkeypatch.delenv("EQUATION_SEMANTICS_MAX_INLINE_EQUATIONS", raising=False)
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    structure = _Structure(
        blocks=[
            _Block("e1", 1, 0, "a = 1 (1)", block_type="equation_block", section_id="s1"),
            _Block("b1", 1, 1, "Here N = 5 holds for the sample.", section_id="s1"),
        ],
        sections=[_Section("s1", "Formulation")],
    )
    ctx = _ctx(
        captured,
        resumed=True,
        structure=structure,
        artifact=lambda stage: {
            "document_id": "doc-1",
            "cartridge_id": None,
            "equations": [],
            "equation_candidates": [],
            "validation_issues": [],
        },
    )
    orchestrator._stage_equation_semantics(ctx)

    report = captured["equation_semantics"]["coverage"]
    # display 1 + inline 1（かつては式ブロックだけを数えて inline の切り捨てが見えなかった）
    assert report["population"] == 2
    assert report["unit"] == "equation_candidates"
    assert report["details"]["source"] == "artifact"


# ── thesis_reconstruction ────────────────────────────────────────────────────

class _Thesis:
    def to_dict(self):
        return {}


def test_thesis_reconstruction_reports_context_coverage():
    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    qualified = _Qualified(qualified_spans=[SimpleNamespace(
        span_id=f"s{i}", block_id=f"b{i}", section_id="sec_1",
        text=f"claim {i}", role_labels=["relation"],
        qualification={"claim_tier": "paper_supporting",
                       "claim_type_candidate": "relation",
                       "granularity": "good"},
        reason="mock", confidence=0.5,
    ) for i in range(50)])
    skeleton = SimpleNamespace(
        document_id="doc-1", cartridge_id=None,
        logical_blocks=[], excluded_regions=[],
        paper_goal={}, central_question={}, headline_claim={},
    )
    ctx = _ctx(
        captured,
        resumed=True,
        skeleton=skeleton,
        qualified=qualified,
        artifact=lambda stage: {},
    )
    # resume 分岐は _from_agent_dict を通るので、thesis artifact は最小形でよい
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orchestrator, "_from_agent_dict", lambda stage, value: _Thesis())
        orchestrator._stage_thesis_reconstruction(ctx)

    report = captured["thesis_reconstruction"]["coverage"]
    assert report["unit"] == "context_items"
    assert report["population"] == 50
    assert report["processed"] == 32     # _MAX_CLAIMS
    assert report["reasons"] == ["max_claims"]
    assert report["details"]["source"] == "artifact"
