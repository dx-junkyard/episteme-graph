"""理論グラフの根拠リンク解決ロジックの静的ガードレール + 挙動検証。

バグ: `lsGraphResolveRef`（原稿スタジオ, admin-lecture-studio.js）に
`"evidence"` / `"derivation"` kind の解決分岐が無く、graph edge の
`evidence_claim_ids` はパイプライン内部 ID（例: `claim_span_001_13_sub04`）で
`lsState.claimsByChunk` 由来の `claimMap`（DB UUID キー）と一致しないため、
根拠リンクチップが常に「未解決」表示になっていた。

バックエンド `GET /admin/documents/{id}/component-graph` は
`reference_index = {claims, evidence, derivations}` を追加返却する契約
（別エージェントが並行実装）。本テストはフロント側 `lsGraphBuildResolver` /
`lsGraphResolveRef` がこの `reference_index` を取り込んで解決することを検証する。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ADMIN_LS_JS = ROOT / "frontend" / "public" / "js" / "admin-lecture-studio.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestReferenceResolutionSource:
    def test_build_resolver_reads_reference_index(self):
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsGraphBuildResolver(graph)")
        end = src.index("\n  function lsGraphSnippet", start)
        block = src[start:end]
        assert "graph.reference_index" in block
        assert "refClaims" in block
        assert "refEvidence" in block
        assert "refDerivations" in block

    def test_resolve_ref_handles_evidence_and_derivation_kinds(self):
        src = _read(ADMIN_LS_JS)
        start = src.index('// kind: "component" | "equation" | "claim" | "evidence" | "derivation"')
        end = src.index("\n  function lsGraphRefChipHtml", start)
        block = src[start:end]
        assert 'kind === "evidence"' in block
        assert 'kind === "derivation"' in block
        assert "resolver.refEvidence" in block
        assert "resolver.refDerivations" in block

    def test_claim_resolution_falls_back_to_reference_index(self):
        src = _read(ADMIN_LS_JS)
        start = src.index('// kind: "component" | "equation" | "claim" | "evidence" | "derivation"')
        end = src.index("\n  function lsGraphRefChipHtml", start)
        block = src[start:end]
        claim_start = block.index('kind === "claim"')
        claim_end = block.index('} else if (kind === "evidence")', claim_start)
        claim_block = block[claim_start:claim_end]
        # 既存 claimMap で見つからない場合に refClaims へフォールバックすること。
        assert "resolver.refClaims" in claim_block

    def test_no_es6_syntax_introduced(self):
        """開発ルール5: admin-lecture-studio.js は ES5 互換で書くこと。"""
        src = _read(ADMIN_LS_JS)
        start = src.index("function lsGraphBuildResolver(graph)")
        end = src.index("\n  function lsGraphRefChipHtml", start)
        block = src[start:end]
        assert "const " not in block
        assert "let " not in block
        assert "=>" not in block
        assert "`" not in block


# ---------------------------------------------------------------------------
# 実行時の振る舞い（Node が使える環境のみ）
# ---------------------------------------------------------------------------

_EXTRACT = r"""
function extractFrom(src, name){
  const s = src.indexOf("function " + name + "(");
  if (s<0) throw new Error("missing "+name);
  let d=0,st=false;
  for(let j=src.indexOf("{",s);j<src.length;j++){const c=src[j];if(c==="{"){d++;st=true;}else if(c==="}"){d--;if(st&&d===0)return src.slice(s,j+1);}}
  throw new Error("unbalanced "+name);
}
function extractMany(src, names){ return names.map(function(n){ return extractFrom(src,n); }).join("\n"); }
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_reference_resolution_behaviour_node(tmp_path):
    """reference_index 経由で claim(fallback) / evidence / derivation が解決され、
    未知の ID は引き続き未解決として正直に報告されることを検証する。"""
    script = _EXTRACT + r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
var window = {};
var lsState = { claimsByChunk: {} };
function escHtml(s){ return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function lsGraphNodeId(n){ return n && n.id; }
eval(extractMany(src, ["lsGraphBuildResolver","lsGraphSnippet","lsGraphResolveRef","lsGraphRefChipHtml"]));

const graph = {
  nodes: [],
  reference_index: {
    claims: { "claim_span_001_13_sub04": { claim_id: "uuid-1", text: "命題のスニペット本文" } },
    evidence: { "ev_0007": { text: "引用元テキストの抜粋", block_id: "b1" } },
    derivations: { "deriv_3": { label: "式3から式5を導出", kind: "derivation", operation: "derive_result" } }
  }
};
const resolver = lsGraphBuildResolver(graph);

const claimRef = lsGraphResolveRef(resolver, "claim", "claim_span_001_13_sub04");
const evidenceRef = lsGraphResolveRef(resolver, "evidence", "ev_0007");
const derivationRef = lsGraphResolveRef(resolver, "derivation", "deriv_3");
const unknownRef = lsGraphResolveRef(resolver, "evidence", "ev_does_not_exist");

const out = {
  claimResolved: claimRef.resolved,
  claimLabelHasSnippet: claimRef.label.indexOf("命題のスニペット本文") >= 0,
  evidenceResolved: evidenceRef.resolved,
  evidenceLabelHasSnippet: evidenceRef.label.indexOf("引用元テキストの抜粋") >= 0,
  derivationResolved: derivationRef.resolved,
  derivationLabelHasSnippet: derivationRef.label.indexOf("式3から式5を導出") >= 0,
  unknownResolved: unknownRef.resolved,
  unknownChipMarksUnresolved: lsGraphRefChipHtml(unknownRef).indexOf("未解決") >= 0,
  resolvedChipHasNoUnresolvedFlag: lsGraphRefChipHtml(evidenceRef).indexOf("未解決") < 0
};
process.stdout.write(JSON.stringify(out));
"""
    harness = tmp_path / "h.js"
    harness.write_text(script, encoding="utf-8")
    proc = subprocess.run(["node", str(harness), str(ADMIN_LS_JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["claimResolved"], "claim id must resolve via reference_index fallback"
    assert out["claimLabelHasSnippet"]
    assert out["evidenceResolved"], "evidence kind must resolve via reference_index"
    assert out["evidenceLabelHasSnippet"]
    assert out["derivationResolved"], "derivation kind must resolve via reference_index"
    assert out["derivationLabelHasSnippet"]
    assert not out["unknownResolved"], "unknown ids must stay unresolved (no silent fabrication)"
    assert out["unknownChipMarksUnresolved"], "unresolved chip must keep the honest 未解決 flag"
    assert out["resolvedChipHasNoUnresolvedFlag"]
