"""論文レーダーのガードレール（設計書 ``paper_radar_design.md`` §7）。

ここで固定するのは**構造**であり、振る舞いの検査は ``test_paper_radar_core.py`` /
``test_paper_radar_api.py`` 側。

検査項目（不変条項 PR1〜PR8 のうち構造で守れるもの）:

- ``radar.py`` / ``compare.py`` が FastAPI を import しない。``radar.py`` は
  ``core.llm`` にも触れない（LLM 接触点は ranking.py（embedding）と compare.py
  （比較文）の2本だけ — 既存 allowlist の改訂と対）
- 比較分析のプロンプト制約文（違い3文 + 重なり3文 + 部品リスト見出し）が原文で存在し、
  ``caveat`` がサーバ側定数である（PR4）。重なりの追加で **LLM コールは増えない**
- ``fetch_by_ids`` がスロットルを通り、宛先が定数ホストである（PR6）
- レーダー経路が購読・見送りへ書き込まない（``touch_last_checked`` を呼ばない — PR5）
- 探索経路（``resolve_seed`` / ``run_radar_search``）が DB へ書き込まず、書き込みは
  arXiv 出所の後付け記帳（``register_arxiv_provenance`` の ``UPDATE documents``）
  1箇所に閉じている（PR1 — 推定は推定のまま保存しない）
- レーダー専用の取得・取り込みエンドポイントが無い（PR3。``/radar/provenance`` は
  既存教材の出所を記帳するだけで、取得・受理経路に触れない）
- ``/api/learning`` 配下にレーダー系ルートが無い（PR8）
- migration ディレクトリにレーダーの採番が無い（PR1 — 新テーブル・新列ゼロ）
- 取り込み worker がレーダーを import しない（PR5 — 自動探索の経路を作らない）
- 距離帯のラベル文字列・閾値が ``core/label_vocab.py`` 以外に直書きされていない（PR2）
- ``band_candidates`` が未測定（``None``）を段階ラベルへ通さない（PR2）
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api")):
    if _path not in sys.path:
        sys.path.insert(0, str(_path))

from tests.guardrail_helpers import (  # noqa: E402
    assert_source_does_not_import,
    assert_source_forbids,
    extract_function_source,
)

CORE_DIR = BACKEND / "core" / "paper_discovery"
ROUTE_SOURCE = BACKEND / "api" / "routes" / "paper_discovery.py"
WORKER_SOURCE = BACKEND / "api" / "ingest_worker.py"
LABEL_VOCAB_SOURCE = BACKEND / "core" / "label_vocab.py"

_RADAR_SRC = (CORE_DIR / "radar.py").read_text(encoding="utf-8")
_COMPARE_SRC = (CORE_DIR / "compare.py").read_text(encoding="utf-8")
_RANKING_SRC = (CORE_DIR / "ranking.py").read_text(encoding="utf-8")
_ROUTE_SRC = ROUTE_SOURCE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. core の独立性（開発ルール2 / LLM 接触点）
# ---------------------------------------------------------------------------


class TestCoreIsolation:
    def test_files_exist(self):
        assert (CORE_DIR / "radar.py").is_file()
        assert (CORE_DIR / "compare.py").is_file()

    def test_radar_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _RADAR_SRC, ["fastapi"], context="core/paper_discovery/radar.py"
        )

    def test_compare_does_not_import_fastapi(self):
        assert_source_does_not_import(
            _COMPARE_SRC, ["fastapi"], context="core/paper_discovery/compare.py"
        )

    def test_radar_does_not_touch_the_llm_layer(self):
        """PR: seed 解決・クエリ組み立ては LLM 0回（embedding は ranking 経由）。"""
        assert_source_forbids(
            _RADAR_SRC,
            [
                "import core.llm",
                "from core.llm",
                "import openai",
                "from openai",
                "generate_text",
                "generate_embeddings",
            ],
            context="core/paper_discovery/radar.py",
        )

    def test_compare_is_the_only_text_llm_and_is_metered(self):
        """比較文の生成は compare.py だけ。U層計測（U3）も同時に固定する。"""
        assert "generate_text_with_structured_output" in _COMPARE_SRC
        assert 'FEATURE_RADAR_COMPARE = "discovery:compare"' in _COMPARE_SRC
        assert "usage_context(" in _COMPARE_SRC

        from core.llm_usage.schema import KNOWN_FEATURES

        assert "discovery:compare" in KNOWN_FEATURES

    def test_compare_feature_is_registered_in_the_model_policy(self):
        """M層3点同時登録（KNOWN_FEATURES / scene / env マッピング）。"""
        from core import llm_policy

        scene = llm_policy.scene_for_feature("discovery:compare")
        assert scene is not None and scene in llm_policy.SCENES
        assert llm_policy._FEATURE_ENV_SETTINGS["discovery:compare"] == (  # noqa: SLF001
            "discovery_compare_llm_model",
            "fast",
        )

    def test_compare_does_not_hold_the_daily_gate(self):
        """コスト上限は route 層（figure_suggest と同じ配置）。"""
        assert_source_forbids(
            _COMPARE_SRC,
            ["CostGate()", "check_and_count", "max_calls_per_day"],
            context="core/paper_discovery/compare.py",
        )
        assert "_radar_compare_gate = CostGate()" in _ROUTE_SRC


# ---------------------------------------------------------------------------
# 2. 比較文の捏造ガードと出所（PR4）
# ---------------------------------------------------------------------------


class TestCompareGuards:
    _REQUIRED_PROMPT_CONSTRAINTS = (
        "アブストラクトに書かれていることだけを比較する",
        "断定せず推量形で書く",
        "数値スコア・優劣の評価を書かない",
        # 重なり2区画（overlaps）— 閉世界の部品リストと逐語引用を原文で固定する。
        "- 重なり（overlaps）は、起点論文と同じ内容を別の表現・文脈で扱っていそうな箇所だけを挙げる（最大3件）",
        "- overlaps の component_label は、下の部品リストにある名前をそのまま使う（リストに無い重なりは component_label を空にする）",
        "- 各重なりにも、その候補のアブストラクトからの逐語引用を evidence_quote として付ける",
        "【起点論文の部品リスト】",
    )

    def test_prompt_constraints_exist_verbatim(self):
        for phrase in self._REQUIRED_PROMPT_CONSTRAINTS:
            assert phrase in _COMPARE_SRC, phrase

    def test_overlap_constraints_stay_under_the_hedging_rule(self):
        """重なりの指示が「断定せず推量形で書く」より後ろにある（制約の射程に入る）。"""
        hedge = _COMPARE_SRC.index("断定せず推量形で書く")
        assert _COMPARE_SRC.index("- 重なり（overlaps）は、") > hedge

    def test_overlap_quotes_are_checked_verbatim_and_labels_are_closed_world(self):
        """重なりも differences と同じ逐語ガード。部品名はリスト外なら空へ（P4）。"""
        validator = extract_function_source(_COMPARE_SRC, "validate_items")
        assert "for overlap in entry.overlaps or []:" in validator
        # 逐語検査は differences と同一規則（2箇所とも同じ式）。
        assert validator.count("normalize_for_quote_match(quote) not in haystack") == 2
        # リスト外の名前は空文字へ落とす（項目は drop しない）。
        assert '"component_label": label_by_key.get(label.casefold(), "")' in validator

    def test_caveat_is_a_server_side_constant(self):
        from core.paper_discovery.compare import CAVEAT

        assert CAVEAT == (
            "アブストラクト（要旨）の比較に基づく AI の推定です。本文は確認されていません。"
        )
        # 各項目に付けるのは validator（LLM 出力に依存しない）。
        validator = extract_function_source(_COMPARE_SRC, "validate_items")
        assert '"caveat": CAVEAT' in validator

    def test_evidence_quote_is_checked_verbatim(self):
        validator = extract_function_source(_COMPARE_SRC, "validate_items")
        assert "normalize_for_quote_match(quote) not in haystack" in validator

    def test_candidate_abstracts_are_refetched_by_the_server(self):
        """PR6: クライアントから要旨本文を受け取らない（verbatim 検査の土台）。"""
        run_compare = extract_function_source(_COMPARE_SRC, "run_compare")
        assert "arxiv_client.fetch_by_ids" in run_compare
        assert "summary" not in extract_function_source(_ROUTE_SRC, "radar_compare")

    def test_overlaps_do_not_add_an_llm_call(self):
        """重なりは**同一コールの出力拡張**（1リクエスト = 1コールを崩さない）。"""
        # 生成の呼び出し口は _call_llm 1本（import 行 + 呼び出し1回）。
        assert _COMPARE_SRC.count("generate_text_with_structured_output(") == 1
        assert extract_function_source(_COMPARE_SRC, "run_compare").count("_call_llm(") == 1

    def test_compare_results_are_not_persisted(self):
        assert_source_forbids(
            _COMPARE_SRC,
            ["INSERT", "UPDATE", "DELETE", "commit("],
            context="core/paper_discovery/compare.py",
        )


# ---------------------------------------------------------------------------
# 3. arXiv への行儀（PR6）
# ---------------------------------------------------------------------------


class TestArxivClientAddition:
    def test_fetch_by_ids_goes_through_the_shared_http_helper(self):
        src = (CORE_DIR / "arxiv_client.py").read_text(encoding="utf-8")
        fetch = extract_function_source(src, "fetch_by_ids")
        assert "_http_get(params, timeout)" in fetch
        # 宛先を渡せる引数を作らない（URL は _api_url() の定数ホストのまま）。
        assert_source_forbids(fetch, ["http://", "https://"], context="fetch_by_ids")
        assert "_throttle()" in extract_function_source(src, "_http_get")


# ---------------------------------------------------------------------------
# 4. 副作用ゼロ（PR3 / PR5）
# ---------------------------------------------------------------------------


class TestNoSideEffects:
    def test_radar_does_not_write_subscriptions_or_dismissals(self):
        assert_source_forbids(
            _RADAR_SRC,
            [
                "touch_last_checked",
                "upsert_subscription",
                "dismissed_ids",
                "store.dismiss",
                "store.restore",
                "INSERT",
                "DELETE",
            ],
            context="core/paper_discovery/radar.py",
        )

    def test_the_only_write_is_the_provenance_registration(self):
        """PR1: 探索は読むだけ。書き込みは出所の後付け記帳1箇所に閉じる。

        推定（ファイル名からの arXiv ID）を seed 解決の副作用で保存しない
        （「推定」を勝手に「登録済み」へ昇格させない）ことを構造で固定する。
        """
        for fn_name in ("_document_row", "seed_keyphrase_candidates", "resolve_seed",
                        "run_radar_search"):
            assert_source_forbids(
                extract_function_source(_RADAR_SRC, fn_name),
                ["INSERT", "UPDATE", "DELETE", "commit("],
                context=f"core/paper_discovery/radar.{fn_name}",
            )
        registration = extract_function_source(_RADAR_SRC, "register_arxiv_provenance")
        assert "UPDATE documents" in registration
        # 空のときだけ上書きする（登録済みの出所を推定で塗り替えない）。
        assert "COALESCE(source_url, '') = ''" in registration
        assert _RADAR_SRC.count("UPDATE ") == 1
        # commit は呼び出し側（route）の責務（core はトランザクションを閉じない）。
        assert_source_forbids(
            registration, ["commit("], context="register_arxiv_provenance"
        )

    def test_radar_routes_do_not_write_or_audit(self):
        for fn_name in ("get_radar_seed", "radar_search", "radar_compare"):
            src = extract_function_source(_ROUTE_SRC, fn_name)
            assert_source_forbids(
                src,
                [
                    "record_review_event",
                    "touch_last_checked",
                    "pd_store.upsert_subscription",
                    "pd_store.dismiss",
                    "pd_queue.enqueue_items",
                    "url_fetch",
                    "_accept_material_source",
                ],
                context=f"routes.paper_discovery.{fn_name}",
            )

    def test_no_radar_specific_ingest_endpoint(self):
        """PR3: 取り込みの弁は既存の ``/ingest`` / ``/ingest-batch`` の2本だけ。

        ``/radar/provenance`` は既存教材の出所を記帳するだけで論文を取得しないので、
        取得・受理経路（``url_fetch`` / ``_accept_material_source`` / キュー投入）に
        触れないことも併せて固定する。
        """
        radar_routes = [
            line for line in _ROUTE_SRC.splitlines() if "@router." in line and "radar" in line
        ]
        assert sorted(radar_routes) == sorted(
            [
                '@router.get("/radar/seed")',
                '@router.post("/radar/search")',
                '@router.post("/radar/compare")',
                '@router.post("/radar/provenance")',
            ]
        )
        assert_source_forbids(
            extract_function_source(_ROUTE_SRC, "register_radar_provenance"),
            [
                "url_fetch",
                "_accept_material_source",
                "pd_queue.enqueue_items",
                "pd_store.upsert_subscription",
                "touch_last_checked",
            ],
            context="routes.paper_discovery.register_radar_provenance",
        )

    def test_provenance_registration_is_gated_and_audited(self):
        """後付け登録は edit 権限 + 監査記帳 + サーバ側の再導出（PR5 / PR8）。"""
        fn = extract_function_source(_ROUTE_SRC, "register_radar_provenance")
        assert "_radar_document_or_404" in fn
        assert "_radar_can_register" in fn
        assert "record_review_event" in fn
        # クライアントが提示した ID を信用せず、サーバが seed を導出し直して照合する。
        assert "pd_radar.resolve_seed" in fn
        assert "PROVENANCE_STATUS_INFERRED" in fn
        # 照合材料が無ければ confirm でも記帳しない。
        assert 'provenance.get("fetched")' in fn

    def test_worker_does_not_import_radar(self):
        """PR5: worker / cron からレーダーを呼ぶ経路を作らない。"""
        src = WORKER_SOURCE.read_text(encoding="utf-8")
        assert_source_forbids(
            src,
            ["radar", "compare", "fetch_by_ids"],
            context="api/ingest_worker.py",
        )


# ---------------------------------------------------------------------------
# 5. 学習者に出さない・migration ゼロ（PR8 / PR1）
# ---------------------------------------------------------------------------


class TestScope:
    def test_no_learning_route_source(self):
        learning_dir_files = [
            BACKEND / "api" / "routes" / "learning.py",
            BACKEND / "api" / "routes" / "corpus.py",
        ]
        for path in learning_dir_files:
            if not path.is_file():
                continue
            assert_source_forbids(
                path.read_text(encoding="utf-8"),
                ["paper_discovery.radar", "pd_radar", "radar/search"],
                context=str(path),
            )

    def test_no_migration_is_added_for_the_radar(self):
        """PR1: 新テーブル・新列ゼロ（構造的な確認）。"""
        sql_files = sorted((BACKEND / "db").glob("*.sql"))
        offending = [
            path.name
            for path in sql_files
            if "radar" in path.name.lower()
            or "radar" in path.read_text(encoding="utf-8").lower()
        ]
        assert offending == [], f"radar must not add DDL: {offending}"


# ---------------------------------------------------------------------------
# 6. 距離ラベルの正本（PR2）
# ---------------------------------------------------------------------------


class TestDistanceLabelCanon:
    def test_labels_and_thresholds_live_only_in_label_vocab(self):
        from core.label_vocab import (
            RADAR_DISTANCE_SCALE,
            RADAR_DISTANCE_THRESHOLD_MID,
            RADAR_DISTANCE_THRESHOLD_NEAR,
        )

        assert "RADAR_DISTANCE_SCALE" in _RANKING_SRC
        for literal in RADAR_DISTANCE_SCALE.labels:
            assert f'"{literal}"' not in _RANKING_SRC, literal
            assert f'"{literal}"' not in _RADAR_SRC, literal
            assert f'"{literal}"' not in _ROUTE_SRC, literal
        for threshold in (RADAR_DISTANCE_THRESHOLD_NEAR, RADAR_DISTANCE_THRESHOLD_MID):
            assert str(threshold) not in _RANKING_SRC
            assert str(threshold) not in _RADAR_SRC

    def test_thresholds_are_declared_once(self):
        src = LABEL_VOCAB_SOURCE.read_text(encoding="utf-8")
        assert src.count("RADAR_DISTANCE_THRESHOLD_NEAR = ") == 1
        assert src.count("RADAR_DISTANCE_THRESHOLD_MID = ") == 1

    def test_band_candidates_never_labels_an_unmeasured_candidate(self):
        """PR2: ``None`` を ``label_for`` に渡さない構造（``is not None`` ガード）。"""
        fn = extract_function_source(_RANKING_SRC, "band_candidates")
        assert "if similarity is not None:" in fn
        assert 'payload["distance_label"] = RADAR_DISTANCE_SCALE.label_for(similarity)' in fn
        # ガードの内側にラベル付与があること（外に出ていない）。
        guard_index = fn.index("if similarity is not None:")
        assert fn.index('payload["distance_label"]') > guard_index

    def test_no_raw_score_keys_in_the_new_modules(self):
        forbidden = ('"score"', '"similarity"', '"confidence"', '"relevance"', '"rank"')
        assert_source_forbids(_RADAR_SRC, forbidden, context="radar.py")
        assert_source_forbids(_COMPARE_SRC, forbidden, context="compare.py")
