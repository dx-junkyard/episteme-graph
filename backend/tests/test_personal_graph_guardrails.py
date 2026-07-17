"""個人知識ネットワーク（Phase P-0）ガードレールの自動テスト（設計書 §11）。

構造的に守る不変条項:
- **PN-5 非LLM・読む側**: ``core/personal_graph/`` が FastAPI / routes / services /
  ``core.llm`` を import しない（import レベルでの固定）。
- **PN-1 本人のみ可視**: ``routes/personal_map.py`` は本人（``current_user["id"]``）
  スコープのみで、``{user_id}`` のような他人を指すパスパラメータを受け付けない。
- **PN-2 導出であって記録ではない**: 書き込み・削除エンドポイント
  （``@router.post`` / ``.put`` / ``.patch`` / ``.delete``）が無い。
- **PN-3 candidate を数えない**: ``derive.py`` のソースが除外・異議処理の語彙
  （``superseded`` / ``llm_candidate`` / ``disagreed`` / ``verdict_wrong`` /
  ``TENSION_OWNED_STATUSES``）を扱っていることを語彙アサーションで確認する。
- **PN-4 数値を見せない・煽らない**: 禁止語彙（「踏破」「達成率」「ランキング」）が
  core/personal_graph/ 配下・routes/personal_map.py のどちらにも無い。

注意: ``core/personal_graph/`` は別エージェントが並行実装中。``derive.py`` を直接
読むアサーションは、そのファイルが着地するまで収集時に ``FileNotFoundError`` で
失敗する — それが現時点で正しい状態（import ではなく ``read_text`` なので、実装が
未完成でも構文エラーの影響は受けない）。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
    extract_function_source,
)

_CORE_DIR = BACKEND / "core" / "personal_graph"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "personal_map.py").read_text(encoding="utf-8")
_ADMIN_ROUTE_SRC = (BACKEND / "api" / "routes" / "admin.py").read_text(encoding="utf-8")

_BANNED_VOCAB = ("踏破", "達成率", "ランキング")


class TestCoreIsFrameworkFreeAndReadOnly:
    """core/personal_graph/ は FastAPI / routes / services / core.llm を import しない（PN-5）。"""

    def test_no_fastapi_import(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["fastapi"])

    def test_no_routes_or_services_import(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["routes", "services"])

    def test_no_core_llm_import(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["core.llm"])


class TestRouteIsPersonalScopeOnly:
    """PN-1: 本人スコープのみ。他人の user_id を受けるパラメータを作らない。"""

    def test_route_uses_current_user_and_accessible_course_gate(self):
        assert "_get_current_user" in _ROUTE_SRC
        assert "get_accessible_course_data" in _ROUTE_SRC

    def test_no_user_id_path_parameter(self):
        assert "{user_id}" not in _ROUTE_SRC


class TestRouteIsReadOnly:
    """PN-2: 導出であって記録ではない。書き込み・削除 API を作らない。"""

    def test_no_write_or_delete_endpoints(self):
        assert_source_forbids(
            _ROUTE_SRC,
            ["@router.post", "@router.put", "@router.patch", "@router.delete"],
            context="routes/personal_map.py",
        )


class TestDeriveExclusionVocabulary:
    """PN-3: candidate 系除外・異議シグナル処理・帰属候補の不使用をソース語彙で確認する。"""

    def test_derive_source_processes_exclusion_vocabulary(self):
        derive_src = (_CORE_DIR / "derive.py").read_text(encoding="utf-8")
        for term in (
            "superseded",
            "llm_candidate",
            "disagreed",
            "verdict_wrong",
            "TENSION_OWNED_STATUSES",
        ):
            assert term in derive_src, f"derive.py is missing expected vocabulary: {term!r}"


class TestBannedVocabulary:
    """PN-4: 煽り・誇示語彙がコード/文言のどこにも無い。"""

    def test_core_tree_forbids_banned_vocab(self):
        assert_module_tree_forbids(_CORE_DIR, _BANNED_VOCAB)

    def test_route_forbids_banned_vocab(self):
        assert_source_forbids(_ROUTE_SRC, _BANNED_VOCAB, context="routes/personal_map.py")


class TestJourneyRouteExists:
    """Phase P-2: 旅の GET エンドポイントが登録されている（PN-1/PN-2 は既存クラスが検査済み）。"""

    def test_journey_get_endpoint_registered(self):
        assert "personal-network/journey" in _ROUTE_SRC
        assert "@router.get(" in _ROUTE_SRC


class TestTensionAnchorRequiresGenuineConnect:
    """review 指摘1（P1）: 未接続 tension が LLM 候補の component をアンカーに使ってしまう
    バグの回帰防止。``_tension_anchor`` / ``_tension_bridge_edges`` は
    ``payload.connected_refs``（本人が connect 操作で明示的に指定した ID のみ）を使い、
    LLM 候補生成時点で書かれる ``payload.target_refs`` を根拠にしない（PN-3）。
    """

    def test_derive_uses_connected_refs_and_gates_on_connected_status(self):
        derive_src = (_CORE_DIR / "derive.py").read_text(encoding="utf-8")
        assert "connected_refs" in derive_src
        assert '"connected"' in derive_src

    def test_services_writes_connected_refs_on_connect(self):
        services_src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        body = services_src.split("def connect_tension_trace")[1].split("\ndef ")[0]
        assert "connected_refs" in body


class TestConnectTensionValidatesDocumentViewability:
    """review 指摘2（P1）: ``connect_tension_trace`` が渡された component_id の閲覧可否を
    検証せずに accepted すると、不正な component 参照を持つ trace 経由で journey が
    閲覧不可 document の情報を漏らし得る。connect 時点で fail-closed に拒否することを確認する。
    """

    def test_connect_tension_trace_checks_component_viewability(self):
        services_src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        body = services_src.split("def connect_tension_trace")[1].split("\ndef ")[0]
        assert "_tension_connect_component_viewable" in body

    def test_component_viewability_helper_uses_resolve_document_access(self):
        services_src = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
        body = services_src.split("def _tension_connect_component_viewable")[1].split("\ndef ")[0]
        assert "resolve_document_access" in body


class TestJourneyChecksStartDocumentViewability:
    """review 指摘2（P1）: journey が起点 document の閲覧可否を確認しないバグの回帰防止。
    ``journey_for_node`` は ``can_view_document`` コールバックを受け取り、閲覧不可なら
    ローカルグラフ・同一性リンクを読まない（PN-7）。呼び出し元 routes/personal_map.py は
    必ずこのコールバックを注入する。
    """

    def test_journey_for_node_accepts_can_view_document_callback(self):
        journey_src = (_CORE_DIR / "journey.py").read_text(encoding="utf-8")
        assert "can_view_document" in journey_src

    def test_route_injects_view_check_into_journey(self):
        assert "can_view_document" in _ROUTE_SRC
        assert "user_can_view_document" in _ROUTE_SRC


class TestJourneyRequiresActiveLibraryEntry:
    """review 指摘3（P2）: retired な library entry を経由した traversal の回帰防止。
    active entry（journey_for_node が ``fetch_library_entry_names`` の結果=active のみで
    ``library_entries`` を積む）であることを、共通部品ハブとして扱う前提条件にする。
    """

    def test_journey_treats_missing_library_entry_as_not_a_hub(self):
        journey_src = (_CORE_DIR / "journey.py").read_text(encoding="utf-8")
        assert "entry is None" in journey_src

    def test_queries_only_adds_active_entries_to_library_entries_dict(self):
        journey_src = (_CORE_DIR / "journey.py").read_text(encoding="utf-8")
        assert "shared_part_id not in names" in journey_src


class TestJourneyUsesConfirmedLinksOnly:
    """PN-6: 旅の traversal は confirmed 同一性リンクのみを辿る。

    ``confirmed_links_for_document``（W-β の正本読み取り関数）は
    ``core/personal_graph/queries.py`` がラップして呼ぶ（``journey.py`` は
    ``core.personal_graph`` パッケージ内で DB を直接知らない、という queries.py 自身の
    docstring 上の規約を維持するため）。``journey.py`` 自体には candidate/confidence の
    生値を辿る経路が無いことをソース語彙で確認する。
    """

    def test_queries_uses_confirmed_links_for_document(self):
        queries_src = (_CORE_DIR / "queries.py").read_text(encoding="utf-8")
        assert "confirmed_links_for_document" in queries_src

    def test_journey_does_not_reference_candidate_status(self):
        journey_src = (_CORE_DIR / "journey.py").read_text(encoding="utf-8")
        assert "candidate" not in journey_src

    def test_journey_does_not_reference_confidence(self):
        journey_src = (_CORE_DIR / "journey.py").read_text(encoding="utf-8")
        assert "confidence" not in journey_src

    def test_journey_defines_boundary_constants(self):
        journey_src = (_CORE_DIR / "journey.py").read_text(encoding="utf-8")
        assert "MAX_FANOUT_PER_SEGMENT" in journey_src
        assert "MAX_STEPS" in journey_src


class TestBridgeAggregationGuardrails:
    """ビジョン Phase B（学習者重ね合わせの橋候補集約, §3 修正①/§4 KN-4/§7 Phase B）。

    - 集約の根拠は本人が connect 操作で指定した ``payload.connected_refs`` のみ。
      LLM 候補由来の ``target_refs`` を ``bridges.py`` が一切参照しない（KN-4/PN-3）
    - k=3 は ``core/privacy.py`` の ``K_ANONYMITY`` を import して使う（リテラル再定義禁止）
    - 応答組み立てに生カウントキー（"count"/"learners" 等）が無く、
      ``bucket_count_range`` のレンジ文字列のみを経由する（PN-1/P3）
    - core 非 FastAPI / 非 services / 禁止語彙（踏破・達成率 等）は既存の
      ``TestCoreIsFrameworkFreeAndReadOnly`` / ``TestBannedVocabulary`` の
      ディレクトリ横断検査が ``bridges.py`` も自動的にカバーする
      （ここではファイル存在のみ確認して検査対象に入っていることを固定する）
    """

    def test_bridges_module_exists_and_is_covered_by_tree_guardrails(self):
        assert (_CORE_DIR / "bridges.py").is_file()

    def test_bridges_reads_connected_refs_only(self):
        src = (_CORE_DIR / "bridges.py").read_text(encoding="utf-8")
        assert "connected_refs" in src
        assert "target_refs" not in src
        assert "'connected'" in src  # status='connected' 行のみを対象にする

    def test_bridges_uses_privacy_canon_without_redefining_k(self):
        src = (_CORE_DIR / "bridges.py").read_text(encoding="utf-8")
        assert "from core.privacy import" in src
        assert "K_ANONYMITY" in src
        assert "bucket_count_range" in src
        # k=3 のリテラル再定義（K_ANONYMITY = 3 / NAIVE_SIGNAL_... 相当）を持たない
        assert "K_ANONYMITY =" not in src
        assert "K_ANONYMITY=" not in src

    def test_bridges_response_uses_range_only(self):
        src = (_CORE_DIR / "bridges.py").read_text(encoding="utf-8")
        assert "learner_count_range" in src
        # 生カウント・関与人数の生値キーを応答に持たない
        assert_source_forbids(
            src, ['"count"', '"learners"'], context="core/personal_graph/bridges.py",
        )
        # 集約純粋部の応答組み立てに user_id キーが出ない（distinct 計数の内部利用のみ）
        aggregate_body = extract_function_source(src, "aggregate_entries")
        assert '"user_id"' not in aggregate_body
        assert "bucket_count_range(" in aggregate_body

    def test_admin_route_registered_with_teacher_gate(self):
        """教員向け GET /api/admin/courses/{course_id}/bridge-insights が
        interest-dashboard（B層教員向け集約）と同じ ``_require_teacher`` ゲートで
        routes/admin.py に登録されている。"""
        assert '"/courses/{course_id}/bridge-insights"' in _ADMIN_ROUTE_SRC
        body = extract_function_source(_ADMIN_ROUTE_SRC, "get_bridge_insights")
        assert "_require_teacher" in body
        assert "aggregate_bridge_candidates" in body

    def test_admin_route_response_carries_no_personal_fields(self):
        """応答組み立てに user_id・生カウントが無く、注記（評価利用禁止）を含む。"""
        body = extract_function_source(_ADMIN_ROUTE_SRC, "get_bridge_insights")
        assert_source_forbids(
            body, ['"user_id"', '"count"', '"learners"'],
            context="routes/admin.py:get_bridge_insights",
        )
        assert "評価利用は禁止" in body

    def test_no_learner_facing_bridge_endpoint(self):
        """学習者向け API を作らない（教員のみ。PN-1）—
        routes/learning.py・routes/personal_map.py に bridge-insights が無い。"""
        learning_src = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        assert "bridge-insights" not in learning_src
        assert "bridge-insights" not in _ROUTE_SRC


class TestMeRouterIsReadOnly:
    """Phase P-0.5（提案書 §5.3）: ``/api/me/personal-network...`` 正本 API も
    ``router``（コースビュー）と同じ読み取り専用制約を守る。"""

    def test_no_write_or_delete_endpoints_on_me_router(self):
        assert_source_forbids(
            _ROUTE_SRC,
            ["@me_router.post", "@me_router.put", "@me_router.patch", "@me_router.delete"],
            context="routes/personal_map.py (me_router)",
        )

    def test_me_router_registered_and_read_only_get(self):
        assert "me_router = APIRouter(prefix=\"/api/me\"" in _ROUTE_SRC
        assert '"/personal-network"' in _ROUTE_SRC
        assert '"/personal-network/journey"' in _ROUTE_SRC

    def test_no_user_id_path_parameter_covers_me_router(self):
        """既存 ``test_no_user_id_path_parameter``（``TestRouteIsPersonalScopeOnly``）が
        ``_ROUTE_SRC``（personal_map.py 全体のソース）を検査対象にしているため、
        ``router`` / ``me_router`` いずれに ``{user_id}`` パスパラメータを追加しても
        既存アサーションが自動的に検出する（本テストは意図の明示のみ）。"""
        assert "{user_id}" not in _ROUTE_SRC


class TestIncludeCandidateLinksFailsClosed:
    """PN-3/PN-6: 候補リンク（本人未確定情報）を個人ネットワークに含める opt-in を
    サーバ側が拒否する（提案書 §5.3 ``include_candidate_links=false`` の既定・強制）。"""

    def test_include_candidate_links_rejected_with_422(self):
        assert "include_candidate_links" in _ROUTE_SRC
        assert "422" in _ROUTE_SRC


class TestDeriveHandlesMapExcluded:
    """提案書 §6「地図には反映しない」操作の受け皿。derive.py が
    ``payload.map_excluded`` を読み、導出対象から外す（行削除ではない・P4）。"""

    def test_derive_source_processes_map_excluded_vocabulary(self):
        derive_src = (_CORE_DIR / "derive.py").read_text(encoding="utf-8")
        assert "map_excluded" in derive_src


class TestPersonScopeDeriveFunctionsExist:
    """Phase P-0.5 の本人スコープ導出関数が実装されていることの構造的固定
    （``routes/personal_map.py`` がこれらを import して呼ぶ契約）。"""

    def test_derive_exposes_person_scope_entrypoints(self):
        derive_src = (_CORE_DIR / "derive.py").read_text(encoding="utf-8")
        for name in ("def build_person_network", "def group_nodes_by_anchor", "def derive_person_network"):
            assert name in derive_src, f"derive.py is missing expected function: {name!r}"

    def test_route_uses_person_scope_entrypoints(self):
        assert "derive_person_network" in _ROUTE_SRC
        assert "group_nodes_by_anchor" in _ROUTE_SRC


class TestConnectTensionValidatesEdgeViewability:
    """N38（2026-07-17・予防的）: connect の ``edge_id`` にも component と同型の
    閲覧可否検証を追加。graph edge は独立テーブルを持たず
    ``theory_component_graphs.graph_json`` の edges[] 内にのみ存在するため、
    route 側（``routes/learning.py``）が JSONB containment で所属 document を解決し
    ``resolve_document_access`` で判定する。fail-closed（edge 不明・document 不明・
    閲覧不可はすべて拒否）だが、既存の connected 行には触らない。
    """

    def _learning_src(self) -> str:
        return (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")

    def test_connect_route_checks_edge_viewability(self):
        src = self._learning_src()
        body = src.split("def connect_tension_route")[1].split("\ndef ")[0]
        assert "_tension_connect_edge_viewable" in body

    def test_edge_viewability_helper_uses_resolve_document_access(self):
        src = self._learning_src()
        body = src.split("def _tension_connect_edge_viewable")[1].split("\ndef ")[0]
        assert "resolve_document_access" in body
        assert "theory_component_graphs" in body

    def test_edge_viewability_helper_is_fail_closed(self):
        """document を1件も解決できなければ False（安全側）に倒す。"""
        src = self._learning_src()
        body = src.split("def _tension_connect_edge_viewable")[1].split("\ndef ")[0]
        assert "if not document_ids:" in body
        assert "return False" in body


class TestConnectTensionEdgeViewabilityBehavior:
    """N38 の挙動レベル検証（DB 非接続・monkeypatch）。"""

    def _import_learning(self):
        import routes.learning as learning_mod
        return learning_mod

    class _FakeSession:
        def __init__(self, rows=None, raise_on_execute=False):
            self._rows = rows or []
            self._raise = raise_on_execute
            self.closed = False

        def execute(self, *a, **kw):
            if self._raise:
                raise RuntimeError("bad edge id")
            rows = self._rows

            class _Result:
                def fetchall(self_inner):
                    return rows

            return _Result()

        def close(self):
            self.closed = True

    def test_no_matching_graph_returns_false(self, monkeypatch):
        learning_mod = self._import_learning()
        fake = self._FakeSession(rows=[])
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: fake)
        assert learning_mod._tension_connect_edge_viewable("user1", "edge-x") is False
        assert fake.closed  # try/finally で必ず close（開発ルール4）

    def test_sql_error_returns_false(self, monkeypatch):
        learning_mod = self._import_learning()
        fake = self._FakeSession(raise_on_execute=True)
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: fake)
        assert learning_mod._tension_connect_edge_viewable("user1", "edge-x") is False
        assert fake.closed

    def test_viewable_document_returns_true(self, monkeypatch):
        learning_mod = self._import_learning()
        import services as services_mod

        fake = self._FakeSession(rows=[("doc-1",)])
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: fake)

        class _Access:
            can_view = True

        monkeypatch.setattr(services_mod, "resolve_document_access", lambda uid, did: _Access())
        assert learning_mod._tension_connect_edge_viewable("user1", "edge-x") is True

    def test_unviewable_document_returns_false(self, monkeypatch):
        learning_mod = self._import_learning()
        import services as services_mod

        fake = self._FakeSession(rows=[("doc-1",)])
        monkeypatch.setattr(learning_mod, "_pg_session", lambda: fake)

        class _Access:
            can_view = False

        monkeypatch.setattr(services_mod, "resolve_document_access", lambda uid, did: _Access())
        assert learning_mod._tension_connect_edge_viewable("user1", "edge-x") is False
