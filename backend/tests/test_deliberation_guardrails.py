"""W層（要素検討ワークスペース）ガードレールの自動テスト（設計 §12）。

Phase 0 スコープで構造的に守っていたもの:
- ``core/deliberation/`` が FastAPI を import しない（開発ルール2 / W 立場）。
- overview 経路が document 権限ゲート（``_ensure_document_viewable``）を通す（fail-closed・W5）。
- 削除 API が存在しない（W4・status 遷移のみ）。
- ElementRef のスコープと要素型の対応が設計書 §2 と一致する。
- A層コード（``src/episteme_graph/agents/``）を import しない（W1・読むだけ）。
- 面②位置づけ（``positioning.py``）が生の confidence を返さない（W8）・禁止語彙を使わない。
- overview が positioning 失敗時にフォールバックする（decomposition だけで 500 にしない）。

Phase W-β（同一性リンク、migration 048）で追加したスコープ:
- Phase 0 は overview のみの読み取り専用だったが、W-β で同一性リンクの POST 系
  （作成・確定・却下）が加わった。``TestReadOnlyPhase0`` は「DELETE が無い」「PUT/PATCH が無い」
  「POST は identity-links 系のみ」を守るよう改訂した（旧・全 POST 禁止のテストは廃止）。
- ``create_candidate`` が ``status`` を引数として受け取らず常に ``candidate`` 固定であること
  （KN-3: LLM/呼び出し元が確定状態を指定できない）。
- ``decide`` が ``confirmed``/``rejected`` 以外を拒否し ``decided_by`` を必須とすること。
- 監査がカタログ定数 ``AUDIT_ENTITY_DELIBERATION`` を使い、生文字列 ``'deliberation'`` を
  ``record_review_event`` へ渡さないこと。
- API レスポンス組み立てが raw confidence を返す経路を持たず、``confidence_label`` を
  経由すること（W8）。
- document 削除経路（``_purge_document`` / ``delete_material``）の両方が
  ``element_identity_links`` の孤児掃除を行うこと（V層 orphan gap パターン）。
"""

from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import refs, decomposition, positioning, identity_links  # noqa: E402,F401
from core.deliberation.schema import (  # noqa: E402
    DOCUMENT_ELEMENT_TYPES,
    DOMAIN_ELEMENT_TYPES,
    ELEMENT_EQUATION,
    ELEMENT_SHARED_PART,
    IDENTITY_LINK_STATUS_CANDIDATE,
    IDENTITY_LINK_STATUS_CONFIRMED,
    IDENTITY_LINK_STATUS_REJECTED,
    SCOPE_DOCUMENT,
    SCOPE_DOMAIN,
    ElementRef,
    ElementResolutionError,
    scope_for_element_type,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_CORE_DIR = BACKEND / "core" / "deliberation"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "deliberation.py").read_text(encoding="utf-8")
_POSITIONING_SRC = (BACKEND / "core" / "deliberation" / "positioning.py").read_text(encoding="utf-8")
_IDENTITY_LINKS_SRC = (BACKEND / "core" / "deliberation" / "identity_links.py").read_text(encoding="utf-8")
_DELETION_SRC = (BACKEND / "core" / "versioning" / "deletion.py").read_text(encoding="utf-8")
_ADMIN_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")


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

    def test_identity_link_write_routes_go_through_document_editable_gate(self):
        # W5: 解釈の付与（同一性リンクの作成・確定・却下）は editor 以上。
        assert "_ensure_document_editable" in _ROUTE_SRC

    def test_route_requires_teacher(self):
        assert "_require_teacher" in _ROUTE_SRC


class TestReadOnlyPhase0:
    """Phase 0 は overview のみで読み取り専用だったが、Phase W-β で同一性リンクの
    POST 系（作成・確定・却下）が加わった。「DELETE が無い」（W4）は不変のまま、
    「POST が存在しない」という旧テストは「POST は identity-links 系のみ」に改訂する。
    PUT/PATCH は W-β でも作らない（確定/却下は POST の状態遷移で表現する。§8）。
    """

    def test_no_delete_endpoint(self):
        # W4: 削除・DELETE 経路を作らない（Phase W-β でも不変）。
        assert_source_forbids(_ROUTE_SRC, ["@router.delete", ".delete("])

    def test_no_put_or_patch_endpoint(self):
        assert_source_forbids(_ROUTE_SRC, ["@router.put", "@router.patch"])

    def test_post_endpoints_are_scoped_to_identity_links(self):
        # POST は identity-links の作成・確定・却下のみ（overview 等の読み取り専用経路に
        # 書き込みを混ぜない）。
        paths = re.findall(r'@router\.post\("([^"]+)"\)', _ROUTE_SRC)
        assert paths, "expected at least one @router.post(...) route (identity-links)"
        for path in paths:
            assert "identity-links" in path, f"unexpected POST route outside identity-links: {path}"


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


class TestPositioningLayering:
    """positioning.py（面②位置づけ）固有のレイヤリング検査。

    TestLayering はディレクトリ単位（``core/deliberation/`` 配下全 *.py）で fastapi /
    episteme_graph.agents / routes・services の非import を検査済みで、``rglob`` により
    positioning.py も自動的にカバーされる（既存テストの変更は不要）。本クラスは
    positioning.py という1ファイルに絞った検査を明示的に固定するためのもの。
    """

    def test_positioning_module_has_no_disallowed_imports(self):
        # 単一ファイルなので既に読み込み済みの _POSITIONING_SRC に対して直接検査する
        # （assert_module_tree_does_not_import は "*.py を rglob するディレクトリ" 前提の
        # ため、ファイルパスを渡すと何も見つからず素通りしてしまう）。
        assert_source_does_not_import(
            _POSITIONING_SRC,
            ["fastapi", "episteme_graph.agents", "routes", "services"],
            context="core/deliberation/positioning.py",
        )


class TestPositioningNoRawNumbers:
    """W8: confidence の生値・禁止語彙を positioning.py が返さないことを構造的に守る。"""

    def test_positioning_source_has_no_confidence_string(self):
        # "confidence" という文字列自体（部分一致含む）をソースに書かない。
        # SQL SELECT で confidence 系カラムを読む経路を作らないことも同時に保証する。
        assert_source_forbids(_POSITIONING_SRC, ["confidence"])

    def test_positioning_source_forbids_hype_vocabulary(self):
        assert_source_forbids(_POSITIONING_SRC, ["踏破", "達成率", "ランキング"])


class TestOverviewPositioningFallback:
    """面②位置づけが失敗しても overview 全体は 500 にせず内訳のみで返す（W6 の縮退）。"""

    def test_route_wraps_positioning_build_in_try_except(self):
        assert "positioning.build(ref)" in _ROUTE_SRC
        assert '"available": True' in _ROUTE_SRC
        assert '"available": False' in _ROUTE_SRC

    def test_route_still_returns_decomposition_on_positioning_failure(self):
        # decomposition の組み立てと positioning の組み立てが別の try ブロックであること
        # （positioning 側の except が decomposition の結果を握りつぶさない）を
        # ソースの並び順で確認する。
        decomposition_idx = _ROUTE_SRC.index("breakdown = decomposition.build(ref)")
        positioning_idx = _ROUTE_SRC.index("positioning.build(ref)")
        return_idx = _ROUTE_SRC.index('"decomposition": breakdown')
        assert decomposition_idx < positioning_idx < return_idx


# ---------------------------------------------------------------------------
# Phase W-β: 同一性リンク（element_identity_links）
# ---------------------------------------------------------------------------


class TestIdentityLinkAlwaysStartsCandidate:
    """KN-3: 同一性リンクは常に candidate 始まりで、生成側が確定状態を指定できない。"""

    def test_create_candidate_has_no_status_parameter(self):
        sig = inspect.signature(identity_links.create_candidate)
        assert "status" not in sig.parameters

    def test_create_candidate_binds_candidate_constant_not_a_literal(self):
        body = extract_function_source(_IDENTITY_LINKS_SRC, "create_candidate")
        assert "IDENTITY_LINK_STATUS_CANDIDATE" in body
        assert '"status": IDENTITY_LINK_STATUS_CANDIDATE' in body

    def test_create_candidate_rejects_domain_scoped_source(self):
        # インスタンス側は scope='document' のみ受理（共通部品同士の直接リンクは作らない）。
        bad_ref = ElementRef(
            scope=SCOPE_DOMAIN, element_type=ELEMENT_SHARED_PART, element_id="x", domain_key="d",
        )
        with pytest.raises(ElementResolutionError):
            identity_links.create_candidate(bad_ref, "00000000-0000-0000-0000-000000000000")


class TestIdentityLinkDecideGuards:
    """decide() は confirmed/rejected 以外を拒否し、decided_by を必須とする（帰属必須・KN-3）。"""

    def test_decide_rejects_invalid_status(self):
        with pytest.raises(ValueError):
            identity_links.decide("00000000-0000-0000-0000-000000000000", status=IDENTITY_LINK_STATUS_CANDIDATE, decided_by="u1")

    def test_decide_rejects_arbitrary_status(self):
        with pytest.raises(ValueError):
            identity_links.decide("00000000-0000-0000-0000-000000000000", status="withdrawn", decided_by="u1")

    def test_decide_requires_decided_by(self):
        with pytest.raises(ValueError):
            identity_links.decide(
                "00000000-0000-0000-0000-000000000000", status=IDENTITY_LINK_STATUS_CONFIRMED, decided_by="",
            )

    def test_decidable_statuses_are_confirmed_and_rejected_only(self):
        from core.deliberation.schema import IDENTITY_LINK_DECIDABLE_STATUSES

        assert set(IDENTITY_LINK_DECIDABLE_STATUSES) == {
            IDENTITY_LINK_STATUS_CONFIRMED,
            IDENTITY_LINK_STATUS_REJECTED,
        }


class TestConfidenceLabelNeverRaw:
    """W8: confidence の生値を段階ラベルへ変換するヘルパの境界値と、API 応答が
    raw confidence を返す経路を持たないことを検証する。"""

    def test_confidence_label_thresholds(self):
        assert identity_links.confidence_label(None) == "暫定"
        assert identity_links.confidence_label(0.0) == "暫定"
        assert identity_links.confidence_label(0.2) == "暫定"
        assert identity_links.confidence_label(0.5) == "参考"
        assert identity_links.confidence_label(0.6) == "参考"
        assert identity_links.confidence_label(0.75) == "確度高"
        assert identity_links.confidence_label(0.99) == "確度高"

    def test_confidence_label_handles_bad_input(self):
        assert identity_links.confidence_label("not-a-number") == "暫定"

    def test_route_response_helper_strips_raw_confidence_key(self):
        assert "confidence_label" in _ROUTE_SRC
        assert 'response.pop("confidence"' in _ROUTE_SRC


class TestIdentityLinkAudit:
    """W7: 監査はカタログ定数 AUDIT_ENTITY_DELIBERATION を使い、生文字列を渡さない
    （test_audit_entity_catalog_guardrails.py の TestNoRawLiteralsAtCallSites と同じ発想）。
    """

    def test_route_imports_catalog_constant(self):
        assert "AUDIT_ENTITY_DELIBERATION" in _ROUTE_SRC
        assert "from core.schema import AUDIT_ENTITY_DELIBERATION" in _ROUTE_SRC

    def test_record_review_event_calls_do_not_pass_raw_literal(self):
        offending: list[int] = []
        start = 0
        prefix = "record_review_event("
        while True:
            idx = _ROUTE_SRC.find(prefix, start)
            if idx == -1:
                break
            start = idx + len(prefix)
            tail = _ROUTE_SRC[start : start + 200].lstrip()
            if tail.startswith('"') or tail.startswith("'"):
                offending.append(idx)
        assert offending == []


class TestIdentityLinkOrphanCleanup:
    """document 削除経路の両方（V層 orphan gap パターン）が element_identity_links の
    孤児掃除を行う。instance 側は document への FK が無いポリモーフィック行のため。"""

    def test_purge_document_cleans_identity_links(self):
        body = extract_function_source(_DELETION_SRC, "_purge_document")
        assert "element_identity_links" in body

    def test_delete_material_cleans_identity_links(self):
        body = extract_function_source(_ADMIN_SRC, "delete_material")
        assert "element_identity_links" in body


class TestIdentityLinksCoreModuleLayering:
    """identity_links.py 固有のレイヤリング検査（TestLayering はディレクトリ単位で
    既に rglob 経由でカバーしているが、単一ファイルに絞った検査を明示的に固定する）。"""

    def test_identity_links_module_has_no_disallowed_imports(self):
        assert_source_does_not_import(
            _IDENTITY_LINKS_SRC,
            ["fastapi", "episteme_graph.agents", "routes", "services"],
            context="core/deliberation/identity_links.py",
        )


# ---------------------------------------------------------------------------
# レビュー指摘 [P1] (2026-07-15): 同一性リンクの一意性が instance_document_id で
# スコープされておらず、equation のように独立テーブルを持たない要素型
# （element_id が論文間で衝突しうる）で別論文の候補作成・一覧取得が衝突・漏えいする。
# ---------------------------------------------------------------------------

_MIGRATION_048_SRC = read_migration_sql(BACKEND, 48)


class TestIdentityLinkUniqueConstraintIncludesDocumentId:
    """migration 048 の UNIQUE 制約が instance_document_id を含む（4列）こと。"""

    def test_unique_constraint_includes_all_four_columns(self):
        assert (
            "UNIQUE (instance_element_type, instance_element_id, instance_document_id, shared_part_id)"
            in _MIGRATION_048_SRC
        )

    def test_constraint_is_explicitly_named_not_anonymous(self):
        # 冪等な後方互換 DO $$ ガードが名前で存在確認できるよう、CREATE TABLE 側でも
        # 明示的に固定された制約名を使う（default 生成名に依存しない）。
        assert "CONSTRAINT element_identity_links_instance_shared_uniq" in _MIGRATION_048_SRC

    def test_has_idempotent_backfill_guard_for_old_three_column_constraint(self):
        # ローカル dev DB に旧3列制約が適用済みの場合に備え、DO $$ ブロックで
        # 存在確認のうえ DROP → 新制約 ADD する冪等ガードがあること。
        assert "DO $$" in _MIGRATION_048_SRC
        assert "pg_constraint" in _MIGRATION_048_SRC
        assert "DROP CONSTRAINT" in _MIGRATION_048_SRC
        assert "to_regclass" in _MIGRATION_048_SRC

    def test_backward_compatibility_guard_avoids_dbapi_percent_markers(self):
        # migration runner は SQLAlchemy の exec_driver_sql() を通じて DB-API へ
        # SQL を渡す。PostgreSQL format() の識別子トークンは DB-API 側でプレースホルダと
        # 誤認され、実行前に TypeError になるため使わない。
        assert "%" not in _MIGRATION_048_SRC
        assert "quote_ident(old_conname)" in _MIGRATION_048_SRC

    def test_migration_passes_the_repo_wide_idempotency_lint(self):
        # backend/db/*.sql 全体に対する構造的な冪等性 lint（test_migrations_runner.py の
        # TestIdempotencyLint と同じ検査ロジック）を 048 単体にも直接適用しておく
        # （DO $$ ブロック内の ALTER TABLE ADD/DROP CONSTRAINT が正しくマスクされ、
        # "ADD CONSTRAINT outside a DO $$ guard" 等の違反として誤検出されないことの保証）。
        from tests.test_migrations_runner import _collect_idempotency_violations

        # _collect_idempotency_violations はディレクトリ全体を走査する実装のため、
        # 048 単体を対象にするには一時ディレクトリへコピーして走査する。
        import tempfile
        from pathlib import Path as _Path

        with tempfile.TemporaryDirectory() as tmp:
            target = _Path(tmp) / "048_element_identity_links.sql"
            target.write_text(_MIGRATION_048_SRC, encoding="utf-8")
            violations = _collect_idempotency_violations(_Path(tmp))
        assert violations == [], violations


class TestIdentityLinkCreateCandidateScopedByDocument:
    """create_candidate() の ON CONFLICT / 衝突時フォールバック SELECT の両方が
    instance_document_id で絞り込むこと（同じ element_id でも別論文なら別行になる）。"""

    def test_on_conflict_target_includes_document_id(self):
        body = extract_function_source(_IDENTITY_LINKS_SRC, "create_candidate")
        assert (
            "ON CONFLICT (instance_element_type, instance_element_id, instance_document_id, shared_part_id)"
            in body
        )

    def test_conflict_fallback_select_filters_by_document_id(self):
        body = extract_function_source(_IDENTITY_LINKS_SRC, "create_candidate")
        # 衝突時に既存行を読み直す SELECT が instance_document_id も一致条件に含むこと
        # （含まなければ別論文の既存行を誤って返してしまう）。
        select_start = body.index("SELECT {_COLUMNS_SQL} FROM element_identity_links")
        select_fragment = body[select_start : select_start + 400]
        assert "instance_document_id = :document_id" in select_fragment


class TestIdentityLinkListForInstanceRequiresDocumentId:
    """list_for_instance() が document_id を必須引数として要求し、SQL でも絞り込むこと。

    document_id を省略した旧シグネチャでの呼び出しは、DB に到達する前に
    TypeError で fail する（DB 接続なしで検証できる）。
    """

    def test_signature_requires_document_id_with_no_default(self):
        sig = inspect.signature(identity_links.list_for_instance)
        params = sig.parameters
        assert "document_id" in params
        assert params["document_id"].default is inspect.Parameter.empty

    def test_calling_without_document_id_raises_type_error_before_touching_db(self):
        with pytest.raises(TypeError):
            identity_links.list_for_instance("figure", "some-figure-id")  # type: ignore[call-arg]

    def test_query_filters_by_instance_document_id(self):
        body = extract_function_source(_IDENTITY_LINKS_SRC, "list_for_instance")
        assert "instance_document_id = :document_id" in body


class TestIdentityLinkRouteScopedListCallSite:
    """呼び出し側（routes/deliberation.py）が解決済み ElementRef.document_id を
    list_for_instance に渡すこと（core 側が要求を強制していても、呼び出し側が
    空文字などでごまかせば無意味なため、call site 自体も固定する）。"""

    def test_route_passes_resolved_document_id(self):
        assert (
            "identity_links.list_for_instance(ref.element_type, ref.element_id, ref.document_id or \"\")"
            in _ROUTE_SRC
        )


# ---------------------------------------------------------------------------
# レビュー指摘 [P1] (2026-07-15): domain-scoped 取得 API が由来 document の権限を
# 確認せず、exemplar_images（由来 document・figure_id・minio_key を含む）と
# identity-links の instance_document_id を無条件に返していた。
# ---------------------------------------------------------------------------

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:  # pragma: no cover
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


@pytest.fixture
def deliberation_routes():
    """``routes/deliberation.py`` を直接 import する（test_library_api.py と同型のパターン）。

    import 自体は DB へ接続しない（core.postgres.get_session は呼び出し時まで遅延する）ため、
    ここでテストする純粋なフィルタ関数（``_filter_by_document_view`` 等）は
    DB 接続なしで検証できる。
    """
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    import routes.deliberation as mod

    return mod


@_skip_no_fastapi
class TestFilterByDocumentView:
    """``_filter_by_document_view``（route 層の純粋フィルタ関数）。

    ``can_view(document_id) -> bool`` のみに依存し DB を触らない。可視の要素だけを残し、
    隠した件数を戻り値として返す（P4・出所の正直さ: 黙って欠落させない）。
    """

    def test_keeps_only_viewable_items_and_reports_hidden_count(self, deliberation_routes):
        items = [
            {"source_document_id": "doc-a", "v": 1},
            {"source_document_id": "doc-b", "v": 2},
            {"source_document_id": "doc-a", "v": 3},
        ]
        visible, hidden = deliberation_routes._filter_by_document_view(
            items, "source_document_id", lambda doc_id: doc_id == "doc-a"
        )
        assert [i["v"] for i in visible] == [1, 3]
        assert hidden == 1

    def test_item_without_document_id_is_hidden(self, deliberation_routes):
        items = [{"source_document_id": "", "v": 1}]
        visible, hidden = deliberation_routes._filter_by_document_view(
            items, "source_document_id", lambda doc_id: True
        )
        assert visible == []
        assert hidden == 1

    def test_empty_list_returns_no_hidden(self, deliberation_routes):
        visible, hidden = deliberation_routes._filter_by_document_view(
            [], "source_document_id", lambda doc_id: True
        )
        assert visible == []
        assert hidden == 0


@_skip_no_fastapi
class TestMakeDocumentViewChecker:
    """``_make_document_view_checker`` は ``resolve_document_access`` をユニークな
    document_id ごとに1回だけ呼ぶ（N+1 回避。CLAUDE.md「チャンク単位のループ内で
    user_can_view_document を繰り返し呼ばないこと」と同じ方針）。"""

    def test_caches_per_document_id(self, deliberation_routes, monkeypatch):
        calls: list[str] = []

        class _FakeAccess:
            def __init__(self, can_view: bool) -> None:
                self.can_view = can_view

        def _fake_resolve(uid, doc_id):
            calls.append(doc_id)
            return _FakeAccess(doc_id == "doc-a")

        monkeypatch.setattr(deliberation_routes, "resolve_document_access", _fake_resolve)
        can_view = deliberation_routes._make_document_view_checker({"id": "u1"})
        assert can_view("doc-a") is True
        assert can_view("doc-a") is True
        assert can_view("doc-b") is False
        assert calls == ["doc-a", "doc-b"]


@_skip_no_fastapi
class TestApplyExemplarImageGate:
    """``_apply_exemplar_image_gate``: shared_part overview の
    ``frozen_content.exemplar_images`` を由来 document の閲覧権限でフィルタし、
    ``exemplar_images_hidden_count`` を正直に返す（W5: 画像は由来 document の権限を継承）。
    """

    def test_filters_images_and_sets_hidden_count(self, deliberation_routes, monkeypatch):
        def _fake_resolve(uid, doc_id):
            class _A:
                can_view = doc_id == "doc-owned"

            return _A()

        monkeypatch.setattr(deliberation_routes, "resolve_document_access", _fake_resolve)
        breakdown = {
            "fields": {
                "frozen_content": {
                    "exemplar_images": [
                        {"source_document_id": "doc-owned", "figure_id": "f1"},
                        {"source_document_id": "doc-other", "figure_id": "f2"},
                    ]
                }
            }
        }
        deliberation_routes._apply_exemplar_image_gate(breakdown, {"id": "u1"})
        images = breakdown["fields"]["frozen_content"]["exemplar_images"]
        assert [i["figure_id"] for i in images] == ["f1"]
        assert breakdown["fields"]["exemplar_images_hidden_count"] == 1

    def test_no_exemplar_images_is_a_noop(self, deliberation_routes):
        breakdown = {"fields": {"frozen_content": {}}}
        deliberation_routes._apply_exemplar_image_gate(breakdown, {"id": "u1"})
        assert "exemplar_images_hidden_count" not in breakdown["fields"]

    def test_missing_fields_is_a_noop(self, deliberation_routes):
        breakdown: dict = {}
        deliberation_routes._apply_exemplar_image_gate(breakdown, {"id": "u1"})
        assert breakdown == {}


class TestSharedPartPermissionGateWiring:
    """route 層の呼び出し箇所（ソース検査）。P4: 隠した件数を正直に返すこと。"""

    def test_overview_route_applies_exemplar_image_gate_for_shared_part(self):
        assert "_apply_exemplar_image_gate(breakdown, current_user)" in _ROUTE_SRC

    def test_shared_part_identity_links_route_filters_and_reports_hidden_count(self):
        body = extract_function_source(_ROUTE_SRC, "list_identity_links_for_shared_part")
        assert "_filter_by_document_view(" in body
        assert '"hidden_count": hidden' in body


# ---------------------------------------------------------------------------
# レビュー指摘 [P2] (2026-07-15): C層レンズ（positioning.py の endorsement レンズ）が
# component_citations を集約していなかった（設計書 §4.4「誰が承認・引用したか」未達）。
# ---------------------------------------------------------------------------


class TestPositioningEndorsementLensAggregatesCitations:
    def test_build_endorsement_query_joins_component_citations(self):
        body = extract_function_source(_POSITIONING_SRC, "_build_endorsement")
        assert "component_citations" in body
        assert "citation_count" in body

    def test_no_raw_citation_count_string_in_positioning(self):
        # W8: 生の件数は返さない。理由付きラベルのみで表現する（"citation_count" という
        # SQL 列名/変数名は許容するが、テンプレート内で数値を文字列連結しないこと自体は
        # TestPositioningNoRawNumbers の "confidence" 禁止語彙チェックと同型に
        # _endorsement_items_from_rows の単体テスト（test_deliberation_positioning.py）
        # 側で検証する。ここでは C層一覧（theory_components.py）のような f"{count}名..."
        # 形式の文字列組み立てが positioning.py に持ち込まれていないことのみ確認する。
        assert "citation_count}" not in _POSITIONING_SRC
        assert "f\"{count}" not in _POSITIONING_SRC
