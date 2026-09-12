"""学ぶ単位の stable_key（learning_units_design.md §5・LU4）。

Phase 1 の :mod:`core.knowledge_objects.stable_key` と同じ性質を固定する:

- 決定論（同じ材料 → 同じキー）・参照集合の順序に依存しない
- 出現順（``order_index``）・run_id・confidence を材料にしない
- 種別が違えば別のキーになる
- 版接頭辞 ``k1:`` を持つ

DB にも LLM にも接続しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.knowledge_objects import stable_key as ko_keys  # noqa: E402
from core.knowledge_objects.schema import (  # noqa: E402
    LEARNING_UNIT_KINDS,
    STABLE_KEY_VERSION_PREFIX,
)


def _key(**kwargs):
    params = {
        "document_id": "doc-1",
        "unit_kind": "section_block",
        "text": "Linearised perturbation equations",
        "refs": ["b2", "b1"],
    }
    params.update(kwargs)
    return ko_keys.learning_unit_stable_key(
        params["document_id"], params["unit_kind"], params["text"], params["refs"]
    )


class TestDeterminism:
    def test_same_material_gives_the_same_key(self):
        assert _key() == _key()

    def test_key_has_the_version_prefix(self):
        assert _key().startswith(STABLE_KEY_VERSION_PREFIX)

    def test_ref_order_does_not_matter(self):
        assert _key(refs=["b1", "b2"]) == _key(refs=["b2", "b1"])

    def test_duplicate_and_blank_refs_are_ignored(self):
        assert _key(refs=["b1", "b2", "b1", "", None]) == _key(refs=["b2", "b1"])

    def test_text_normalisation_absorbs_whitespace(self):
        """正規化は agent 側 ``content_hash`` と同じ実装（新しい正規化を書かない）。"""
        assert _key(text="  Linearised   perturbation  equations ") == _key()


class TestDiscrimination:
    def test_kind_changes_the_key(self):
        keys = {kind: _key(unit_kind=kind) for kind in LEARNING_UNIT_KINDS}
        assert len(set(keys.values())) == len(LEARNING_UNIT_KINDS)

    def test_document_changes_the_key(self):
        assert _key(document_id="doc-2") != _key()

    def test_text_changes_the_key(self):
        assert _key(text="Something else entirely") != _key()

    def test_refs_change_the_key(self):
        assert _key(refs=["b1"]) != _key()

    def test_other_knowledge_kinds_do_not_collide(self):
        """種別接頭辞があるので claim / component のキーとは衝突しない。"""
        unit = ko_keys.learning_unit_stable_key("doc-1", "section_block", "text", ["b1"])
        claim = ko_keys.claim_stable_key("doc-1", "text", ["b1"])
        component = ko_keys.component_stable_key("doc-1", "text", "section_block", ["b1"])
        assert len({unit, claim, component}) == 3
