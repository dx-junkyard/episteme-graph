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
)

_CORE_DIR = BACKEND / "core" / "personal_graph"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "personal_map.py").read_text(encoding="utf-8")

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
