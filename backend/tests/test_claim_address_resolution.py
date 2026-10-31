"""教材の主張の宛先（UUID / block 付き / 素の番号）の解決（focus_document_design.md §追補「主張の宛先」）。

rhetorical_role の ``span_id`` は block ごとに振り直されるため、素の ``claim_span_007`` は
1論文の中でも複数の主張に当たる。読み手（``core.element_context``）は3形を受けてこの順に
狭め、素の番号が論文内で曖昧なら推測せず事実文を返す。書き手（``course_content_builder``）は
新しい freeze で論文内で一意な宛先を書く。
"""

from __future__ import annotations

import pytest

from core import course_content_builder as ccb
from core import element_context

_DOC_A = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_DOC_B = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_U1 = "11111111-1111-1111-1111-111111111111"
_U2 = "22222222-2222-2222-2222-222222222222"
_U3 = "33333333-3333-3333-3333-333333333333"


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return [dict(r) for r in self._rows]


class _ClaimFake:
    """2本のクエリ（素の ID / block 付き）を params で見分け、意味論を Python で再現する。"""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, _stmt, params):
        doc_ids = set(params.get("doc_ids") or [])
        raw = str(params.get("address") or params.get("raw_id") or "")
        out = []
        for row in self.rows:
            if row["document_id"] not in doc_ids:
                continue
            legacy = row.get("legacy_ids") or []
            if "block_id" in params:
                exact = raw in legacy
                by_block = row.get("block_id") == params["block_id"] and (
                    params["local_id"] in legacy or row.get("span_id") == params["local_id"]
                )
                if exact or by_block:
                    out.append({
                        "id": row["id"], "document_id": row["document_id"],
                        "parent_claim_id": row.get("parent_claim_id"),
                        "origin": row.get("origin"), "exact_key": exact,
                    })
            else:
                uuid_id = params.get("uuid_id")
                if raw in legacy or (uuid_id and row["id"] == uuid_id):
                    out.append({
                        "id": row["id"], "document_id": row["document_id"],
                        "id_match": row["id"] == raw,
                        "parent_claim_id": row.get("parent_claim_id"),
                    })
        out.sort(key=lambda r: (not r.get("id_match", False), r["document_id"], r["id"]))
        if "block_id" not in params:
            out = out[: element_context._CLAIM_CANDIDATE_LIMIT]
        return _Result(out)

    def close(self):
        pass


def _row(id_, block, *, legacy=("claim_span_007",), doc=_DOC_A, origin="claim_object", span="span_007", parent=None):
    return {
        "id": id_, "document_id": doc, "legacy_ids": list(legacy), "block_id": block,
        "span_id": span, "origin": origin, "parent_claim_id": parent,
    }


@pytest.fixture
def two_same_numbers(monkeypatch):
    # 同じ論文の別 block に span_007 が2つ（claim_object 2行 + span 行1行）。
    rows = [
        _row(_U1, "blk_a"),
        _row(_U2, "blk_b"),
        _row(_U3, "blk_b", legacy=("span_007", "claim_span_007", "blk_b:span_007"), origin="span"),
    ]
    monkeypatch.setattr(element_context, "get_session", lambda: _ClaimFake(rows))
    return rows


class TestAddressForm:
    @pytest.mark.parametrize("value,form", [
        (_U1, "uuid"),
        ("blk_a:span_007", "block_qualified"),
        ("blk_a:claim_span_007", "block_qualified"),
        ("claim:blk_a:span_007", "block_qualified"),
        ("claim_span_007", "bare"),
        ("", "bare"),
    ])
    def test_forms(self, value, form):
        assert element_context.claim_address_form(value) == form


class TestResolveThreeForms:
    def test_uuid_resolves(self, two_same_numbers):
        assert element_context._resolve_claim(_U2, {_DOC_A}) == (_U2, _DOC_A)

    def test_block_qualified_agent_id_resolves_to_claim_object(self, two_same_numbers):
        # blk_b では span 行と claim_object 行の両方が当たる → 主張オブジェクトの行。
        assert element_context._resolve_claim("blk_b:claim_span_007", {_DOC_A}) == (_U2, _DOC_A)
        assert element_context._resolve_claim("blk_a:claim_span_007", {_DOC_A}) == (_U1, _DOC_A)

    def test_p06_span_key_resolves_to_exact_key_row(self, two_same_numbers):
        assert element_context._resolve_claim("blk_b:span_007", {_DOC_A}) == (_U3, _DOC_A)
        assert element_context._resolve_claim("claim:blk_b:span_007", {_DOC_A}) == (_U3, _DOC_A)

    def test_block_qualified_unknown_block_is_none(self, two_same_numbers):
        assert element_context._resolve_claim("blk_z:claim_span_007", {_DOC_A}) is None

    def test_bare_unique_resolves(self, monkeypatch):
        rows = [_row(_U1, "blk_a")]
        monkeypatch.setattr(element_context, "get_session", lambda: _ClaimFake(rows))
        assert element_context._resolve_claim("claim_span_007", {_DOC_A}) == (_U1, _DOC_A)

    def test_bare_ambiguous_within_document_is_flagged(self, two_same_numbers):
        hit, ambiguous = element_context._resolve_claim_detail("claim_span_007", {_DOC_A})
        assert hit is None and ambiguous is True

    def test_bare_ambiguous_across_documents_is_not_within_document(self, monkeypatch):
        rows = [_row(_U1, "blk_a"), _row(_U2, "blk_a", doc=_DOC_B)]
        monkeypatch.setattr(element_context, "get_session", lambda: _ClaimFake(rows))
        hit, ambiguous = element_context._resolve_claim_detail("claim_span_007", {_DOC_A, _DOC_B})
        assert hit is None and ambiguous is False


class TestBuildElementContextAmbiguityFact:
    def test_bare_ambiguous_returns_fact_not_none(self, two_same_numbers, monkeypatch):
        def _boom(_ref):  # pragma: no cover
            raise AssertionError("must not build a lens for an ambiguous number")

        monkeypatch.setattr(element_context.context_lens_mod, "build", _boom)
        out = element_context.build_element_context("claim", "claim_span_007", {_DOC_A})
        assert out == {
            "available": False,
            "note": element_context.NOTE_AMBIGUOUS_CLAIM_NUMBER,
            "reason": element_context.REASON_AMBIGUOUS_CLAIM_NUMBER,
        }

    def test_ambiguous_in_focus_does_not_fall_through_to_other_paper(self, monkeypatch):
        # 焦点の論文 A で曖昧、論文 B には一意 → B の主張を推測で見せない。
        rows = [_row(_U1, "blk_a"), _row(_U2, "blk_b"), _row(_U3, "blk_a", doc=_DOC_B)]
        monkeypatch.setattr(element_context, "get_session", lambda: _ClaimFake(rows))
        monkeypatch.setattr(
            element_context.context_lens_mod, "build",
            lambda _ref: (_ for _ in ()).throw(AssertionError("no lens")),
        )
        out = element_context.build_element_context(
            "claim", "claim_span_007", {_DOC_A, _DOC_B}, preferred_document_ids={_DOC_A}
        )
        assert out["reason"] == element_context.REASON_AMBIGUOUS_CLAIM_NUMBER

    def test_cross_document_ambiguity_without_focus_stays_none(self, monkeypatch):
        rows = [_row(_U1, "blk_a"), _row(_U2, "blk_a", doc=_DOC_B)]
        monkeypatch.setattr(element_context, "get_session", lambda: _ClaimFake(rows))
        assert element_context.build_element_context("claim", "claim_span_007", {_DOC_A, _DOC_B}) is None

    def test_fact_has_no_numbers(self):
        assert not any(ch.isdigit() for ch in element_context.NOTE_AMBIGUOUS_CLAIM_NUMBER)


# ---------------------------------------------------------------------------
# 書き手: course_content_builder が論文内で一意な宛先を書く
# ---------------------------------------------------------------------------


def _artifacts():
    """同じ論文に claim_span_007 が2つ（blk_a / blk_b）ある claim_object_builder 出力。"""
    return {
        _DOC_A: {
            "claim_object_builder": {"claims": [
                {"claim_id": "claim_span_007", "text": "主張A", "source_evidence_ids": ["ev_a"]},
                {"claim_id": "claim_span_007", "text": "主張B", "source_evidence_ids": ["ev_b"]},
                {"claim_id": "claim_span_009", "text": "主張C", "source_evidence_ids": ["ev_c"]},
            ]},
            "evidence_registry": {"records": [
                {"evidence_id": "ev_a", "source": {"block_id": "blk_a"}, "evidence_text": "a"},
                {"evidence_id": "ev_b", "source": {"block_id": "blk_b"}, "evidence_text": "b"},
                {"evidence_id": "ev_c", "source": {"block_id": "blk_c"}, "evidence_text": "c"},
            ]},
        }
    }


class TestClaimAddressFor:
    def test_uuid_when_live_row_is_unique_after_block_narrowing(self):
        rows = [
            {"id": _U1, "legacy_ids": ["claim_span_007"], "block_id": "blk_a", "origin": "claim_object"},
            {"id": _U2, "legacy_ids": ["claim_span_007"], "block_id": "blk_b", "origin": "claim_object"},
            {"id": _U3, "legacy_ids": ["claim_span_007", "blk_a:span_007"], "block_id": "blk_a", "origin": "span"},
        ]
        assert ccb.claim_address_for({}, _DOC_A, "claim_span_007", block_ids=["blk_a"], rows=rows, duplicated=True) == _U1
        assert ccb.claim_address_for({}, _DOC_A, "claim_span_007", block_ids=["blk_b"], rows=rows, duplicated=True) == _U2

    def test_parent_child_narrowing(self):
        rows = [
            {"id": _U1, "legacy_ids": ["claim_span_001", "claim_span_001_sub02"], "block_id": "blk_a", "origin": "claim_object"},
            {"id": _U2, "legacy_ids": ["claim_span_001_sub02"], "block_id": "blk_a", "origin": "atomic_rewrite",
             "parent_claim_id": _U1},
        ]
        assert ccb.claim_address_for({}, _DOC_A, "claim_span_001_sub02", block_ids=["blk_a"], rows=rows, duplicated=False) == _U2

    def test_block_qualified_when_rows_ambiguous(self):
        rows = [
            {"id": _U1, "legacy_ids": ["claim_span_007"], "block_id": "blk_a", "origin": "claim_object"},
            {"id": _U2, "legacy_ids": ["claim_span_007"], "block_id": "blk_a", "origin": "claim_object"},
        ]
        assert ccb.claim_address_for({}, _DOC_A, "claim_span_007", block_ids=["blk_a"], rows=rows, duplicated=False) == "blk_a:claim_span_007"

    def test_no_rows_unique_stays_bare(self):
        assert ccb.claim_address_for({}, _DOC_A, "claim_span_009", block_ids=["blk_c"], rows=None, duplicated=False) == "claim_span_009"

    def test_no_rows_duplicated_gets_block(self):
        assert ccb.claim_address_for({}, _DOC_A, "claim_span_007", block_ids=["blk_a"], rows=None, duplicated=True) == "blk_a:claim_span_007"

    def test_no_block_stays_bare(self):
        assert ccb.claim_address_for({}, _DOC_A, "synth_claim_0001", block_ids=[], rows=None, duplicated=True) == "synth_claim_0001"


class TestFreezeEmitsUniqueAddresses:
    def test_collect_assigns_addresses_from_live_rows(self):
        rows = [
            {"id": _U1, "document_id": _DOC_A, "legacy_ids": ["claim_span_007"], "block_id": "blk_a", "origin": "claim_object"},
            {"id": _U2, "document_id": _DOC_A, "legacy_ids": ["claim_span_007"], "block_id": "blk_b", "origin": "claim_object"},
            {"id": _U3, "document_id": _DOC_A, "legacy_ids": ["claim_span_009"], "block_id": "blk_c", "origin": "claim_object"},
        ]
        bundle = ccb._collect_structured_content(_artifacts(), None, rows)
        addresses = bundle["scoped"]["claim_addresses"]
        # artifact の中で先に出た claim_span_007（blk_a）が scope に残る → その UUID。
        assert addresses[(_DOC_A, "claim_span_007")] == _U1
        assert addresses[(_DOC_A, "claim_span_009")] == _U3

    def test_collect_without_rows_qualifies_only_duplicates(self):
        bundle = ccb._collect_structured_content(_artifacts())
        addresses = bundle["scoped"]["claim_addresses"]
        assert addresses[(_DOC_A, "claim_span_007")] == "blk_a:claim_span_007"
        assert addresses[(_DOC_A, "claim_span_009")] == "claim_span_009"

    def test_evidence_links_use_address(self):
        rows = [
            {"id": _U1, "document_id": _DOC_A, "legacy_ids": ["claim_span_007"], "block_id": "blk_a", "origin": "claim_object"},
            {"id": _U2, "document_id": _DOC_A, "legacy_ids": ["claim_span_007"], "block_id": "blk_b", "origin": "claim_object"},
        ]
        bundle = ccb._collect_structured_content(_artifacts(), None, rows)
        scope = ccb._BundleScope(bundle)
        component = {"component_id": "comp_001", "linked_claim_ids": ["claim_span_007"], ccb._SCOPE_DOC_KEY: _DOC_A}
        links = ccb._topic_evidence_links([component], [], {}, {}, "exact_title", scope=scope)
        claim_targets = [l["target_id"] for l in links if l["kind"] == "claim"]
        assert claim_targets == [_U1]

    def test_rewrite_existing_bare_embeds(self):
        text = "前文 ![[claim:claim_span_007]] と ![[component:comp_1]] と ![[claim:other]]"
        out = ccb.rewrite_claim_embeds(text, {"claim_span_007": _U1})
        assert f"![[claim:{_U1}]]" in out
        assert "![[component:comp_1]]" in out and "![[claim:other]]" in out

    def test_address_map_skips_numbers_with_two_addresses(self):
        bundle = {"scoped": {"claims": {}, "claim_addresses": {
            (_DOC_A, "claim_span_007"): _U1, (_DOC_B, "claim_span_007"): _U2,
            (_DOC_A, "claim_span_009"): _U3,
        }}}
        scope = ccb._BundleScope(bundle)
        mapping = ccb._claim_address_map(
            [(_DOC_A, "claim_span_007"), (_DOC_B, "claim_span_007"), (_DOC_A, "claim_span_009")], scope
        )
        assert mapping == {"claim_span_009": _U3}
