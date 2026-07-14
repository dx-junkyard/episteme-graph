"""W層（要素検討ワークスペース）ガードレールの自動テスト（設計 §12）。

Phase 0 スコープで構造的に守る:
- ``core/deliberation/`` が FastAPI を import しない（開発ルール2 / W 立場）。
- overview 経路が document 権限ゲート（``_ensure_document_viewable``）を通す（fail-closed・W5）。
- 削除 API が存在しない（W4・status 遷移のみ。Phase 0 は読み取り専用）。
- ElementRef のスコープと要素型の対応が設計書 §2 と一致する。
- A層コード（``src/episteme_graph/agents/``）を import しない（W1・読むだけ）。

Phase 2（対話・migration 046）で candidate 徹底・confidence 非表示・監査等の項目を追加する。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import refs, decomposition  # noqa: E402,F401
from core.deliberation.schema import (  # noqa: E402
    DOCUMENT_ELEMENT_TYPES,
    DOMAIN_ELEMENT_TYPES,
    ELEMENT_EQUATION,
    ELEMENT_SHARED_PART,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
    ElementResolutionError,
    scope_for_element_type,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_forbids,
)

_CORE_DIR = BACKEND / "core" / "deliberation"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")


class TestLayering:
    def test_no_fastapi_import_in_core(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["fastapi"])

    def test_core_does_not_import_a_layer_agents(self):
        # W1: A層（src/episteme_graph/agents）は読むだけ。core からの import 経路を作らない。
        assert_module_tree_does_not_import(_CORE_DIR, ["episteme_graph.agents"])

    def test_core_does_not_import_routes_or_services(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["routes", "services"])


class TestPermissionGate:
    def test_overview_route_goes_through_document_viewable_gate(self):
        # W5 fail-closed: document-scoped 要素は必ず _ensure_document_viewable を通す。
        assert "_ensure_document_viewable" in _ROUTE_SRC

    def test_route_requires_teacher(self):
        assert "_require_teacher" in _ROUTE_SRC


class TestReadOnlyPhase0:
    def test_no_delete_endpoint(self):
        # W4 / Phase 0 は読み取り専用。削除・DELETE 経路を作らない。
        assert_source_forbids(_ROUTE_SRC, ['@router.delete', ".delete("])

    def test_no_write_endpoint_in_phase0(self):
        # Phase 0 は overview（GET）のみ。POST/PUT/PATCH を作らない。
        assert_source_forbids(_ROUTE_SRC, ["@router.post", "@router.put", "@router.patch"])


class TestScopeVocabulary:
    def test_scope_element_type_mapping_matches_design(self):
        for et in DOCUMENT_ELEMENT_TYPES:
            assert scope_for_element_type(et) == SCOPE_DOCUMENT
        for et in DOMAIN_ELEMENT_TYPES:
            assert scope_for_element_type(et) == SCOPE_DOMAIN
        assert DOMAIN_ELEMENT_TYPES == (ELEMENT_SHARED_PART,)

    def test_unknown_element_type_rejected(self):
        try:
            scope_for_element_type("nonsense")
        except ElementResolutionError as exc:
            assert exc.kind == "invalid"
        else:  # pragma: no cover
            raise AssertionError("unknown element_type must raise ElementResolutionError")

    def test_elementref_validate_enforces_scope_consistency(self):
        # document 型に domain スコープを与えたら弾く。
        bad = ElementRef(scope=SCOPE_DOMAIN, element_type="theory_claim", element_id="x")
        try:
            bad.validate()
        except ElementResolutionError as exc:
            assert exc.kind == "invalid"
        else:  # pragma: no cover
            raise AssertionError("scope/element_type mismatch must raise")

    def test_equation_requires_document_id(self):
        # equation は独立テーブルを持たず document_id で一意化する（設計書 §2）。
        try:
            refs.resolve(ELEMENT_EQUATION, "eq_2_7", document_id=None)
        except ElementResolutionError as exc:
            assert exc.kind == "not_found"
        else:  # pragma: no cover
            raise AssertionError("equation without document_id must raise")
