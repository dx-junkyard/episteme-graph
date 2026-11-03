"""SB層 適性判定（第 22 周 A3）— 損失・目的関数の組み立ては「数値で動かす理論」に数えない。決定論・分野語なし。"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from core.simulation_bridge import schema, suitability  # noqa: E402

SOURCE = BACKEND_DIR / "core" / "simulation_bridge" / "suitability.py"


def _eq(latex, eq_type, summary):
    return {"latex": latex, "plain_text": "", "agent_payload": {"semantics": {"equation_type": eq_type, "summary": summary}}}


LOSS = [
    _eq("\\mathcal{L} = \\lambda_d \\mathcal{L}_d + \\lambda_t \\mathcal{L}_t", "definition", "Defines the total training loss as a weighted sum."),
    _eq("\\mathcal{L}_t = \\frac{1}{N}\\sum_i \\|r_i\\|^2", "definition", "Defines the residual loss as the mean squared residual."),
    _eq("N = 64", "definition", "Defines the number of points."),
]
PHYSICS = [
    _eq("F = m a", "relation", "Relates force to mass and acceleration."),
    _eq("a = \\frac{dv}{dt}", "definition", "Defines acceleration."),
]
LAGRANGIAN = [
    _eq("\\mathcal{L}_2 = G_2(\\phi, X)", "definition", "Defines the second Lagrangian density of the theory."),
    _eq("E = \\partial_\\phi \\mathcal{L}_2", "relation", "Relates the field equation to the Lagrangian."),
]


def test_loss_assembly_is_not_simulable():
    result = suitability.assess_module({}, LOSS)
    assert result == {"simulable": False, "note": schema.FACT_NOT_SIMULABLE}


def test_argmin_marks_procedure():
    assert suitability.is_procedure_equation(_eq("\\hat\\theta = \\arg\\min_\\theta J(\\theta)", "definition", ""))


def test_physical_relation_is_simulable():
    assert suitability.assess_module({}, PHYSICS) == {"simulable": True, "note": ""}


def test_lagrangian_calligraphic_l_is_not_mistaken_for_loss():
    assert suitability.assess_module({}, LAGRANGIAN)["simulable"] is True


def test_no_material_means_no_judgement():
    assert suitability.assess_module({}, []) == {"simulable": None, "note": ""}
    assert suitability.assess_module({}, [{"latex": "", "plain_text": ""}]) == {"simulable": None, "note": ""}
    no_types = [{"latex": "y = a x", "plain_text": "", "agent_payload": {}}]
    assert suitability.assess_module({}, no_types) == {"simulable": None, "note": ""}


def test_unknown_or_definition_only_is_not_judged():
    defs = [_eq("x = 1", "definition", "Defines x."), _eq("y = 2", "unknown", "")]
    assert suitability.assess_module({}, defs)["simulable"] is None


def test_minority_procedure_is_not_judged_true():
    mixed = [*PHYSICS, _eq("\\mathcal{L} = \\sum r^2", "definition", "Defines the loss function.")]
    assert suitability.assess_module({}, mixed)["simulable"] is None


def test_never_raises():
    assert suitability.assess_module(None, None) == {"simulable": None, "note": ""}
    assert suitability.assess_module({}, [{"latex": "x", "agent_payload": "{not json"}])["simulable"] is None


def test_fact_has_no_numbers():
    assert not any(ch.isdigit() for ch in schema.FACT_NOT_SIMULABLE)


def test_pure_module_imports():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert not any(n.startswith(("fastapi", "sqlalchemy", "core.llm")) for n in names)


def test_no_domain_vocabulary_in_rules():
    text = SOURCE.read_text(encoding="utf-8").lower()
    for word in ("neutron", "tov", "horndeski", "cosmolog", "gravit", "pulsar", "eos"):
        assert word not in text
