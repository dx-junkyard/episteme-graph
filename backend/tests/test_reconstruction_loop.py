"""R層のふるまいテスト（DIFF / REFLECT / validator / item_builder / health / stumble）。

DB を使わない純ロジックと、core の _fetch_* を monkeypatch した集計ロジックを検証する。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.reconstruction import diff as recon_diff  # noqa: E402
from core.reconstruction import item_builder  # noqa: E402
from core.reconstruction import validator  # noqa: E402
from core.reconstruction import health as health_mod  # noqa: E402
from core.reconstruction import stumble as stumble_mod  # noqa: E402


CLAIM = {
    "claim_type": "relation",
    "text": "The observable offset grows linearly with the coupling g.",
    "normalized_text": "offset proportional to g",
    "concepts": [{"name": "offset", "role": "subject"}, {"name": "g", "role": "driver"}],
    "equation": {"label": "3.2", "latex": "O = a g", "defined_symbols": ["O", "g", "a"], "relation_type": "proportional"},
    "source_scope": {"level": "chunk", "section_title": "3. Construction", "pages": [4]},
    "evidence_text": "the offset scales linearly with g",
}

PREDICT_ITEM = {
    "elicit_mode": "predict",
    "response_space": [{"id": "a", "label": "proportional to g"}, {"id": "b", "label": "proportional to 1/g"}],
    "expected": {"option_id": "a"},
}


class TestDiffPredict:
    def test_match(self):
        d = recon_diff.run_diff(PREDICT_ITEM, CLAIM, {"option_id": "a"})
        assert d.machine_verdict == "match"

    def test_mismatch(self):
        d = recon_diff.run_diff(PREDICT_ITEM, CLAIM, {"option_id": "b"})
        assert d.machine_verdict == "mismatch"
        assert d.focus

    def test_no_selection_is_na(self):
        d = recon_diff.run_diff(PREDICT_ITEM, CLAIM, {})
        assert d.machine_verdict == "na"


class TestDiffRestate:
    def test_restate_verdict_is_na(self):
        item = {"elicit_mode": "restate", "response_space": [], "expected": {}}
        d = recon_diff.run_diff(item, CLAIM, {"text": "the offset goes up with g"})
        assert d.machine_verdict == "na"
        assert "offset" in d.covered_concepts

    def test_restate_missing_concept_detected(self):
        item = {"elicit_mode": "restate", "response_space": [], "expected": {}}
        d = recon_diff.run_diff(item, CLAIM, {"text": "it increases"})
        assert "offset" in d.missing_concepts or "g" in d.missing_concepts


class TestResponseHasContent:
    """CAPTURE なしの REVEAL 防止: 空応答を実体ありと誤判定しない。"""

    def test_empty_variants(self):
        for resp in ({}, {"text": ""}, {"text": "   "}, {"option_id": ""}, None):
            assert recon_diff.response_has_content(resp or {}) is False

    def test_content_variants(self):
        assert recon_diff.response_has_content({"option_id": "a"}) is True
        assert recon_diff.response_has_content({"text": "my restatement"}) is True


class TestReflection:
    def test_reveal_present_after_diff(self):
        d = recon_diff.run_diff(PREDICT_ITEM, CLAIM, {"option_id": "b"})
        refl = recon_diff.build_reflection(PREDICT_ITEM, CLAIM, {"option_id": "b"}, d)
        assert refl["reveal"]["text"] == CLAIM["text"]
        assert refl["reveal"]["equation_latex"] == "O = a g"
        assert refl["reveal"]["evidence_text"] == CLAIM["evidence_text"]
        assert refl["statements"]

    def test_verdict_hypothesis_framing(self):
        d = recon_diff.run_diff(PREDICT_ITEM, CLAIM, {"option_id": "b"})
        refl = recon_diff.build_reflection(PREDICT_ITEM, CLAIM, {"option_id": "b"}, d)
        joined = " ".join(refl["statements"])
        assert "可能性" in joined  # 断定でなく仮説文体


class TestValidator:
    def test_predict_ok(self):
        data = {
            "elicit_mode": "predict", "prompt": "how does the offset behave vs g?",
            "response_space": [{"id": "a", "label": "prop g"}, {"id": "b", "label": "prop 1/g"}],
            "expected": {"option_id": "a"}, "author_confidence": 0.6,
        }
        res, errs, _ = validator.validate_output(data, CLAIM)
        assert res is not None and errs == []

    def test_predict_expected_must_be_in_space(self):
        data = {
            "elicit_mode": "predict", "prompt": "q",
            "response_space": [{"id": "a", "label": "x"}], "expected": {"option_id": "zzz"},
        }
        res, errs, _ = validator.validate_output(data, CLAIM)
        assert res is None and errs

    def test_restate_rejects_options(self):
        data = {"elicit_mode": "restate", "prompt": "state it", "response_space": [{"id": "a", "label": "x"}], "expected": {}}
        res, errs, _ = validator.validate_output(data, CLAIM)
        assert res is None and any("restate" in e for e in errs)

    def test_rejects_verbatim_answer_in_prompt(self):
        data = {"elicit_mode": "restate", "prompt": CLAIM["text"], "response_space": [], "expected": {}}
        res, errs, _ = validator.validate_output(data, CLAIM)
        assert res is None and any("verbatim" in e for e in errs)

    def test_confidence_clamped(self):
        data = {"elicit_mode": "restate", "prompt": "state it your way", "response_space": [], "expected": {}, "author_confidence": 5.0}
        res, errs, warns = validator.validate_output(data, CLAIM)
        assert res is not None and res.author_confidence == 1.0


class TestItemBuilder:
    def test_prefers_predict_for_relational_claim(self):
        assert item_builder.preferred_elicit_mode(CLAIM) == "predict"

    def test_falls_back_to_restate(self):
        claim = {"claim_type": "definition", "concepts": [{"name": "x"}], "equation": {}}
        assert item_builder.preferred_elicit_mode(claim) == "restate"

    def test_symbol_probe_uses_claim_symbols(self):
        probe = item_builder.symbol_probe(CLAIM)
        assert probe["elicit_mode"] == "symbol"
        assert probe["target_symbol"] in ("O", "offset")
        assert probe["prompt"]


class TestHealthRank:
    def test_suspicious_item_ranks_above_clean(self, monkeypatch):
        rows = [
            # 低 confidence + 高 mismatch + dissent → 上位
            {"item_id": "bad", "claim_id": "c1", "status": "auto", "author_confidence": 0.1,
             "n_responses": 8, "n_mismatch": 6, "n_verdict_dissent": 2, "n_verdict_self_disagree": 3,
             "n_descend": 4, "n_users": 6, "elicit_mode": "predict", "prompt": "p"},
            # 高 confidence + 低 mismatch → 下位
            {"item_id": "good", "claim_id": "c2", "status": "auto", "author_confidence": 0.9,
             "n_responses": 8, "n_mismatch": 0, "n_verdict_dissent": 0, "n_verdict_self_disagree": 0,
             "n_descend": 0, "n_users": 6, "elicit_mode": "predict", "prompt": "p"},
        ]
        monkeypatch.setattr(health_mod, "_fetch_health_rows", lambda session, document_id=None, document_ids=None: rows)
        monkeypatch.setattr(health_mod, "_pg_session", lambda: _NullSession())
        out = health_mod.get_review_queue("doc1")
        ids = [it["item_id"] for it in out["items"]]
        assert ids[0] == "bad"
        # 生カウント（n_mismatch 等）は出さない
        assert "n_mismatch" not in str(out)
        assert "_score" not in str(out)
        assert out["items"][0]["signals"]["mismatch_level"] in ("低", "中", "高")

    def test_below_k_marked_info_short(self, monkeypatch):
        rows = [{"item_id": "x", "claim_id": "c", "status": "auto", "author_confidence": 0.2,
                 "n_responses": 2, "n_mismatch": 2, "n_verdict_dissent": 0, "n_verdict_self_disagree": 0,
                 "n_descend": 0, "n_users": 2, "elicit_mode": "restate", "prompt": "p"}]
        monkeypatch.setattr(health_mod, "_fetch_health_rows", lambda session, document_id=None, document_ids=None: rows)
        monkeypatch.setattr(health_mod, "_pg_session", lambda: _NullSession())
        out = health_mod.get_review_queue("doc1")
        assert out["items"][0]["rank_tier"] == "情報不足"
        assert out["items"][0]["signals"]["mismatch_level"] == "まだデータなし"

    def test_many_responses_from_single_user_stay_hidden(self, monkeypatch):
        """k-匿名の母数は人数。1人の学習者が何度答えてもセルは開示されない（P3）。"""
        rows = [{"item_id": "x", "claim_id": "c", "status": "auto", "author_confidence": 0.2,
                 "n_responses": 8, "n_mismatch": 6, "n_verdict_dissent": 1, "n_verdict_self_disagree": 2,
                 "n_descend": 5, "n_users": 1, "elicit_mode": "predict", "prompt": "p"}]
        monkeypatch.setattr(health_mod, "_fetch_health_rows", lambda session, document_id=None, document_ids=None: rows)
        monkeypatch.setattr(health_mod, "_pg_session", lambda: _NullSession())
        item = health_mod.get_review_queue("doc1")["items"][0]
        assert item["rank_tier"] == "情報不足"
        assert item["signals"]["mismatch_level"] == "まだデータなし"
        assert item["signals"]["responses"] == "まだデータなし"
        assert item["signals"]["descend_frequency"] == "まだデータなし"
        assert item["signals"]["verdict_dissent"] is False


class TestFetchHealthRowsDocumentIdsAggregation:
    """レビュー指摘2: 複数教材コース集約向け document_ids フィルタ（health.py 正本）。

    document_ids は document_id より優先し、空リストは全件フォールバックせず
    DB へも問い合わせず即0件を返す（fail-closed）。
    """

    def test_document_ids_used_when_both_given(self):
        captured = {}

        class _FakeResult:
            def fetchall(self):
                return []

        class _FakeSession:
            def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return _FakeResult()

        health_mod._fetch_health_rows(_FakeSession(), document_id="ignored", document_ids=["doc-a", "doc-b"])
        assert "ANY(:docs)" in captured["sql"]
        assert captured["params"] == {"docs": ["doc-a", "doc-b"]}

    def test_falls_back_to_document_id_when_document_ids_is_none(self):
        captured = {}

        class _FakeResult:
            def fetchall(self):
                return []

        class _FakeSession:
            def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return _FakeResult()

        health_mod._fetch_health_rows(_FakeSession(), document_id="doc1", document_ids=None)
        assert "i.document_id = :doc" in captured["sql"]
        assert captured["params"] == {"doc": "doc1"}

    def test_empty_document_ids_returns_empty_without_querying_db(self):
        class _RaisingSession:
            def execute(self, *a, **k):
                raise AssertionError("should not query DB when document_ids is an empty list")

        assert health_mod._fetch_health_rows(_RaisingSession(), document_ids=[]) == []

    def test_no_filters_returns_all_rows_unchanged_behavior(self):
        """document_id・document_ids とも未指定なら従来どおり無条件（全件）。"""
        captured = {}

        class _FakeResult:
            def fetchall(self):
                return []

        class _FakeSession:
            def execute(self, stmt, params):
                captured["sql"] = str(stmt)
                captured["params"] = params
                return _FakeResult()

        health_mod._fetch_health_rows(_FakeSession())
        assert "WHERE" not in captured["sql"]
        assert captured["params"] == {}


class TestGetReviewQueueDocumentIdsAggregation:
    """get_review_queue の document_ids 経路（review_queue API から course_id 指定時に使われる）。"""

    def test_document_ids_returns_aggregated_items(self, monkeypatch):
        rows = [{"item_id": "y", "claim_id": "c", "status": "auto", "author_confidence": 0.1,
                 "n_responses": 8, "n_mismatch": 6, "n_verdict_dissent": 0,
                 "n_verdict_self_disagree": 0, "n_descend": 0, "n_users": 6,
                 "elicit_mode": "predict", "prompt": "p"}]
        monkeypatch.setattr(
            health_mod, "_fetch_health_rows",
            lambda session, document_id=None, document_ids=None: rows if document_ids else [],
        )
        monkeypatch.setattr(health_mod, "_pg_session", lambda: _NullSession())
        out = health_mod.get_review_queue(document_ids=["doc-a", "doc-b"])
        assert len(out["items"]) == 1
        assert out["items"][0]["item_id"] == "y"

    def test_empty_document_ids_is_fail_closed(self, monkeypatch):
        """空リストは全件フォールバックせず0件（コースの source 教材が0件の場合等）。"""
        monkeypatch.setattr(health_mod, "_pg_session", lambda: _NullSession())
        out = health_mod.get_review_queue(document_ids=[])
        assert out == {"items": [], "k_anonymity": health_mod.K_ANON}


class TestStumble:
    def test_axes_k_anonymized(self, monkeypatch):
        agg = {
            "cA": {"n": 5, "n_mismatch": 3, "n_descend": 4, "n_dissent": 1, "n_self_disagree": 2,
                   "n_items": 1, "n_users": 4},
            "cB": {"n": 1, "n_mismatch": 1, "n_descend": 0, "n_dissent": 0, "n_self_disagree": 0,
                   "n_items": 1, "n_users": 1},
        }
        meta = {"cA": {"claim_type": "relation", "label": "claim A"},
                "cB": {"claim_type": "relation", "label": "claim B"}}
        questions = {"cA": 4, "cB": 1}
        monkeypatch.setattr(stumble_mod, "_fetch_claim_items", lambda s, d: agg)
        monkeypatch.setattr(stumble_mod, "_fetch_claim_meta", lambda s, d, ids: meta)
        monkeypatch.setattr(stumble_mod, "_fetch_anchored_questions", lambda s, ids: questions)
        monkeypatch.setattr(stumble_mod, "_pg_session", lambda: _NullSession())
        out = stumble_mod.get_stumble_summary("doc1")
        by = {c["claim_id"]: c for c in out["claims"]}
        # cA: n_users=4 (>=k) → error_rate 段階ラベル、descent レンジ、faq 3-5
        assert by["cA"]["axes"]["error_rate"] in ("低", "中", "高")
        assert by["cA"]["axes"]["symbol_descent"] == "3-5"
        assert by["cA"]["axes"]["faq"]["questions"] == "3-5"
        # cB: n_users=1 (<k) → すべて『まだデータなし』
        assert by["cB"]["axes"]["error_rate"] == "まだデータなし"
        assert by["cB"]["axes"]["faq"]["questions"] == "まだデータなし"
        assert out["k_anonymity"] == 3

    def test_single_user_activity_stays_hidden(self, monkeypatch):
        """k-匿名の母数は人数。応答行数が多くても学習者が1人ならセルは開示されない（P3）。"""
        agg = {"cA": {"n": 9, "n_mismatch": 7, "n_descend": 5, "n_dissent": 2, "n_self_disagree": 3,
                      "n_items": 2, "n_users": 1}}
        monkeypatch.setattr(stumble_mod, "_fetch_claim_items", lambda s, d: agg)
        monkeypatch.setattr(stumble_mod, "_fetch_claim_meta", lambda s, d, ids: {"cA": {"claim_type": "relation", "label": "A"}})
        monkeypatch.setattr(stumble_mod, "_fetch_anchored_questions", lambda s, ids: {})
        monkeypatch.setattr(stumble_mod, "_pg_session", lambda: _NullSession())
        c = stumble_mod.get_stumble_summary("doc1")["claims"][0]
        assert c["axes"]["error_rate"] == "まだデータなし"
        assert c["axes"]["symbol_descent"] == "まだデータなし"
        assert c["axes"]["verdict_self_check_divergence"] == "まだデータなし"
        assert c["has_data"] is False


class _NullSession:
    def execute(self, *a, **k):
        raise AssertionError("DB should be monkeypatched out in this test")

    def close(self):
        pass
