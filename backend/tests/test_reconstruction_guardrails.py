"""R層ガードレールの自動テスト（設計 §4.5「守るべき一線」を構造的に守る）。

- 学習者向けレスポンスに正答率・スコアの生数値が含まれない
- next のレスポンスに伏せフィールド（text / equation / evidence_text）が漏れない
- 出題対象が source_backed + 承認済みに限定される
- item の削除 API が存在しない（retire のみ）
- stumble-summary が n<3 セルを返さない（k-匿名 k=3）
- core/reconstruction/ が FastAPI を import しない
- REFLECT 文面に禁止語彙（点数・順位・煽り文句）が入らない
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

from core.reconstruction import diff as recon_diff  # noqa: E402
from core.reconstruction import item_builder  # noqa: E402
from core.reconstruction.health import K_ANON, count_range, rate_level  # noqa: E402
from core.reconstruction.schema import (  # noqa: E402
    APPROVED_REVIEW_STATUSES,
    DELIVERABLE_STATUSES,
    HIDDEN_CLAIM_FIELDS,
    ITEM_STATUSES,
    SOURCE_BACKED,
)
from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
    assert_source_forbids,
)

_CORE_DIR = BACKEND / "core" / "reconstruction"
_ROUTE_SRC = (BACKEND / "api" / "routes" / "reconstruction.py").read_text(encoding="utf-8")
_MAIN_SRC = (BACKEND / "api" / "main.py").read_text(encoding="utf-8")
_STUMBLE_SRC = (_CORE_DIR / "stumble.py").read_text(encoding="utf-8")
_HEALTH_SRC = (_CORE_DIR / "health.py").read_text(encoding="utf-8")


_CLAIM = {
    "claim_type": "relation",
    "text": "The observable offset grows linearly with the coupling g at leading order (3.2).",
    "normalized_text": "offset proportional to g",
    "concepts": [{"name": "offset", "role": "subject"}, {"name": "g", "role": "driver"}],
    "equation": {"label": "3.2", "latex": "O = a g", "defined_symbols": ["O", "g", "a"], "relation_type": "proportional"},
    "source_scope": {"level": "chunk", "section_title": "3. Construction", "pages": [4]},
    "evidence_text": "the offset scales linearly with g",
}


class TestNoHiddenFieldLeak:
    """next の learner 向け表現に答え（本文・式・evidence）が漏れない（§3.2）。"""

    def test_public_item_view_hides_answer_fields(self):
        item = {
            "id": "i1", "claim_id": "c1", "elicit_mode": "predict",
            "prompt": "how does the offset behave vs g?",
            "response_space": [{"id": "a", "label": "prop g"}, {"id": "b", "label": "prop 1/g"}],
            "author": "llm", "expected": {"option_id": "a"},
        }
        view = item_builder.public_item_view(item, _CLAIM)
        blob = str(view)
        assert _CLAIM["text"] not in blob
        assert _CLAIM["normalized_text"] not in blob
        assert _CLAIM["evidence_text"] not in blob
        assert "O = a g" not in blob  # equation latex
        # expected（正解の option_id）も learner 表現に含めない
        assert "expected" not in view
        assert '"option_id"' not in blob

    def test_hidden_fields_constant_covers_answer_fields(self):
        for f in ("text", "normalized_text", "equation", "evidence_text"):
            assert f in HIDDEN_CLAIM_FIELDS

    def test_public_response_space_omits_expected_hint(self):
        item = {
            "id": "i1", "claim_id": "c1", "elicit_mode": "predict", "prompt": "q",
            "response_space": [{"id": "a", "label": "x", "is_correct": True}],
            "author": "llm",
        }
        view = item_builder.public_item_view(item, _CLAIM)
        assert "is_correct" not in str(view["response_space"])


class TestNoRawScoreToLearners:
    """学習者向けレスポンス・REFLECT 文面にスコア/正答率/順位の生数値が入らない（P7）。"""

    BANNED = ("正答率", "点数", "順位", "ランキング", "偏差値", "スコア", "得点", "％", "%")

    def _reflection_text(self, mode, response):
        item = {
            "elicit_mode": mode,
            "response_space": [{"id": "a", "label": "prop g"}, {"id": "b", "label": "prop 1/g"}],
            "expected": {"option_id": "a"},
        }
        d = recon_diff.run_diff(item, _CLAIM, response)
        refl = recon_diff.build_reflection(item, _CLAIM, response, d)
        return " ".join(refl["statements"])

    def test_reflection_has_no_banned_vocab(self):
        for mode, resp in (("predict", {"option_id": "b"}), ("predict", {"option_id": "a"}), ("restate", {"text": "my restatement"})):
            text = self._reflection_text(mode, resp)
            for w in self.BANNED:
                assert w not in text, f"banned {w!r} in REFLECT statement: {text}"

    def test_reflection_has_no_score_shaped_text(self):
        """REFLECT にスコア/正答率/順位の数値表現を出さない（出典引用の数値は許容）。"""
        score_patterns = (r"\d+\s*%", r"\d+\s*点", r"\d+\s*位", r"\d+\s*/\s*\d+", "正答", "得点", "点数")
        for mode, resp in (("predict", {"option_id": "b"}), ("restate", {"text": "restatement"})):
            text = self._reflection_text(mode, resp)
            for pat in score_patterns:
                assert not re.search(pat, text), f"score-shaped text {pat!r} in REFLECT: {text}"

    def test_learning_routes_emit_no_score_keys(self):
        for key in ("正答率", '"score"', '"accuracy"', '"correct_rate"', "得点"):
            assert key not in _ROUTE_SRC


class TestElicitTargetRestriction:
    """出題対象は source_backed かつ承認済み review_status の claim のみ（§2.2）。"""

    def test_next_query_filters_source_backed_and_approved(self):
        block = re.search(r"def get_next_item[\s\S]+?return \{\"item\": item_builder", _ROUTE_SRC).group(0)
        assert "c.support_status = :backed" in block
        assert "c.review_status = ANY(:approved)" in block

    def test_next_never_serves_symbol_probes(self):
        """記号葉プローブは descend 経由でのみ提示（next で単独配信しない, §3.4）。"""
        block = re.search(r"def get_next_item[\s\S]+?return \{\"item\": item_builder", _ROUTE_SRC).group(0)
        assert "i.elicit_mode <> 'symbol'" in block

    def test_submit_requires_captured_response(self):
        """空応答で出典（伏せフィールド）を開示しない（CAPTURE → REVEAL の順序を守る）。"""
        assert "response_has_content" in _ROUTE_SRC

    def test_worker_only_authors_source_backed_and_approved(self):
        src = (_CORE_DIR / "worker.py").read_text(encoding="utf-8")
        assert "c.support_status = :backed" in src
        assert "c.review_status = ANY(:approved)" in src

    def test_approved_statuses_are_review_states(self):
        assert SOURCE_BACKED == "source_backed"
        assert "teacher_approved" in APPROVED_REVIEW_STATUSES


class TestNoDeleteOnlyRetire:
    """item は削除せず auto → flagged → retired の状態遷移（P4）。"""

    def test_no_delete_endpoint_in_routes(self):
        assert_source_forbids(
            _ROUTE_SRC, ["@learning_router.delete", "@admin_router.delete"], context="routes/reconstruction.py",
        )

    def test_no_delete_from_in_reconstruction_core(self):
        assert_module_tree_forbids(_CORE_DIR, ["DELETE FROM"])

    def test_retired_is_a_status_not_deletion(self):
        assert "retired" in ITEM_STATUSES
        assert "confirmed" in ITEM_STATUSES
        assert set(DELIVERABLE_STATUSES) == {"auto", "confirmed"}


class TestKAnonymity:
    """stumble-summary / review-queue は k=3・n<3 セル非表示（P3）。"""

    def test_k_is_three(self):
        assert K_ANON == 3

    def test_count_range_hides_below_k(self):
        assert count_range(0) == "まだデータなし"
        assert count_range(2) == "まだデータなし"
        assert count_range(3) == "3-5"
        assert count_range(7) == "6-10"
        assert count_range(20) == "11+"

    def test_rate_level_requires_k_denominator(self):
        assert rate_level(1, 2) == "まだデータなし"
        assert rate_level(0, 4) == "低"
        assert rate_level(3, 4) == "高"

    def test_stumble_and_health_use_k_anon_helpers(self):
        assert "count_range" in _STUMBLE_SRC and "rate_level" in _STUMBLE_SRC
        assert "count_range" in _HEALTH_SRC and "rate_level" in _HEALTH_SRC

    def test_k_gate_counts_distinct_users_not_rows(self):
        """k-匿名の母数は人数（DISTINCT user_id）。応答行数でセルを開示しない（P3、D層と同じ扱い）。"""
        assert "COUNT(DISTINCT r.user_id)" in _MAIN_SRC  # health ビューの n_users
        assert "COUNT(DISTINCT r.user_id)" in _STUMBLE_SRC
        assert "COUNT(DISTINCT user_id)" in _STUMBLE_SRC  # FAQ（interest_traces）も人数
        assert "n_users" in _HEALTH_SRC and "gate_by_users" in _HEALTH_SRC
        assert "gate_by_users" in _STUMBLE_SRC

    def test_stumble_axes_are_label_strings_not_raw_counts(self, monkeypatch):
        """サマリーの axes 値は段階ラベル/レンジ（文字列）のみ（生カウントを出力しない）。"""
        from core.reconstruction import stumble as stumble_mod

        agg = {"cA": {"n": 5, "n_mismatch": 3, "n_descend": 4, "n_dissent": 1, "n_self_disagree": 2,
                      "n_items": 1, "n_users": 4}}
        monkeypatch.setattr(stumble_mod, "_fetch_claim_items", lambda s, d: agg)
        monkeypatch.setattr(stumble_mod, "_fetch_claim_meta", lambda s, d, ids: {"cA": {"claim_type": "relation", "label": "A"}})
        monkeypatch.setattr(stumble_mod, "_fetch_anchored_questions", lambda s, ids: {"cA": 4})

        class _Null:
            def execute(self, *a, **k):
                raise AssertionError("db out")

            def close(self):
                pass

        monkeypatch.setattr(stumble_mod, "_pg_session", lambda: _Null())
        axes = stumble_mod.get_stumble_summary("doc1")["claims"][0]["axes"]
        assert isinstance(axes["error_rate"], str)
        assert isinstance(axes["symbol_descent"], str)
        assert isinstance(axes["verdict_self_check_divergence"], str)
        assert isinstance(axes["faq"]["questions"], str)


class TestCoreIsFrameworkFree:
    """core/reconstruction/ は FastAPI を import しない（テスタビリティ）。"""

    def test_no_fastapi_import_in_core(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["fastapi"])


class TestAuditVocabulary:
    """状態変更は theory_review_events に監査（entity_type 拡張は 'reconstruction_*'）。"""

    def test_entity_types_defined(self):
        from core.reconstruction.schema import ENTITY_ITEM, ENTITY_RESPONSE
        assert ENTITY_ITEM == "reconstruction_item"
        assert ENTITY_RESPONSE == "reconstruction_response"

    def test_routes_record_events(self):
        assert "_record_recon_event" in _ROUTE_SRC
        assert "verdict_wrong" in _ROUTE_SRC  # 機械判定への異議を監査

    def test_migration_present(self):
        assert "reconstruction_items" in _MAIN_SRC
        assert "learner_reconstructions" in _MAIN_SRC
        assert "reconstruction_item_health" in _MAIN_SRC
