"""PE1: 製品から分離されていること。"""
from __future__ import annotations

import re

from uxsim.config import REPO_ROOT, UXSIM_ROOT

_FORBIDDEN = re.compile(r"^\s*(from\s+core\.llm\b|import\s+core\.llm\b|from\s+core\s+import\s+llm\b)", re.MULTILINE)


def test_uxsim_does_not_import_product_llm():
    offenders = [str(p.relative_to(UXSIM_ROOT)) for p in UXSIM_ROOT.rglob("*.py")
                 if "runs" not in p.parts and _FORBIDDEN.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_dockerfile_does_not_ship_uxsim():
    dockerfile = REPO_ROOT / "backend" / "Dockerfile"
    assert "uxsim" not in dockerfile.read_text(encoding="utf-8")


def test_product_imports_limited_to_principle_oracle():
    """製品コードの import は審判 B（正本の参照）だけに限る。テストと砂場の起動スクリプトは除く。"""
    pat = re.compile(r"^\s*(from|import)\s+(core|api|services)\b", re.MULTILINE)
    offenders = [str(p.relative_to(UXSIM_ROOT)) for p in UXSIM_ROOT.rglob("*.py")
                 if not ({"tests", "runs", "sandbox"} & set(p.relative_to(UXSIM_ROOT).parts)) and pat.search(p.read_text(encoding="utf-8"))]
    assert offenders == ["oracles/principle.py"]
