"""概念辞書の分野スコープと照合コスト（P3-R10）。

``core/library/concept_dictionary.py`` の 2 点だけを見る（辞書の材料そのものと接地の
導出は ``test_claim_concept_grounding_dictionary.py`` が正本）:

1. レジストリの読み出しは、分野が引けるときは**当該分野 + ``unassigned``** に絞る。
   引けなければ従来どおり全件（分野を推測で埋めない）。
2. 本文照合の走査は**相異なる表記の数**だけ（エントリ数 × 表記数ではない）。
   出力の順序・内容は絞り込み前と同じ。

併せて V-6（学習者向け ``claim.concepts`` の読み時遮断）の共通述語
``core/learner_context_common.py::is_symbol_like_concept`` / ``visible_concept_names``
も固定する（判定表を新設せず既存 2 正本へ委譲していること）。

DB にも LLM にも触らない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.library import concept_dictionary as cd  # noqa: E402


def _entry(entry_id: str, name: str, domain_key: str, aliases=()) -> dict:
    return {
        "id": entry_id,
        "domain_key": domain_key,
        "entry_type": "concept",
        "name": name,
        "aliases": list(aliases),
    }


class _FakeStore:
    """``list_entries`` の呼ばれ方を記録する fake。"""

    def __init__(self, by_domain: dict[str, list[dict]]):
        self.by_domain = by_domain
        self.calls: list[dict] = []

    def list_entries(self, **kwargs):
        self.calls.append(dict(kwargs))
        domain = kwargs.get("domain_key")
        if domain is None:
            return [e for rows in self.by_domain.values() for e in rows]
        return list(self.by_domain.get(domain, []))


def _install(monkeypatch, store, labels=None):
    monkeypatch.setattr(cd, "library_store", store)
    monkeypatch.setattr(
        cd.registry, "labels_for_entries", lambda ids, **kwargs: dict(labels or {})
    )
    monkeypatch.setattr(cd, "cartridge_ontology_for", lambda cartridge_id: None)


# ---------------------------------------------------------------------------
# 1. 分野スコープ（P3-R10）
# ---------------------------------------------------------------------------


class TestDomainScope:
    def test_domain_narrows_the_registry_to_that_domain_and_unassigned(self, monkeypatch):
        store = _FakeStore(
            {
                "astro": [_entry("e1", "Cosmic web", "astro")],
                "unassigned": [_entry("e2", "Form factor", "unassigned")],
                "flavour": [_entry("e3", "Zero recoil limit", "flavour")],
            }
        )
        _install(monkeypatch, store)
        dictionary = cd.build_concept_dictionary(cartridge_id="astro")
        names = sorted(item["canonical"] for item in dictionary.entries.values())
        assert names == ["Cosmic web", "Form factor"]
        assert [c.get("domain_key") for c in store.calls] == ["astro", "unassigned"]

    def test_no_domain_reads_every_entry(self, monkeypatch):
        store = _FakeStore(
            {
                "astro": [_entry("e1", "Cosmic web", "astro")],
                "flavour": [_entry("e3", "Zero recoil limit", "flavour")],
            }
        )
        _install(monkeypatch, store)
        dictionary = cd.build_concept_dictionary(cartridge_id="")
        assert len(dictionary) == 2
        assert store.calls == [{"include_candidates": False}]

    def test_explicit_domain_key_wins_over_cartridge_id(self, monkeypatch):
        """``corpus.document_domain_keys`` から引けた分野を明示で渡せる。"""
        store = _FakeStore({"flavour": [_entry("e3", "Zero recoil limit", "flavour")]})
        _install(monkeypatch, store)
        dictionary = cd.build_concept_dictionary(cartridge_id="", domain_key="flavour")
        assert len(dictionary) == 1
        assert [c.get("domain_key") for c in store.calls] == ["flavour", "unassigned"]

    def test_same_entry_in_both_buckets_is_not_duplicated(self, monkeypatch):
        row = _entry("e1", "Cosmic web", "astro")
        store = _FakeStore({"astro": [row], "unassigned": [row]})
        _install(monkeypatch, store)
        dictionary = cd.build_concept_dictionary(cartridge_id="astro")
        assert len(dictionary) == 1

    def test_registry_failure_still_yields_a_dictionary(self, monkeypatch):
        class _Boom:
            def list_entries(self, **kwargs):
                raise RuntimeError("db down")

        _install(monkeypatch, _Boom())
        assert len(cd.build_concept_dictionary(cartridge_id="astro")) == 0


# ---------------------------------------------------------------------------
# 2. 照合コスト（P3-R10）
# ---------------------------------------------------------------------------


class TestMatchCost:
    def _dictionary(self) -> cd.ConceptDictionary:
        dictionary = cd.ConceptDictionary()
        # 3 エントリが同じ別名「cosmic web」を共有する。
        for entry_id, name in (("e1", "Cosmic web"), ("e2", "Large scale structure")):
            dictionary.add(
                name,
                source=cd.SOURCE_REGISTRY_LABEL,
                entry_id=entry_id,
                aliases=["cosmic web"],
            )
        return dictionary

    def test_distinct_surfaces_deduplicates(self):
        surfaces = self._dictionary().distinct_surfaces()
        assert surfaces == ["Cosmic web", "cosmic web", "Large scale structure"]

    def test_body_is_scanned_once_per_distinct_surface(self, monkeypatch):
        dictionary = self._dictionary()
        scanned: list[str] = []
        real = cd.text_mentions_alias

        def _counting(body, surface):
            scanned.append(surface)
            return real(body, surface)

        monkeypatch.setattr(cd, "text_mentions_alias", _counting)
        found = dictionary.match("The cosmic web is the large scale structure of matter.")
        assert len(scanned) == len(set(scanned)) == len(dictionary.distinct_surfaces())
        # 出力は従来どおりエントリの登録順・各エントリの**最初の**一致表記
        # （照合は大文字小文字を畳むので代表名がそのまま当たる）。
        assert found == [
            ("cosmic web", "Cosmic web"),
            ("large scale structure", "Large scale structure"),
        ]

    def test_empty_text_scans_nothing(self, monkeypatch):
        scanned: list[str] = []
        monkeypatch.setattr(
            cd, "text_mentions_alias", lambda b, s: scanned.append(s) or False
        )
        assert self._dictionary().match("") == []
        assert scanned == []

    def test_word_boundary_is_still_enforced(self):
        """P0-2 / F-7: 部分文字列一致に退行しない。"""
        dictionary = cd.ConceptDictionary()
        dictionary.add("SM", source=cd.SOURCE_REGISTRY_LABEL, entry_id="e1")
        assert dictionary.match("cosmological constant") == []


# ---------------------------------------------------------------------------
# 3. 学習者向け concepts の読み時遮断（V-6）
# ---------------------------------------------------------------------------


class TestLearnerConceptGate:
    """CG フックより前に走った run の ``theory_claims.concepts`` は生の数式記号のまま。

    既存行の接地バックフィルとは独立に、**読み時**に学習者へ式片が「概念」として
    並ばないようにする。行は消さない（P4）— 出さないだけで DB の値も
    symbol_registry からの辿りも失われない。判定表は新設せず、既存の 2 正本
    （P0-3 の ``is_symbol_like_concept_name`` と ``looks_like_tex_math``）を重ねる。
    """

    @staticmethod
    def _gate():
        from core.learner_context_common import is_symbol_like_concept

        return is_symbol_like_concept

    def test_symbolic_names_from_the_scratch_db_are_excluded(self):
        gate = self._gate()
        for name in (
            r"E_{L/R}(z,t)",
            r"F_3(t,{\bm{k}}_1,\ldots)",
            r"0.62 \lesssim ? \lesssim 1.41",  # 先頭が \ でも {}$ も無い式片
            r"\lambda",
            "R_D",
            "",
        ):
            assert gate(name) is True, name

    def test_real_concept_names_survive(self):
        gate = self._gate()
        for name in ("cosmic web", "zero recoil limit", "重力", "dark energy equation of state"):
            assert gate(name) is False, name

    def test_visible_concept_names_keeps_order_and_drops_duplicates(self):
        from core.learner_context_common import visible_concept_names

        assert visible_concept_names(
            [
                {"name": "cosmic web"},
                {"name": "R_D"},
                "zero recoil limit",
                "cosmic web",
                {"name": r"0.62 \lesssim ? \lesssim 1.41"},
                None,
            ]
        ) == ["cosmic web", "zero recoil limit"]

    def test_internal_ids_are_blocked_too(self):
        from core.learner_context_common import visible_concept_names

        assert visible_concept_names([{"name": "comp_003"}, {"name": "cosmic web"}]) == [
            "cosmic web"
        ]

    def test_no_second_symbol_table_is_introduced(self):
        """判定表を作らない（P0-3 の正本へ委譲していること）。"""
        from tests.guardrail_helpers import extract_function_source

        src = (BACKEND / "core" / "learner_context_common.py").read_text(encoding="utf-8")
        body = extract_function_source(src, "is_symbol_like_concept")
        delegate = extract_function_source(src, "_symbol_like_concept_name")
        assert "is_symbol_like_concept_name" in delegate
        assert "_symbol_like_concept_name(" in body
        assert "looks_like_tex_math(" in body
        # 自前の記号集合・長さ閾値を書かない。
        for marker in ("frozenset(", 'r"\\\\', "MIN_CONCEPT_NAME_LENGTH ="):
            assert marker not in body, marker

    def test_both_learner_facing_modules_re_export_the_gate(self):
        """``component_context`` / ``element_context`` の双方から使える（片方だけにしない）。"""
        from core import component_context, element_context

        for module in (component_context, element_context):
            assert hasattr(module, "visible_concept_names"), module.__name__
            assert hasattr(module, "_is_symbol_like_concept"), module.__name__
