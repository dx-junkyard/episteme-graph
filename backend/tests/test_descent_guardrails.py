"""構造の降下路（Phase 3 — 足場ダイヤル・楽屋 v1）のガードレール。

正本: docs/features/structure_descent_design.md §1/§5/§6（SD1〜SD6）。

- SD2: core/descent/ が fastapi / core.llm / routes / services を import しない（非LLM・決定論）
- SD1/G4: 誘導語彙 denylist（「降りるべき」「今すぐ」「おすすめ」）が core と route に無い
- SD3/SD5: routes/descent.py は GET のみ（書き込みデコレータなし）・"{user_id}" なし・
  core/descent と route に INSERT / UPDATE が無い（閲覧を記録しない・数えない）
- SD4: 痕跡登録簿の backstage_question 宣言（learner_trajectory=True /
  teacher_dashboard=False / personal_map=False / statuses ⊆ _TRACE_STATUSES）
- §6 精査記録②: learning.py の楽屋ガード（_trace_kind 分岐・backstage で tension_hint を
  立てない・tension mining をスケジュールしない）がソース構造として存在する
- services.aggregate_interest_dashboard の除外リテラルに 'backstage_question' が現れる
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.trace_registry import (  # noqa: E402
    DASHBOARD_EXCLUDED_KINDS,
    PERSONAL_MAP_KINDS,
    TRACE_KINDS,
    TRAJECTORY_EXCLUDED_KINDS,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
    extract_function_source,
)

DESCENT_DIR = BACKEND / "core" / "descent"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "descent.py").read_text(encoding="utf-8")
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")

# SD1/G4: システムからの降下誘導・煽りを示す語彙（設計書 §5 の denylist）。
STEERING_WORDS = ("降りるべき", "今すぐ", "おすすめ")


# ===========================================================================
# 1. core/descent の隔離（SD2: 非LLM・非FastAPI・routes/services 非依存）
# ===========================================================================


class TestDescentCoreIsolation:
    def test_core_descent_does_not_import_frameworks_or_llm(self):
        assert_module_tree_does_not_import(
            DESCENT_DIR,
            ["fastapi", "core.llm", "openai", "routes", "services"],
        )

    def test_core_descent_exists_with_expected_modules(self):
        names = {p.name for p in DESCENT_DIR.glob("*.py")}
        assert {"__init__.py", "engine.py", "resolve.py"} <= names


# ===========================================================================
# 2. 誘導語彙 denylist（SD1/G4: 「降りるべきだ」と誘導しない）
# ===========================================================================


class TestSteeringVocabularyDenylist:
    def test_core_descent_has_no_steering_words(self):
        assert_module_tree_forbids(DESCENT_DIR, STEERING_WORDS)

    def test_route_has_no_steering_words(self):
        assert_source_forbids(
            _ROUTE_SRC, STEERING_WORDS, context="api/routes/descent.py"
        )


# ===========================================================================
# 3. 読み取り専用（SD3/SD5: 閲覧を記録しない・数えない）
# ===========================================================================


class TestReadOnly:
    def test_route_has_no_write_decorators_and_no_user_id_param(self):
        assert_source_forbids(
            _ROUTE_SRC,
            [
                "@learning_router.post",
                "@learning_router.put",
                "@learning_router.patch",
                "@learning_router.delete",
                "{user_id}",
            ],
            context="api/routes/descent.py",
        )

    def test_core_descent_has_no_write_sql(self):
        """SD5: 開示履歴・使用数を書かない — INSERT / UPDATE 文字列自体が無い。"""
        assert_module_tree_forbids(DESCENT_DIR, ["INSERT", "UPDATE", "DELETE FROM"])

    def test_route_has_no_write_sql(self):
        assert_source_forbids(
            _ROUTE_SRC,
            ["INSERT", "UPDATE", "DELETE FROM"],
            context="api/routes/descent.py",
        )


# ===========================================================================
# 4. 痕跡登録簿の backstage_question 宣言（SD4 / TR1）
# ===========================================================================


class TestBackstageQuestionRegistryDeclaration:
    def test_kind_is_registered(self):
        assert "backstage_question" in TRACE_KINDS

    def test_exposure_declaration_matches_design(self):
        """SD4: 本人には見える（問いの軌跡）が、教員集約・わたしの地図には出ない。"""
        spec = TRACE_KINDS["backstage_question"]
        assert spec.learner_trajectory is True
        assert spec.teacher_dashboard is False
        assert spec.personal_map is False

    def test_derived_frozensets_reflect_the_declaration(self):
        assert "backstage_question" in DASHBOARD_EXCLUDED_KINDS
        assert "backstage_question" not in TRAJECTORY_EXCLUDED_KINDS
        assert "backstage_question" not in PERSONAL_MAP_KINDS

    def test_statuses_are_subset_of_trace_statuses(self):
        from api import services

        spec = TRACE_KINDS["backstage_question"]
        allowed = set(services._TRACE_STATUSES)
        assert spec.statuses <= allowed, (
            "backstage_question の statuses に _TRACE_STATUSES 外の語彙がある: "
            f"{sorted(spec.statuses - allowed)}"
        )


# ===========================================================================
# 5. learning.py の楽屋ガード（§6 精査記録②: 送信側で構造的に除外する）
# ===========================================================================


class TestLearningBackstageGuard:
    def test_trace_kind_branches_to_backstage_question(self):
        assert '"backstage_question" if _is_backstage' in _LEARNING_SRC, (
            "learning.py の _trace_kind から backstage 分岐が消えている"
            "（楽屋の質問が kind='question' で記録され集計に混入する。SD4）"
        )

    def test_tension_hint_is_forced_false_for_backstage(self):
        assert "False if _is_backstage else judge_tension_hint" in _LEARNING_SRC, (
            "learning.py の楽屋 tension_hint ガードが消えている"
            "（tension worker は payload_flag 方式のため kind では自動除外されない。"
            "§6 精査記録②）"
        )

    def test_tension_mining_is_not_scheduled_for_backstage(self):
        snippet = (
            "if _tension_hint and not _is_backstage:\n"
            '        maybe_schedule_tension_mining('
        )
        assert snippet in _LEARNING_SRC, (
            "learning.py の tension mining スケジュールから backstage ガードが"
            "消えている（二重防御の明示ガード。SD4 / §6 精査記録②）"
        )

    def test_backstage_payload_key_is_written_only_for_backstage(self):
        assert '**({"backstage": True} if _is_backstage else {})' in _LEARNING_SRC, (
            "learning.py の痕跡 payload から backstage キーの条件付き焼き込みが"
            "消えている（本人の台帳表示・後方検証用。§4）"
        )

    def test_entry_mode_discuss_is_not_baked_into_backstage_traces(self):
        """2026-08-15 レビュー是正 Fix 2: discuss 観測基盤（core/discuss/observation.py）は
        kind フィルタなしで payload->>'entry_mode'='discuss' を数えるため、楽屋の痕跡に
        entry_mode を焼き込むと SD4（楽屋は集計に入らない）に反して混入する。"""
        snippet = (
            '**({"entry_mode": "discuss"} if _is_discuss and not _is_backstage else {})'
        )
        assert snippet in _LEARNING_SRC, (
            "learning.py の entry_mode 焼き込みから backstage ガードが消えている"
            "（楽屋の質問が discuss 観測基盤に数えられてしまう。SD4）"
        )

    def test_backstage_is_front_loaded_and_clears_typed_action_and_atlas(self):
        """2026-08-15 レビュー是正 Fix 3: _is_backstage の判定はハンドラ冒頭
        （EXPLAIN_GRAPH_ELEMENT / atlas の early-return 記録経路より前）で行い、
        backstage のとき typed action と atlas_context を無視する（サーバ側防御）。"""
        snippet = (
            "    _is_backstage = bool(body.backstage)\n"
            "    if _is_backstage:\n"
            "        body.action = None\n"
            "        body.atlas_context = None\n"
        )
        assert snippet in _LEARNING_SRC, (
            "learning.py の楽屋前倒しガード（body.action / body.atlas_context の無効化）"
            "が消えている（楽屋の質問が kind='question' の early-return 記録経路に"
            "流れ得る。SD4）"
        )
        # 前倒しの位置検査: 判定は EXPLAIN_GRAPH_ELEMENT 分岐・atlas 応答分岐より前。
        guard_pos = _LEARNING_SRC.index(snippet)
        assert guard_pos < _LEARNING_SRC.index('if body.action == "EXPLAIN_GRAPH_ELEMENT"'), (
            "楽屋ガードが EXPLAIN_GRAPH_ELEMENT 分岐より後ろにある"
        )
        assert guard_pos < _LEARNING_SRC.index(
            "_atlas_response = _atlas_action_response("
        ), "楽屋ガードが atlas アクション応答より後ろにある"

    def test_anchor_confirm_is_not_offered_in_backstage(self):
        """2026-08-15 レビュー是正 Fix 4: 「集計に入りません」と宣言した枠で
        帰属確定 UI（anchor_confirm）を出さない（SD4）。"""
        snippet = (
            "        _trace_id\n"
            "        and not _is_casual\n"
            "        and not _is_backstage\n"
        )
        assert snippet in _LEARNING_SRC, (
            "learning.py の anchor_confirm 条件から backstage ガードが消えている"
            "（楽屋で帰属確認カードが出てしまう。SD4）"
        )


# ===========================================================================
# 6. 教員向け集約からの除外（SD4: 楽屋は集計に入らない）
# ===========================================================================


class TestDashboardExclusion:
    def test_aggregate_interest_dashboard_excludes_backstage_question(self):
        body = extract_function_source(_SERVICES_SRC, "aggregate_interest_dashboard")
        assert "'backstage_question'" in body, (
            "aggregate_interest_dashboard の除外リテラルに 'backstage_question' が"
            "現れない（楽屋の質問が教員向け集団集計に数えられてしまう。SD4）"
        )


# ===========================================================================
# 7. 楽屋の質問は unanswered_query_logs にも残さない（2026-09-05 ビジョン監査 C1）
# ===========================================================================


class TestBackstageUnansweredQueryGuard:
    """楽屋（backstage）の質問は本人専用（SD4 / vision 原則5）。

    `unanswered_query_logs` は `interest_traces` とは別テーブルで、痕跡登録簿
    （core/trace_registry.py）の除外機構が届かない。ここに書かれた質問原文は
    `GET /api/admin/courses/{id}/unanswered-queries` から氏名付きで教員に表示される
    ため、`_learning_chat_core` の RAG ゼロ件経路は楽屋のとき記録してはならない。
    """

    def test_every_unanswered_query_log_call_is_guarded_by_backstage(self):
        import re

        body = extract_function_source(_LEARNING_SRC, "_learning_chat_core")
        calls = [m.start() for m in re.finditer(r"log_unanswered_query\(", body)]
        assert calls, "_learning_chat_core に log_unanswered_query 呼び出しが見つからない"
        for pos in calls:
            # 直前の非空行が楽屋ガードであること（if not _is_backstage: の直下で呼ぶ）。
            preceding = [ln.strip() for ln in body[:pos].splitlines() if ln.strip()]
            assert preceding and preceding[-1] == "if not _is_backstage:", (
                "log_unanswered_query が楽屋ガード（if not _is_backstage:）の直下以外で"
                "呼ばれている（楽屋の質問原文が教員の「未回答の質問」表に氏名付きで出る。"
                "SD4 / 原則5）"
            )
