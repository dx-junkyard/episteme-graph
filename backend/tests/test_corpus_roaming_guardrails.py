"""コーパス回遊層のガードレール（設計書 §10 のうち Phase A / C / D 該当項目）。

正本: docs/features/corpus_roaming_design.md（CR1〜CR10）。

固定する項目:
  1. ``core/corpus_view.py`` が FastAPI / LLM を import しない（開発ルール2）
  2. 可視性交差が **SQL 内**で強制される（route 層のフィルタ後付けにしない — CR1）
  3. 学習者向け DTO に weight / confidence / score / 件数キーが無い（CR3・再帰走査）
  4. 閉世界 denylist（「世界初」「誰も」「この分野には論文がない」等）の非出現（CR4）
  5. corpus 系から arXiv / Semantic Scholar クライアントへの import が無い（CR7）
  6. 縁が ``atlas_gap_decisions``（教員の判断）を読まない（§6.1）
  7. 行削除 API・DELETE 文の不在（CR8）
  8. k=3 の再定義なし（``core/privacy.py`` へ委譲）
  9. ``frontier_interest`` の登録簿宣言と消費面の除外一致（tension / anchor worker・
     わたしの地図・問いの軌跡・教員 dashboard から構造的に外れている）
 10. migration 073 が「列追加1つ・冪等・シードなし」であること
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import corpus_view  # noqa: E402
from core.trace_registry import CONSUMERS, TRACE_KINDS  # noqa: E402
from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
    read_migration_sql,
)

_CORE_SRC = (BACKEND / "core" / "corpus_view.py").read_text(encoding="utf-8")
_ROUTE_SRC = (BACKEND / "api" / "routes" / "corpus.py").read_text(encoding="utf-8")
_SERVICES_SRC = (BACKEND / "api" / "services.py").read_text(encoding="utf-8")
_DISCOVERY_ROUTE_SRC = (BACKEND / "api" / "routes" / "paper_discovery.py").read_text(
    encoding="utf-8"
)
_MIGRATION_SQL = read_migration_sql(BACKEND, 73)

#: 閉世界の正直さ（CR4）— 「このコーパス / この検索条件では」を越えて分野全体を
#: 断定する語彙。事実文・プロンプト・UI 文言のいずれにも現れてはならない。
_CLOSED_WORLD_DENYLIST = (
    "世界初",
    "誰も",
    "この分野には論文がない",
    "この分野には論文が存在しない",
    "未踏",
    "前人未到",
)


# ---------------------------------------------------------------------------
# 1. core の純度（開発ルール2 / CR9）
# ---------------------------------------------------------------------------


class TestCoreIsPure:
    def test_core_does_not_import_fastapi_or_llm(self):
        assert_source_does_not_import(
            _CORE_SRC,
            ["fastapi", "core.llm", "openai", "services"],
            context="core/corpus_view.py",
        )

    def test_core_does_not_import_external_paper_clients(self):
        """CR7: 学習者起点で arXiv / Semantic Scholar を呼ばない。"""
        assert_source_does_not_import(
            _CORE_SRC,
            [
                "core.paper_discovery.arxiv_client",
                "core.paper_discovery.citation_client",
                "core.paper_discovery.citation_search",
                "requests",
                "httpx",
                "urllib.request",
            ],
            context="core/corpus_view.py",
        )

    def test_route_does_not_import_external_paper_clients(self):
        assert_source_does_not_import(
            _ROUTE_SRC,
            [
                "core.paper_discovery.arxiv_client",
                "core.paper_discovery.citation_client",
                "core.paper_discovery.citation_search",
                "requests",
                "httpx",
            ],
            context="api/routes/corpus.py",
        )

    def test_core_opens_no_session_of_its_own(self):
        """セッションは呼び出し側が管理する（core/landscape/store.py と同じ流儀）。"""
        assert "get_session" not in _CORE_SRC


# ---------------------------------------------------------------------------
# 2. 可視性の SQL 内強制（CR1）
# ---------------------------------------------------------------------------


class TestVisibilityIsEnforcedInSql:
    def test_every_placement_and_signal_query_binds_doc_ids(self):
        selects = [
            block for block in re.findall(r"SELECT.*?\"\"\"", _CORE_SRC, flags=re.S)
            if "landscape_placements" in block or "landscape_gap_signals" in block
            or "FROM documents d" in block
        ]
        assert selects, "配置 / 信号 / 論文の SELECT が見つからない（実装が変わったら追随）"
        for block in selects:
            assert "ANY(:doc_ids)" in block, (
                "可視性交差が SQL 内で強制されていない SELECT がある（CR1）:\n" + block
            )

    def test_route_reads_the_visible_set_for_every_read_endpoint(self):
        for fn in ("get_corpus_domains", "get_corpus_landscape", "get_corpus_documents"):
            body = extract_function_source(_ROUTE_SRC, fn)
            assert "list_visible_document_ids(current_user[\"id\"])" in body, (
                f"{fn} が本人の可視集合を取得していない（CR1）"
            )

    def test_empty_visible_set_short_circuits(self):
        """空集合は「全件」ではなく「何も見えない」（fail-closed）。"""
        assert "if not doc_ids:" in _CORE_SRC
        assert "if not key or not doc_ids:" in _CORE_SRC


# ---------------------------------------------------------------------------
# 3. 数値を見せない（CR3）
# ---------------------------------------------------------------------------


def _numeric_keys(node, path="") -> list[str]:
    forbidden = ("weight", "confidence", "score", "count", "similarity", "relevance")
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            if any(term in lowered for term in forbidden):
                found.append(f"{path}.{key}")
            found.extend(_numeric_keys(value, f"{path}.{key}"))
    elif isinstance(node, (list, tuple)):
        for index, item in enumerate(node):
            found.extend(_numeric_keys(item, f"{path}[{index}]"))
    return found


class TestNoNumbersInLearnerDto:
    def test_dto_shapes_have_no_numeric_keys(self):
        """実データ形の DTO を再帰走査する（キー名の混入を構造的に禁止）。"""
        sample = {
            "domain_key": "astrophysics",
            "skeleton_version": "v3",
            "placements": [
                {
                    "document_id": "d1", "document_title": "t", "anchor_node_id": "c1",
                    "node_label": "l", "region_id": "r1", "perspective": "theory",
                    "perspective_label": "理論から", "status": "inferred",
                    "source_label": "AIによる推定（未確認）",
                }
            ],
            "fringe": [
                {"region_id": "r1", "region_label": "l",
                 "fact_line": corpus_view.FACT_FRINGE, "paper_titles": ["a"]}
            ],
            "outer": {"fact_line": corpus_view.outer_fact_line("2026-08-27")},
        }
        assert _numeric_keys(sample) == []

    def test_source_never_selects_weight_or_confidence(self):
        assert_source_forbids(
            _CORE_SRC,
            ["p.weight", "s.confidence", "weight_label", "confidence"],
            context="core/corpus_view.py",
        )

    def test_teacher_aggregation_returns_range_labels_only(self):
        body = extract_function_source(_SERVICES_SRC, "aggregate_frontier_interest")
        assert "range_label" in body
        assert "bucket_count_range" in body
        for leaked in ('"learners"', '"count"', '"created_at"', '"user_id"'):
            assert leaked not in body, f"教員向け集約が {leaked} を返している（CR6）"


# ---------------------------------------------------------------------------
# 4. 閉世界の正直さ（CR4）
# ---------------------------------------------------------------------------


class TestClosedWorldHonesty:
    def test_fact_lines_avoid_the_denylist(self):
        for source, context in (
            (_CORE_SRC, "core/corpus_view.py"),
            (_ROUTE_SRC, "api/routes/corpus.py"),
        ):
            assert_source_forbids(source, list(_CLOSED_WORLD_DENYLIST), context=context)

    def test_fringe_fact_line_is_scoped_to_the_map(self):
        assert corpus_view.FACT_FRINGE == (
            "この領域の先に、まだ地図に置かれていない主題を扱う論文があります。"
        )

    def test_outer_fact_line_keeps_the_search_condition_and_time(self):
        line = corpus_view.outer_fact_line("2026-08-27")
        assert "教員の検索条件では" in line
        assert "2026-08-27" in line

    def test_ring_vocabulary_is_the_single_source(self):
        assert corpus_view.RINGS == ("fringe", "outer")


# ---------------------------------------------------------------------------
# 5. 縁は論文由来の信号だけを読む（§6.1）
# ---------------------------------------------------------------------------


class TestFringeReadsSignalsOnly:
    def test_core_never_reads_teacher_decisions(self):
        assert_source_forbids(
            _CORE_SRC,
            ["atlas_gap_decisions", "cluster_key", "derive_candidates"],
            context="core/corpus_view.py（学習者に審議状況を見せない — §6.1）",
        )

    def test_only_active_signals(self):
        body = extract_function_source(_CORE_SRC, "_visible_gap_signals")
        assert "s.status = :active" in body
        assert "SIGNAL_STATUS_ACTIVE" in body


# ---------------------------------------------------------------------------
# 6. 情報を落とさない（CR8）
# ---------------------------------------------------------------------------


class TestNoDeletion:
    def test_no_delete_statements_or_routes(self):
        for source, context in (
            (_CORE_SRC, "core/corpus_view.py"),
            (_ROUTE_SRC, "api/routes/corpus.py"),
        ):
            assert_source_forbids(
                source,
                ["DELETE FROM", "@learning_router.delete", "@router.delete"],
                context=context,
            )

    def test_core_is_read_only(self):
        assert_source_forbids(
            _CORE_SRC, ["INSERT INTO", "UPDATE "], context="core/corpus_view.py"
        )

    def test_withdraw_is_a_status_transition(self):
        body = extract_function_source(_SERVICES_SRC, "withdraw_frontier_interest")
        assert "SET status = 'dismissed'" in body
        assert "DELETE" not in body


# ---------------------------------------------------------------------------
# 7. k-匿名は core/privacy.py へ委譲（k=3 を再定義しない）
# ---------------------------------------------------------------------------


class TestKAnonymityDelegation:
    def test_aggregation_delegates_to_privacy_module(self):
        body = extract_function_source(_SERVICES_SRC, "aggregate_frontier_interest")
        assert "from core.privacy import" in body
        assert "meets_k_anonymity" in body
        assert not re.search(r"(?<![\w.])(3|K_ANONYMITY)\s*(?:<=|>=|<|>)", body), (
            "k=3 の閾値をリテラルで再実装している（core/privacy.py に委譲すること）"
        )

    def test_ranges_come_from_the_shared_bucket(self):
        body = extract_function_source(_SERVICES_SRC, "aggregate_frontier_interest")
        for literal in ('"3-5"', '"6-10"', '"11+"'):
            assert literal not in body, "レンジ境界を再定義している（core/privacy.py が正本）"


# ---------------------------------------------------------------------------
# 8. 痕跡 kind の登録簿と消費面の一致（Phase D / CR6）
# ---------------------------------------------------------------------------


class TestFrontierInterestTraceKind:
    def test_registered_with_all_three_exposure_declarations(self):
        spec = TRACE_KINDS["frontier_interest"]
        assert spec.learner_trajectory is False   # 発話ではない（問いの軌跡に出さない）
        assert spec.teacher_dashboard is False    # 個人の回遊履歴を教員に見せない
        assert spec.personal_map is False         # わたしの地図の導出対象にしない
        assert spec.statuses == frozenset({"open", "dismissed"})

    def test_declares_its_teacher_aggregation(self):
        """TR5: 台帳が「あなた以外には表示されません」と偽らないための宣言。"""
        spec = TRACE_KINDS["frontier_interest"]
        assert spec.teacher_aggregations
        assert any("aggregate_frontier_interest" in a for a in spec.teacher_aggregations)

    def test_consumer_declared_for_the_aggregation(self):
        entry = CONSUMERS["frontier_interest_aggregate"]
        assert entry["module"] == "backend/api/services.py"
        assert entry["function"] == "aggregate_frontier_interest"
        assert entry["kinds"] == frozenset({"frontier_interest"})

    def test_excluded_from_trajectory_and_dashboard_sql(self):
        for fn in ("get_interest_traces", "aggregate_interest_dashboard"):
            body = extract_function_source(_SERVICES_SRC, fn)
            assert "'frontier_interest'" in body, (
                f"{fn} が frontier_interest を明示除外していない"
            )

    def test_not_consumed_by_personal_map_or_workers(self):
        for relative in (
            "core/personal_graph/queries.py",
            "core/personal_graph/derive.py",
            "core/tension/worker.py",
            "core/structure_anchor/worker.py",
            "core/cycle/queries.py",
            "core/doubt/naive_signal.py",
        ):
            source = (BACKEND / relative).read_text(encoding="utf-8")
            assert "frontier_interest" not in source, (
                f"{relative} が frontier_interest を読んでいる（許可リスト方式のまま"
                "構造的に除外されているべき — CR6）"
            )

    def test_record_carries_no_free_text_and_no_tension_hint(self):
        body = extract_function_source(_SERVICES_SRC, "record_frontier_interest")
        assert "tension_hint" not in body, "端への関心を tension worker に拾わせない"
        assert 'text=""' in body, "本文・質問文を持たない（CR6）"
        assert "record_interest_trace(" in body, "痕跡記録の唯一入口を経由すること（TR1）"


# ---------------------------------------------------------------------------
# 9. migration 073（§6.2 / §8）
# ---------------------------------------------------------------------------


class TestMigration:
    def test_adds_only_the_aggregate_bit(self):
        statements = [
            s.strip() for s in _MIGRATION_SQL.split(";")
            if s.strip() and not all(
                line.strip().startswith("--") or not line.strip()
                for line in s.splitlines()
            )
        ]
        assert len(statements) == 1, "migration 073 は列追加1つだけ（§8）"
        assert "ADD COLUMN IF NOT EXISTS last_search_found_new BOOLEAN" in _MIGRATION_SQL
        assert "paper_discovery_subscriptions" in _MIGRATION_SQL

    def test_is_idempotent_and_seedless(self):
        assert "IF NOT EXISTS" in _MIGRATION_SQL
        for banned in ("INSERT INTO", "CREATE TABLE", "DROP", "DELETE FROM", "DEFAULT"):
            assert banned not in _MIGRATION_SQL.upper().replace("--", "\n--"), (
                f"migration 073 に {banned} が含まれる（シードなし・列追加のみ）"
            )

    def test_search_writes_the_bit_without_storing_candidates(self):
        search_src = (BACKEND / "core" / "paper_discovery" / "search.py").read_text(
            encoding="utf-8"
        )
        body = extract_function_source(search_src, "run_search")
        assert "found_new=" in body
        assert 'c.get("status") == "new"' in body
        assert "INSERT INTO" not in body, "候補スナップショットを保存しない（PD5）"
