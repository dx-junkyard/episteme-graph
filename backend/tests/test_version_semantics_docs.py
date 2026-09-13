"""版の語彙（PROV-O 2 系統）の宣言が索引表に揃っていることを固定するガードレール。

正本: docs/features/knowledge_transfer_design.md §7（P4-4）と
docs/architecture/layer_registry.md §4「版の語彙」。P4-4 は**コード変更ゼロ**の宣言だが、
「版」を持つ構造が増えたときに宣言だけが欠ける（X-12 の再発）のを機械検査で止める。

検査の材料は backend/db/*.sql の実ファイル（版・supersede・pin を担う列名の出現）で、
その表・列が §4 の表に**名前として**現れることを要求する。語彙は revision / alternate の
2 語だけで、第 3 の語を作らない。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LAYER_REGISTRY = ROOT / "docs" / "architecture" / "layer_registry.md"
DESIGN = ROOT / "docs" / "features" / "knowledge_transfer_design.md"
DB_DIR = ROOT / "backend" / "db"


def _section4() -> str:
    text = LAYER_REGISTRY.read_text(encoding="utf-8")
    start = text.index("## 4. 版の語彙")
    return text[start:]


#: 「版」または「同一性（alternate）」を担う構造。migration の実ファイルに列名 / 表名が
#: 実在することと、§4 の表にその名前が現れることの両方を要求する。
VERSION_BEARING_STRUCTURES: dict[str, str] = {
    # 表・列名（§4 に現れるべき名前） : 実在を確認する migration 側のトークン
    "shared_versions": "shared_versions",
    "version_no": "version_no",
    "atlas_skeletons": "atlas_skeletons",
    "id_migrations": "id_migrations",
    "library_entry_versions": "library_entry_versions",
    "superseded_by_run_id": "superseded_by_run_id",
    "produced_by_run_id": "produced_by_run_id",
    "document_analysis_runs": "document_analysis_runs",
    "active_analysis_run_id": "active_analysis_run_id",
    "element_id_remap": "element_id_remap",
    "element_identity_links": "element_identity_links",
    "library_entry_relations": "library_entry_relations",
    "library_atlas_node_links": "library_atlas_node_links",
    "atlas_anchor_aliases": "atlas_anchor_aliases",
    "landscape_placements": "landscape_placements",
    "element_explanations": "element_explanations",
}


class TestVersionVocabularyDeclared:
    def test_section_exists_with_two_terms_only(self):
        sec = _section4()
        assert "prov:wasRevisionOf" in sec and "prov:alternateOf" in sec
        # 第 3 の語（例: prov:specializationOf / wasDerivedFrom を「版」の語として）を
        # 表の語彙列に持ち込まない。語彙列の値は revision / alternate（+ 対応表の注記）のみ。
        rows = [
            line for line in sec.splitlines()
            if line.startswith("| `") and line.count("|") >= 5
        ]
        assert rows, "§4 の表が空"
        for row in rows:
            vocab_cell = row.split("|")[2].strip().replace("*", "")
            assert vocab_cell.startswith(("revision", "alternate")), row

    def test_every_version_bearing_structure_is_declared(self):
        sec = _section4()
        sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(DB_DIR.glob("*.sql")))
        missing_in_db = [name for name, token in VERSION_BEARING_STRUCTURES.items() if token not in sql]
        assert missing_in_db == [], f"migration に実在しない構造名がテスト表にある: {missing_in_db}"
        missing = [name for name in VERSION_BEARING_STRUCTURES if name not in sec]
        assert missing == [], (
            f"layer_registry.md §4「版の語彙」に宣言の無い構造: {missing}"
            "（版・同一性を持つ構造を足したら §4 の表に 1 行足す）"
        )

    def test_alternate_structures_are_not_called_revision(self):
        """同一性リンク系（alternate）を revision と書かない（KN-2 / 原則7）。"""
        sec = _section4()
        for name in ("element_identity_links", "library_entry_relations", "library_atlas_node_links", "atlas_anchor_aliases"):
            row = next(line for line in sec.splitlines() if line.startswith("| `") and name in line.split("|")[1])
            vocab_cell = row.split("|")[2].replace("*", "").strip()
            assert vocab_cell.startswith("alternate"), row

    def test_design_doc_points_to_the_registry_section(self):
        text = DESIGN.read_text(encoding="utf-8")
        assert "## 7. P4-4 版の語彙" in text
        assert "layer_registry.md" in text
        assert re.search(r"prov:wasRevisionOf", text) and re.search(r"prov:alternateOf", text)
