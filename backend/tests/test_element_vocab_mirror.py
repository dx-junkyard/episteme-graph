"""統制語彙の訳語ミラー（Python ⇄ JS）の完全一致ガードレール（CP4）。

`docs/features/element_context_presentation_redesign.md` §2.4 / §3.2 CP4:
訳語の正本は `backend/core/element_vocab.py`（サーバ）+
`frontend/public/js/element-vocab.js`（ミラー）の**1組**で、キー集合と訳語文字列の
一致をテストで固定する。stage 訳語が3箇所に分裂して不一致だった（RC10）のが本件の
発端なので、ここが崩れたら「正本が2つある」ことになる。

JS 側のパースは既存の `*_ui_static.py` 群と同じ正規表現方式（Node を呼ばない）。
"""
from __future__ import annotations

import re
from pathlib import Path

from core import element_vocab

ROOT = Path(__file__).resolve().parents[2]
VOCAB_JS = ROOT / "frontend" / "public" / "js" / "element-vocab.js"

# Python 正本の定数名 → JS の var 名（同名で揃えている）。
MIRRORED_TABLES = [
    "THEORY_STAGE_LABELS",
    "THEORY_STAGE_DISPLAY_TO_KEY",
    "OPERATION_LABELS",
    "CHAIN_TYPE_LABELS",
    "LINK_STATUS_LABELS",
    "DEFINITION_STATUS_LABELS",
    "SYMBOL_SCOPE_LABELS",
    "CLAIM_TYPE_LABELS",
    "EQUATION_ROLE_LABELS",
]

# ElementVocab に公開されているべきミラー関数。
MIRRORED_FUNCTIONS = [
    "theoryStageLabel",
    "operationLabel",
    "chainTypeLabel",
    "linkStatusLabel",
    "definitionStatusLabel",
    "symbolScopeLabel",
    "claimTypeLabel",
]

_ENTRY_RE = re.compile(r"(?:\"([^\"]+)\"|([A-Za-z_][\w$]*))\s*:\s*\"([^\"]*)\"")


def _js() -> str:
    return VOCAB_JS.read_text(encoding="utf-8")


def _js_table(name: str) -> dict[str, str]:
    src = _js()
    marker = "var " + name + " = {"
    assert marker in src, "JS に " + name + " が無い"
    start = src.index(marker)
    end = src.index("\n  };", start)
    block = src[start + len(marker) : end]
    out: dict[str, str] = {}
    for quoted, bare, value in _ENTRY_RE.findall(block):
        out[quoted or bare] = value
    return out


# ---------------------------------------------------------------------------
# 1. キー集合と訳語文字列の完全一致
# ---------------------------------------------------------------------------


class TestTableParity:
    def test_every_mirrored_table_matches_exactly(self):
        for name in MIRRORED_TABLES:
            python_table = getattr(element_vocab, name)
            js_table = _js_table(name)
            assert js_table == python_table, name + " の Python ⇄ JS が不一致"

    def test_key_sets_are_not_empty(self):
        for name in MIRRORED_TABLES:
            assert getattr(element_vocab, name), name

    def test_python_tables_have_no_empty_translation(self):
        """空文字は「未知キー」の戻り値なので、表の値として持たない。"""
        for name in MIRRORED_TABLES:
            for key, value in getattr(element_vocab, name).items():
                assert value.strip(), name + ":" + key


# ---------------------------------------------------------------------------
# 2. 語彙キーが A層スキーマの語彙を漏れなく覆う
# ---------------------------------------------------------------------------


class TestVocabularyCoverage:
    def test_theory_stage_keys_match_the_agent_schema(self):
        from episteme_graph.agents.component_graph.schema import (  # noqa: PLC0415
            THEORY_STAGES,
            THEORY_STAGE_LABELS as AGENT_STAGE_LABELS,
        )

        assert set(element_vocab.THEORY_STAGE_LABELS) == set(THEORY_STAGES)
        # 英語表示名からの逆引きが A層の表示名を全て解決できること。
        for key, display in AGENT_STAGE_LABELS.items():
            assert element_vocab.theory_stage_key(display) == key

    def test_operation_keys_cover_the_controlled_operations(self):
        from episteme_graph.agents.derivation_chain.schema import (  # noqa: PLC0415
            CONTROLLED_OPERATIONS,
        )

        missing = sorted(op for op in CONTROLLED_OPERATIONS if not element_vocab.operation_label(op))
        assert missing == [], "訳語の無い operation: " + str(missing)

    def test_operation_labels_accept_the_legacy_english_display_form(self):
        """`admin-lecture-studio.js` の LS_OPERATION_VERB_JA から移植した英語表示形。"""
        assert element_vocab.operation_label("Apply definition") == "定義を適用"
        assert element_vocab.operation_label("Derive result") == "結果を導出"
        assert element_vocab.operation_label("Solve linear system") == "連立方程式を求解"

    def test_claim_type_keys_cover_the_agent_and_db_vocabularies(self):
        from episteme_graph.agents.claim_object_builder.schema import (  # noqa: PLC0415
            CLAIM_TYPE_ONTOLOGY,
        )

        missing = sorted(t for t in CLAIM_TYPE_ONTOLOGY if not element_vocab.claim_type_label(t))
        assert missing == [], "訳語の無い claim_type: " + str(missing)
        # theory_claims.claim_type の CHECK 語彙（migration 013）。
        for db_type in (
            "equation", "relation", "derivation_step", "correction",
            "uncertainty", "diagnostic_claim", "equation_approximation",
            "equation_constraint",
        ):
            assert element_vocab.claim_type_label(db_type), db_type

    def test_symbol_and_link_and_chain_vocabularies(self):
        from episteme_graph.agents.equation_semantics.schema import (  # noqa: PLC0415
            LINK_STATUSES,
            ROLE_IN_ARGUMENT_VOCAB,
        )
        from episteme_graph.agents.symbol_registry.schema import (  # noqa: PLC0415
            DEFINITION_STATUSES,
            SYMBOL_SCOPES,
        )

        assert set(element_vocab.LINK_STATUS_LABELS) == set(LINK_STATUSES)
        assert set(element_vocab.EQUATION_ROLE_LABELS) == set(ROLE_IN_ARGUMENT_VOCAB)
        assert set(element_vocab.SYMBOL_SCOPE_LABELS) == set(SYMBOL_SCOPES)
        assert set(DEFINITION_STATUSES) <= set(element_vocab.DEFINITION_STATUS_LABELS)
        # 「論文中に定義が無い」は読者に有用な事実なので定義状態と同じ表で持つ。
        assert element_vocab.definition_status_label("definition_missing")


# ---------------------------------------------------------------------------
# 3. 未知キーは fail-closed（内部語彙を画面に漏らさない）
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_python_lookups_return_empty_for_unknown_keys(self):
        for func in (
            element_vocab.theory_stage_label,
            element_vocab.operation_label,
            element_vocab.chain_type_label,
            element_vocab.link_status_label,
            element_vocab.definition_status_label,
            element_vocab.symbol_scope_label,
            element_vocab.claim_type_label,
            element_vocab.equation_role_label,
        ):
            assert func("no_such_key_zzz") == ""
            assert func(None) == ""
            assert func("") == ""

    def test_link_status_fact_is_none_for_derived_and_unknown(self):
        assert element_vocab.link_status_fact("derived") is None
        assert element_vocab.link_status_fact("zzz") is None
        assert element_vocab.link_status_fact("axiomatic")
        assert element_vocab.link_status_fact("external_reference")
        assert element_vocab.link_status_fact("unresolved")

    def test_js_lookups_are_fail_closed(self):
        src = _js()
        start = src.index("function strictLookup(table, key) {")
        body = src[start : src.index("\n  }\n", start)]
        assert "hasOwnProperty.call(table, raw)" in body
        assert 'return "";' in body
        for name in MIRRORED_FUNCTIONS:
            assert "function " + name + "(" in src, name


# ---------------------------------------------------------------------------
# 4. 公開面と ES5（開発ルール5）
# ---------------------------------------------------------------------------


class TestJsContract:
    def test_functions_are_published_on_element_vocab(self):
        src = _js()
        published = src[src.index("global.ElementVocab = {") :]
        for name in MIRRORED_FUNCTIONS + ["theoryStageKey"]:
            assert name + ": " + name in published, name

    def test_still_es5(self):
        src = _js()
        assert "=>" not in src
        assert "`" not in src
        assert not re.search(r"(^|[^\w.$])const\s", src)
        assert not re.search(r"(^|[^\w.$])let\s", src)

    def test_points_at_the_python_source_of_truth(self):
        src = _js()
        assert "backend/core/element_vocab.py" in src
        assert "element_context_presentation_redesign.md" in src

    def test_python_module_is_pure(self):
        """core/ は FastAPI を import しない（開発ルール2）。DB にも触らない。"""
        source = Path(element_vocab.__file__).read_text(encoding="utf-8")
        for forbidden in ("fastapi", "sqlalchemy", "core.postgres", "openai"):
            assert forbidden not in source
