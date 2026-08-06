"""監査 entity_type カタログのガードレール（調査レポート Tier2 提案7）。

``theory_review_events`` テーブルに entity_type の CHECK 制約は無い（各層が独自に
追記してきた経緯のため）。``core/schema.py`` の ``AUDIT_ENTITY_TYPES`` を唯一の
正本カタログとし、以下を構造的に守る:

- カタログに重複や空文字列が無い
- 監査を書き込む各所（api/routes/*.py, api/services.py, 各層の schema.py,
  core/document_pipeline/persistence.py）が生の文字列リテラルではなく
  カタログ定数を参照している（新規 entity_type がカタログ登録なしに紛れ込むのを防ぐ）
- 各層固有の語彙モジュール（doubt / versioning / reconstruction の schema.py）が
  カタログの値と一致している（値の二重定義がない）
"""

from __future__ import annotations

from pathlib import Path

from core.schema import AUDIT_ENTITY_TYPES

_BACKEND = Path(__file__).resolve().parents[1]

# 監査 INSERT の呼び出し元（提案7で record_review_event への委譲に統一した箇所）。
_AUDIT_CALLER_FILES = [
    _BACKEND / "api" / "services.py",
    _BACKEND / "api" / "routes" / "theory_components.py",
    _BACKEND / "api" / "routes" / "atlas.py",
    _BACKEND / "api" / "routes" / "doubt.py",
    _BACKEND / "api" / "routes" / "reconstruction.py",
    _BACKEND / "api" / "routes" / "admin_assistant.py",
    _BACKEND / "api" / "routes" / "admin.py",
    _BACKEND / "api" / "routes" / "library.py",
    _BACKEND / "api" / "routes" / "versioning.py",
    _BACKEND / "api" / "routes" / "deliberation.py",
    _BACKEND / "api" / "routes" / "teaching_figures.py",
    _BACKEND / "api" / "routes" / "landscape.py",
]

# 監査 INSERT 呼び出しのパターン（関数名 + 開き括弧の直後）。
_CALL_PREFIXES = (
    "_record_review_event(",
    "record_review_event(",
    "_record_doubt_event(",
    "_record_recon_event(",
    "_record_tension_event(",
    "_record_anchor_event(",
    "_record_assistant_event(",
    "_record_next_step_event(",
    "_record_manual_event(",
    "_record_item_event(",
    "_audit(",
)


class TestCatalogIsWellFormed:
    def test_no_duplicates(self):
        assert len(AUDIT_ENTITY_TYPES) == len(set(AUDIT_ENTITY_TYPES))

    def test_no_blank_values(self):
        assert all(isinstance(v, str) and v.strip() for v in AUDIT_ENTITY_TYPES)

    def test_catalog_covers_documented_vocabulary(self):
        """CLAUDE.md に列挙された entity_type 語彙がカタログに揃っている。"""
        documented = {
            "document_share", "endorsement", "explanation", "citation", "component", "claim",
            "tension", "structure_anchor", "atlas_binding",
            "ledger", "assumption", "challenge", "verification_proposal", "counterfactual_session",
            "next_step", "assistant_action",
            "library_entry", "reconstruction_item", "reconstruction_response",
        }
        missing = documented - set(AUDIT_ENTITY_TYPES)
        assert missing == set(), f"catalog missing documented entity_types: {missing}"


class TestNoRawLiteralsAtCallSites:
    """新規 entity_type がカタログ登録なしに紛れ込むのを防ぐ（回帰検出）。

    提案7の一本化後、監査呼び出し元は catalog 定数 (``AUDIT_ENTITY_*``) を渡す
    ようになっている。この呼び出しの直後に生の文字列リテラルが来ていたら、
    新しい entity_type がカタログを経由せず追加された可能性が高い。
    """

    def test_call_sites_do_not_pass_raw_string_literals(self):
        offending: list[str] = []
        for path in _AUDIT_CALLER_FILES:
            src = path.read_text(encoding="utf-8")
            for prefix in _CALL_PREFIXES:
                start = 0
                while True:
                    idx = src.find(prefix, start)
                    if idx == -1:
                        break
                    start = idx + len(prefix)
                    tail = src[start:start + 200]
                    stripped = tail.lstrip()
                    if stripped.startswith('"') or stripped.startswith("'"):
                        line_no = src.count("\n", 0, idx) + 1
                        offending.append(f"{path.name}:{line_no} — {prefix} called with raw literal")
        assert offending == [], (
            "Raw string literals found where a core.schema.AUDIT_ENTITY_* constant "
            f"was expected (register new entity_type values in the catalog instead): {offending}"
        )


class TestLayerVocabulariesMatchCatalog:
    """各層固有の語彙モジュールがカタログの値と一致する（二重定義の検出）。"""

    def test_doubt_layer_vocabulary(self):
        from core.doubt.schema import LEDGER_REVIEW_ENTITY_TYPES

        assert set(LEDGER_REVIEW_ENTITY_TYPES) <= set(AUDIT_ENTITY_TYPES)
        assert LEDGER_REVIEW_ENTITY_TYPES == (
            "ledger", "assumption", "challenge", "verification_proposal", "counterfactual_session",
        )

    def test_versioning_layer_vocabulary(self):
        from core.versioning.schema import AUDIT_DELETION, AUDIT_RELEASE, AUDIT_SUBSCRIPTION

        assert {AUDIT_RELEASE, AUDIT_DELETION, AUDIT_SUBSCRIPTION} <= set(AUDIT_ENTITY_TYPES)

    def test_reconstruction_layer_vocabulary(self):
        from core.reconstruction.schema import ENTITY_ITEM, ENTITY_RESPONSE

        assert {ENTITY_ITEM, ENTITY_RESPONSE} <= set(AUDIT_ENTITY_TYPES)


class TestAuditRowShapeUnchanged:
    """監査行の列構成（提案7の絶対制約: データ形状を変えない）。"""

    def test_insert_columns_unchanged(self):
        src = (_BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        block = src.split("def record_review_event")[1].split("\ndef ")[0]
        assert "INSERT INTO theory_review_events (entity_type, entity_id, old_status, new_status, changed_by, metadata)" in block
        assert "CAST(:changed_by AS uuid)" in block
        assert "CAST(:metadata AS jsonb)" in block
