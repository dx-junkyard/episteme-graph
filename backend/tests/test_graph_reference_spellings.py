"""参照 ID の綴りゆれ（``claim:`` 接頭辞 / 合成 step ID）が解決できること。

実測（2026-09-19・2609.* 10 本）では、thesis_reconstruction / dsl_linking が書く
``claim:{block_id}:{span_id}`` が ``theory_claims.source_scope.legacy_ids``
（接頭辞なし）と突合できず、開幕画面に内部 ID がそのまま出て（27〜68%）、
グラフレビューの根拠 claim も未解決になっていた。derivation step 側は逆に、graph が
裸の ``step_001`` を書き、保存済みの step は ``{derivation_id}:{step_id}`` で持つ。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import routes.theory_components as tc  # noqa: E402
from core.discuss import opening as discuss_opening  # noqa: E402

_CLAIM_UUID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_DOC = "11111111-1111-1111-1111-111111111111"


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, _stmt, _params=None):
        return self

    def fetchall(self):
        return self._rows

    def close(self):
        self.closed = True


def _claim_row(legacy_ids):
    return (
        _CLAIM_UUID,
        "The redshift distribution varies across the sky.",
        {"span_id": "span_001", "block_id": "blk_9fd917ff", "legacy_ids": legacy_ids},
        "teacher_review_required",
    )


class TestClaimReferenceIndex:
    def test_prefixed_reference_resolves_against_unprefixed_legacy_id(self, monkeypatch):
        monkeypatch.setattr(
            tc, "_pg_session",
            lambda: _FakeSession([_claim_row(["blk_9fd917ff:span_001"])]),
        )
        index = tc._resolve_claim_reference_index(
            _DOC, {"claim:blk_9fd917ff:span_001"}, artifacts={}
        )
        entry = index["claim:blk_9fd917ff:span_001"]
        assert entry["claim_id"] == _CLAIM_UUID
        assert entry["resolution"] == "db"
        assert entry["text"].startswith("The redshift distribution")

    def test_unprefixed_reference_still_resolves(self, monkeypatch):
        monkeypatch.setattr(
            tc, "_pg_session",
            lambda: _FakeSession([_claim_row(["blk_9fd917ff:span_001"])]),
        )
        index = tc._resolve_claim_reference_index(_DOC, {"blk_9fd917ff:span_001"}, artifacts={})
        assert index["blk_9fd917ff:span_001"]["claim_id"] == _CLAIM_UUID

    def test_unknown_reference_is_still_unresolved(self, monkeypatch):
        """綴りを広げるだけで、当たらない参照を当てたことにはしない。"""
        monkeypatch.setattr(
            tc, "_pg_session",
            lambda: _FakeSession([_claim_row(["blk_9fd917ff:span_001"])]),
        )
        index = tc._resolve_claim_reference_index(_DOC, {"claim:blk_other:span_001"}, artifacts={})
        assert index == {}

    def test_artifact_fallback_also_accepts_the_prefix(self, monkeypatch):
        monkeypatch.setattr(tc, "_pg_session", lambda: _FakeSession([]))
        artifacts = {
            "claim_object_builder": {
                "claims": [{"claim_id": "claim_span_001_3_sub02", "text": "atomic claim"}]
            }
        }
        index = tc._resolve_claim_reference_index(
            _DOC, {"claim:claim_span_001_3_sub02"}, artifacts=artifacts
        )
        entry = index["claim:claim_span_001_3_sub02"]
        assert entry["resolution"] == "artifact"
        assert entry["text"] == "atomic claim"


class TestDerivationReferenceIndex:
    _ARTIFACTS = {
        "derivation_chain": {
            "chains": [
                {
                    "derivation_id": "derivation_claim_0001",
                    "operation": "derive_result",
                    "steps": [{"step_id": "step_001", "operation": "define_basis", "reason": "…"}],
                }
            ]
        }
    }

    def test_bare_step_id_resolves(self):
        index = tc._resolve_derivation_reference_index(self._ARTIFACTS, {"step_001"})
        assert index["step_001"]["kind"] == "step"

    def test_composite_step_id_resolves(self):
        index = tc._resolve_derivation_reference_index(
            self._ARTIFACTS, {"derivation_claim_0001:step_001"}
        )
        assert index["derivation_claim_0001:step_001"]["kind"] == "step"
        assert index["derivation_claim_0001:step_001"]["operation"] == "define_basis"

    def test_unreferenced_ids_are_not_indexed(self):
        assert tc._resolve_derivation_reference_index(self._ARTIFACTS, {"step_999"}) == {}


class TestDiscussOpeningClaimLabels:
    def test_prefixed_claim_id_gets_the_paper_text_not_the_internal_id(self):
        index = {"blk_9fd917ff:span_001": "この論文の中心的な主張"}
        item = discuss_opening._claim_ref_item("claim:blk_9fd917ff:span_001", index)
        assert item["label"] == "この論文の中心的な主張"
        # id は元の綴りのまま返す（呼び出し側の参照を書き換えない）。
        assert item["id"] == "claim:blk_9fd917ff:span_001"

    def test_unresolvable_id_still_falls_back_to_the_id(self):
        item = discuss_opening._claim_ref_item("claim:blk_x:span_001", {})
        assert item["label"] == "claim:blk_x:span_001"
