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
