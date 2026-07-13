"""Issue #447 / #449: グラフ node ラベルと thesis-anchor 強調の回帰チェック。

#447: node の primary label に内部 ID（equation ID / parser node ID / claim ID 等）が
      決して漏れないこと。label ロジックは `lsGraphSemanticLabel` を経由し、
      `lsGraphStripInternalIds` が内部 ID トークンを除去する。
#449: `is_thesis_anchor` フラグを持つ node が常に視覚的に強調されること。強調は ID 文字列
      マッチではなく node 自身のフラグ参照から導かれる。フラグは API/agent schema を
      経由して frontend に伝搬する。

frontend に JS テストハーネスが無いため、原稿スタジオ（Lecture Script Studio。Tier 3-17b で
admin.js から admin-lecture-studio.js へ分離済み）の純粋ヘルパを Node で評価して
ドメイン横断の入力に対する挙動を検証する（Node が無い環境では source-assertion に縮退）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"
STYLES_CSS = ROOT / "frontend" / "public" / "css" / "styles.css"
SCHEMAS = ROOT / "backend" / "api" / "schemas.py"

_SRC = str(ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- #447: 内部 ID がラベルに漏れない ------------------------------------------


class TestIssue447NoInternalIdInLabel:
    def test_admin_js_defines_strip_helpers(self):
        source = _read(ADMIN_LS_JS)
        for fn in (
            "lsGraphLooksLikeInternalId",
            "lsGraphStripInternalIds",
            "lsGraphSemanticLabel",
            "lsGraphOperationSemanticLabel",
        ):
            assert "function " + fn in source, f"{fn} must be defined in admin.js"

    def test_display_label_routes_through_semantic_label(self):
        source = _read(ADMIN_LS_JS)
        # 主たる label entrypoint が semantic label を経由する。
        assert "function lsGraphNodeDisplayLabel" in source
        assert "lsGraphSemanticLabel(node)" in source

    def test_main_stage_shortening_applies_to_all_operation_nodes(self):
        source = _read(ADMIN_LS_JS)
        # 旧実装の main-layer 限定ガードを撤去し、operation node 全般へ拡張したこと。
        assert 'layer !== "main" || ctype !== "TheoryOperationNode"' not in source
        assert "isOperationNode" in source

    @pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
    def test_strip_internal_ids_cross_domain(self):
        # admin.js の純粋ヘルパを抜き出して Node で評価する（ドメイン横断 fixture）。
        source = _read(ADMIN_LS_JS)
        helpers = []
        for marker in ("function lsGraphLooksLikeInternalId", "function lsGraphStripInternalIds"):
            start = source.index(marker)
            # 対応する閉じ "\n  }\n"（2 スペースインデントの関数末尾）まで抽出。
            end = source.index("\n  }\n", start) + len("\n  }\n")
            helpers.append(source[start:end])
        # ドメイン横断ケース: [input, expected]。expected に内部 ID を含まないこと。
        cases = [
            ["Derive result — eq_2_7 → eq_2_9", "Derive result"],  # 物理
            ["Theory basis: eq_1_3", "Theory basis"],
            ["Apply criterion claim_42 → claim_7", "Apply criterion"],
            ["Solve linear system — theory_op_0012", "Solve linear system"],
            ["Estimate treatment effect — eq_4_1", "Estimate treatment effect"],  # 計量経済
            ["measure_4 expression level", "expression level"],  # 生物
            ["Completeness condition", "Completeness condition"],  # ID 無しは不変
        ]
        script = (
            "\n".join(h for h in helpers)
            + "\nconst cases = " + json.dumps(cases) + ";"
            + "\nconst out = cases.map(c => [c[0], c[1], lsGraphStripInternalIds(c[0])]);"
            + "\nprocess.stdout.write(JSON.stringify(out));"
        )
        proc = subprocess.run(
            ["node", "-e", script], capture_output=True, text=True, timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
        results = json.loads(proc.stdout)
        id_markers = ("eq_", "_0", "claim_", "theory_op", "measure_4", "eq.", "eq(")
        for inp, expected, actual in results:
            assert actual == expected, f"{inp!r} -> {actual!r} (expected {expected!r})"
            assert not any(m in actual for m in id_markers), f"internal id leaked: {actual!r}"


# --- #449: thesis-anchor 強調 ---------------------------------------------------


class TestIssue449AnchorEmphasis:
    def test_vis_node_references_anchor_flag(self):
        source = _read(ADMIN_LS_JS)
        # visNode 構築が node.is_thesis_anchor を参照すること（ID マッチに依存しない）。
        assert "node.is_thesis_anchor" in source
        assert "visNode.borderWidth = 4" in source

    def test_legend_explains_anchor_symbol(self):
        source = _read(ADMIN_LS_JS)
        assert "ls-legend-anchor" in source
        css = _read(STYLES_CSS)
        assert ".ls-legend-anchor" in css

    def test_anchor_flag_passes_through_api_schema(self):
        schemas = _read(SCHEMAS)
        start = schemas.index("class ComponentGraphNode")
        end = schemas.index("class ComponentGraphEdge", start)
        node_model = schemas[start:end]
        assert "is_thesis_anchor: bool = False" in node_model

    def test_anchor_flag_on_component_graph_node_dataclass(self):
        from episteme_graph.agents.component_graph.schema import (
            ComponentGraphNode,
            ComponentGraphResult,
        )

        node = ComponentGraphNode(
            component_id="theory_op_0001",
            label="Conclusion",
            component_type="TheoryOperationNode",
            is_thesis_anchor=True,
        )
        assert node.is_thesis_anchor is True
        result = ComponentGraphResult(
            document_id="doc-1",
            graph_schema_version="1",
            cartridge_id=None,
            nodes=[node],
            edges=[],
            review_notes=[],
            confidence=1.0,
        )
        payload = result.to_graph_payload()
        assert payload["nodes"][0]["is_thesis_anchor"] is True
