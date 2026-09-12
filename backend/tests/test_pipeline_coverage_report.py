"""ガードレール: パイプライン各ステージの「取りこぼしの量」報告（P0-10 / F-18）。

正本: `docs/architecture/knowledge_structure_review_2026-09-12.md` §4 Phase 0 の
P0-10 と付属資料 `A_fidelity.md` の F-18。

守る事:
  (a) orchestrator で ``coverage`` キーを組み立てるのは ``_attach_coverage`` だけ
      （ステージごとに自前 dict を書かない = 形式のばらつきの再発防止）。
  (b) ``_attach_coverage`` の出力は共通形式（``is_coverage_report``）を満たす。
  (c) 形式の正本 ``agents/coverage_report.py`` は stdlib のみに依存する。
  (d) 実際にパイプラインを回したとき、対象ステージの stage payload に
      ``coverage`` が乗る。
"""
from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.guardrail_helpers import assert_module_tree_does_not_import

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ORCHESTRATOR_PATH = (
    ROOT / "backend" / "core" / "document_pipeline" / "orchestrator.py"
)
COVERAGE_MODULE_PATH = SRC / "episteme_graph" / "agents" / "coverage_report.py"

_ATTACH_FN = "_attach_coverage"


def _orchestrator_tree() -> ast.Module:
    return ast.parse(ORCHESTRATOR_PATH.read_text(encoding="utf-8"))


def _enclosing_function_names(tree: ast.Module) -> dict[ast.AST, str]:
    """ノード → それを含む最も内側の関数名（トップレベルは ``"<module>"``）。"""
    owner: dict[ast.AST, str] = {}

    def walk(node: ast.AST, current: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else current
            )
            owner[child] = name
            walk(child, name)

    walk(tree, "<module>")
    return owner


# --- (a) coverage キーの組み立ては _attach_coverage 経由のみ ------------------

def test_coverage_key_is_only_assembled_by_the_shared_helper():
    """``payload["coverage"] = ...`` をステージが直接書かない。"""
    tree = _orchestrator_tree()
    owner = _enclosing_function_names(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = target.slice
            is_coverage_key = (
                (isinstance(key, ast.Constant) and key.value == "coverage")
                or (isinstance(key, ast.Name) and key.id == "COVERAGE_REPORT_KEY")
            )
            if is_coverage_key and owner.get(node, "<module>") != _ATTACH_FN:
                offenders.append(f"line {node.lineno} in {owner.get(node, '<module>')}")
    assert offenders == [], (
        "coverage キーを _attach_coverage 以外で組み立てている: " + ", ".join(offenders)
    )


def test_coverage_dict_literals_are_not_written_by_hand():
    """``{"coverage": {...}}`` のような dict リテラルも禁止（形式が分裂する）。"""
    tree = _orchestrator_tree()
    owner = _enclosing_function_names(tree)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key in node.keys:
            if isinstance(key, ast.Constant) and key.value == "coverage":
                if owner.get(node, "<module>") != _ATTACH_FN:
                    offenders.append(
                        f"line {node.lineno} in {owner.get(node, '<module>')}"
                    )
    assert offenders == [], (
        "coverage の dict リテラルを直接書いている: " + ", ".join(offenders)
    )


def test_build_coverage_report_is_called_only_from_the_shared_helper():
    tree = _orchestrator_tree()
    owner = _enclosing_function_names(tree)
    callers = {
        owner.get(node, "<module>")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_coverage_report"
    }
    assert callers == {_ATTACH_FN}, (
        f"build_coverage_report の呼び出し元は {_ATTACH_FN} だけであるべき: {sorted(callers)}"
    )


def test_orchestrator_imports_the_shared_format_source():
    src = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    assert "from episteme_graph.agents.coverage_report import" in src
    assert "COVERAGE_REPORT_KEY" in src


# --- (b) _attach_coverage の出力は共通形式 -----------------------------------

def test_attach_coverage_output_is_a_valid_report():
    from episteme_graph.agents.coverage_report import COVERAGE_REPORT_KEY, is_coverage_report

    from core.document_pipeline import orchestrator

    payload = {"status": "completed", "figures": 3}
    returned = orchestrator._attach_coverage(
        payload,
        population=10,
        processed=4,
        reasons=["skipped_by_limit", "skipped_by_limit"],
        unit="figures",
        details={"skipped_figure_keys": ["p1_i0"]},
    )
    assert returned is payload, "payload を mutate して返す（既存キーは保持）"
    assert payload["status"] == "completed" and payload["figures"] == 3
    report = payload[COVERAGE_REPORT_KEY]
    assert is_coverage_report(report)
    assert report["truncated"] == 6
    assert report["reasons"] == ["skipped_by_limit"]
    assert report["unit"] == "figures"
    assert report["details"] == {"skipped_figure_keys": ["p1_i0"]}


def test_attach_coverage_never_raises_on_bad_input():
    """報告の組み立てでステージを落とさない（fail-soft）。"""
    from core.document_pipeline import orchestrator

    payload: dict = {}
    orchestrator._attach_coverage(payload, population="x", processed=None)  # type: ignore[arg-type]
    # 例外を投げないこと自体が契約。値が入らない場合があっても既存キーは壊さない。
    assert isinstance(payload, dict)


def test_attach_coverage_does_not_put_counts_into_details():
    """件数は3値で尽くす（details に件数を持たせない）という規約の回帰。"""
    src = ORCHESTRATOR_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    owner = _enclosing_function_names(tree)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        if node.func.id != _ATTACH_FN:
            continue
        for kw in node.keywords:
            if kw.arg != "details":
                continue
            for sub in ast.walk(kw.value):
                if isinstance(sub, ast.Dict):
                    for key in sub.keys:
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            assert not key.value.endswith(("_count", "_total")), (
                                f"details に件数キー {key.value!r} を入れている "
                                f"(line {node.lineno}, {owner.get(node, '<module>')})"
                            )


# --- (c) 形式の正本は stdlib のみ -------------------------------------------

def test_coverage_report_module_imports_stdlib_only():
    assert COVERAGE_MODULE_PATH.exists()
    assert_module_tree_does_not_import(
        COVERAGE_MODULE_PATH.parent,
        ["fastapi", "sqlalchemy", "pydantic", "openai"],
        glob="coverage_report.py",
    )
    tree = ast.parse(COVERAGE_MODULE_PATH.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    assert roots <= {"__future__", "typing"}, f"想定外の import: {sorted(roots)}"


# --- (d) 実パイプラインの stage payload に coverage が乗る -------------------

@dataclass
class _Block:
    block_id: str
    page: int
    order: int
    text: str
    block_type: str = "body_paragraph"
    section_id: str | None = None


@dataclass
class _Section:
    section_id: str
    title: str
    level: int = 1
    order: int = 0
    page_start: int = 1
    page_end: int | None = None


class _MockAgent:
    def __init__(self, result):
        self._result = result

    def run(self, *args, **kwargs):
        return self._result


@pytest.fixture()
def stage_payloads():
    """全 agent をモックしてパイプラインを1回流し、stage → payload を集める。"""
    from core.document_pipeline import orchestrator

    @dataclass
    class _Result:
        document_id: str = "doc"
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        components: list = field(default_factory=list)
        qualified_spans: list = field(default_factory=list)
        equations: list = field(default_factory=list)
        equation_candidates: list = field(default_factory=list)
        sections: list = field(
            default_factory=lambda: [_Section("s1", "Intro", order=1, page_start=1)]
        )
        blocks: list = field(
            default_factory=lambda: [
                _Block("b1", 1, 0, "Hello world body.", section_id="s1"),
                _Block("eq1", 1, 1, "E = mc^2", block_type="equation_block", section_id="s1"),
                _Block("cap1", 1, 2, "Fig. 1 Setup.", block_type="figure_caption", section_id="s1"),
            ]
        )
        review_notes: list = field(default_factory=list)

    @dataclass
    class _CourseMappingResult:
        document_id: str = "doc"
        cartridge_id: str | None = None
        topics: list = field(default_factory=list)
        validation_issues: list = field(default_factory=list)

    @dataclass
    class _ComponentGraphResult:
        document_id: str = "doc"
        graph_schema_version: str = "0.1.0"
        cartridge_id: str | None = None
        nodes: list = field(default_factory=list)
        edges: list = field(default_factory=list)
        review_notes: list = field(default_factory=list)
        confidence: float = 0.9
        validation_issues: list = field(default_factory=list)

        def to_dict(self):
            return {
                "nodes": [], "edges": [], "document_id": self.document_id,
                "graph_schema_version": self.graph_schema_version,
                "cartridge_id": self.cartridge_id,
                "review_notes": self.review_notes,
                "confidence": self.confidence,
                "validation_issues": [],
            }

        def to_graph_payload(self):
            return {"graph_schema_version": "0.1.0", "nodes": [], "edges": []}

    structure_result = _Result()
    agents = {
        "DocumentStructureAgent": _MockAgent(structure_result),
        "PaperSkeletonAgent": _MockAgent(_Result()),
        "RhetoricalRoleAgent": _MockAgent(_Result()),
        "ClaimQualificationAgent": _MockAgent(_Result()),
        "EquationSemanticsAgent": _MockAgent(_Result()),
        "ThesisReconstructionAgent": _MockAgent(_Result()),
        "DSLLinkingAgent": _MockAgent(_Result()),
        "ComponentAssemblyAgent": _MockAgent(_Result()),
        "ComponentGraphAgent": _MockAgent(_ComponentGraphResult()),
        "CourseMappingAgent": _MockAgent(_CourseMappingResult()),
    }

    payloads: dict[str, dict] = {}

    def progress(stage, info):
        if isinstance(info, dict) and info.get("status") == "completed":
            payloads[stage] = dict(info)

    fake_persistence = {
        "persist_source_chunks": MagicMock(return_value=[
            {"chunk_id": "c1", "chunk_index": 0, "section_id": "s1",
             "block_ids": ["b1"], "page_start": 1, "page_end": 1, "text": "Hello"}
        ]),
        "persist_qualified_claims": MagicMock(return_value=[]),
        "persist_components": MagicMock(return_value={}),
        "persist_component_graph": MagicMock(return_value="graph-1"),
        "persist_document_embedding": MagicMock(return_value="emb-1"),
        "upsert_analysis_run": MagicMock(return_value="run-1"),
    }

    with patch.multiple(orchestrator, **fake_persistence):
        orchestrator.run_document_pipeline(
            pdf_bytes=b"%PDF-1.4 fake bytes",
            document_id="doc-coverage",
            material_id="mat-coverage",
            filename="paper.pdf",
            cartridge_id="particle_physics",
            progress_callback=progress,
            agents=agents,
        )
    return payloads


def test_every_reported_coverage_is_a_valid_report(stage_payloads):
    from episteme_graph.agents.coverage_report import is_coverage_report

    reported = {
        stage: payload["coverage"]
        for stage, payload in stage_payloads.items()
        if "coverage" in payload
    }
    assert reported, f"coverage を報告したステージが1つも無い: {sorted(stage_payloads)}"
    for stage, report in reported.items():
        assert is_coverage_report(report), f"{stage}: 共通形式でない coverage: {report}"


def test_figure_table_semantics_reports_caption_coverage(stage_payloads):
    payload = stage_payloads.get("figure_table_semantics")
    assert payload is not None
    report = payload["coverage"]
    assert report["unit"] == "captions"
    # structure の figure_caption 1件が母集合（0件と 1件を取り違えない）。
    assert report["population"] == 1


def test_equation_semantics_reports_equation_block_coverage(stage_payloads):
    payload = stage_payloads.get("equation_semantics")
    assert payload is not None
    report = payload["coverage"]
    assert report["unit"] == "equation_blocks"
    assert report["population"] == 1
    # モック agent は候補を返さないので、見ていない式ブロックが正直に残る。
    assert report["processed"] == 0
    assert report["truncated"] == 1
    assert report["reasons"] == ["max_equations"]


def test_contextual_explanation_reports_element_coverage(stage_payloads):
    payload = stage_payloads.get("contextual_explanation")
    assert payload is not None
    assert "coverage" in payload, "contextual_explanation に coverage が無い"
    assert payload["coverage"]["unit"] == "elements"
    # 既存キーは一切変えない（後方互換）。
    assert "truncated_count" in payload and "elements_considered" in payload


def test_apparatus_semantics_reports_skipped_by_option():
    """analyze_images off は「図が無い」ではなく「見ていない」— 母集合は
    figure_image_extraction の実績から導き、processed=0 + 理由コードで残す。

    （パイプライン一周のフィクスチャは DB 不達で図抽出が縮退し母集合を持てないため、
    ここはステージ関数を直接呼んで skipped_by_option 経路だけを検証する。）
    """
    from types import SimpleNamespace

    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    ctx = SimpleNamespace(
        document_id="doc-1",
        material_id="mat-1",
        cartridge_id=None,
        effective_options={},
        figure_extraction_summary={"figures": 5, "failed": 1},
        apparatus_result=None,
        fig_tbl=None,
        structure=None,
        artifact=lambda stage: None,
        should_use_artifact=lambda stage: False,
        report_start=lambda stage, **kwargs: None,
        report_done=lambda stage, payload: captured.__setitem__(stage, payload),
        save_artifact=lambda stage, value: None,
        finish_target_stage=lambda stage, payload=None: False,
    )

    orchestrator._stage_apparatus_semantics(ctx)

    payload = captured["apparatus_semantics"]
    assert payload.get("skipped_by_option") is True
    report = payload.get("coverage")
    assert report is not None, "analyze_images off でも「見ていない」ことを報告する"
    assert report["unit"] == "figures"
    assert report["population"] == 4  # 抽出 5 - 失敗 1
    assert report["processed"] == 0
    assert report["truncated"] == 4
    assert report["reasons"] == ["skipped_by_option"]


def test_apparatus_semantics_omits_coverage_when_population_is_unknown():
    """図抽出が走らなかった run（`figures` キー不在）は母集合を推測しない。"""
    from types import SimpleNamespace

    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    ctx = SimpleNamespace(
        document_id="doc-1",
        material_id="mat-1",
        cartridge_id=None,
        effective_options={},
        figure_extraction_summary={"skipped": True, "reason": "not_pdf"},
        apparatus_result=None,
        fig_tbl=None,
        structure=None,
        artifact=lambda stage: None,
        should_use_artifact=lambda stage: False,
        report_start=lambda stage, **kwargs: None,
        report_done=lambda stage, payload: captured.__setitem__(stage, payload),
        save_artifact=lambda stage, value: None,
        finish_target_stage=lambda stage, payload=None: False,
    )

    orchestrator._stage_apparatus_semantics(ctx)

    assert "coverage" not in captured["apparatus_semantics"]


def test_resumed_stage_does_not_get_a_freshly_computed_coverage():
    """resume で artifact を再利用したステージは今回の母集合を捏造しない
    （前回 payload をそのまま報告する = 既存の resume 挙動と同じ）。"""
    from types import SimpleNamespace

    from core.document_pipeline import orchestrator

    captured: dict[str, dict] = {}
    ctx = SimpleNamespace(
        document_id="doc-1",
        material_id="mat-1",
        cartridge_id=None,
        structure=SimpleNamespace(blocks=[_Block("eq1", 1, 0, "x=1", block_type="equation_block")]),
        skeleton=None,
        roles=None,
        equations=None,
        agent_classes={},
        artifact=lambda stage: {
            "document_id": "doc-1",
            "cartridge_id": None,
            "equations": [],
            "equation_candidates": [],
            "validation_issues": [],
        },
        should_use_artifact=lambda stage: True,
        report_start=lambda stage, **kwargs: None,
        report_done=lambda stage, payload: captured.__setitem__(stage, payload),
        save_artifact=lambda stage, value: None,
        finish_target_stage=lambda stage, payload=None: False,
    )

    orchestrator._stage_equation_semantics(ctx)

    assert "coverage" not in captured["equation_semantics"]
