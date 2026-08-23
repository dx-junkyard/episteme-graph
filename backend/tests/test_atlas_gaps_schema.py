"""カテゴリギャップ候補 — ``core/atlas_gaps/schema.py`` の単体テスト。

設計書: ``docs/features/category_gap_candidates_design.md`` §4.1（反復閾値）/
§4.2（cluster_key は版非依存）/ §5.2（語彙）。

検証観点:
  1. ``normalize_label`` の決定論（NFKC → casefold → 空白畳み）と冪等性
  2. ``build_cluster_key`` の**版非依存**（skeleton_version を含まない）と往復
  3. 語彙集合の整合（ラベル網羅・部分集合関係）
  4. ``confidence_label`` の境界と安全側（未測定は「低」）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from core.atlas_gaps import schema  # noqa: E402


# ---------------------------------------------------------------------------
# 1. normalize_label
# ---------------------------------------------------------------------------


class TestNormalizeLabel:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Cosmic Web", "cosmic web"),
            ("  Cosmic   Web  ", "cosmic web"),
            ("COSMIC WEB", "cosmic web"),
            ("Cosmic\tWeb", "cosmic web"),
            ("Cosmic\nWeb", "cosmic web"),
            # NFKC: 全角英数・全角空白を半角へ
            ("ＣＯＳＭＩＣ　Ｗｅｂ", "cosmic web"),
            # 日本語はそのまま（区切りの「・」は保つ）
            ("宇宙論・大規模構造", "宇宙論・大規模構造"),
            ("", ""),
            (None, ""),
            ("   ", ""),
        ],
    )
    def test_normalization_rules(self, raw, expected):
        assert schema.normalize_label(raw) == expected

    def test_is_idempotent(self):
        once = schema.normalize_label(" Modified   Gravity ")
        assert schema.normalize_label(once) == once

    def test_is_deterministic(self):
        assert schema.normalize_label("Dark  Energy") == schema.normalize_label(
            "dark energy"
        )

    def test_does_not_collapse_distinct_labels(self):
        # 空白を全部落とす core/atlas.py::normalize_label とは意図的に別実装
        assert schema.normalize_label("cosmic web") != schema.normalize_label("cosmicweb")


# ---------------------------------------------------------------------------
# 2. build_cluster_key（§4.2 裁定: 版非依存）
# ---------------------------------------------------------------------------


class TestClusterKey:
    def test_shape(self):
        assert (
            schema.build_cluster_key("astrophysics", "cosmology", "Cosmic Web")
            == "gap|astrophysics|cosmology|cosmic web"
        )

    def test_region_layer_has_empty_parent_segment(self):
        assert (
            schema.build_cluster_key("astrophysics", "", "重力波天文学")
            == "gap|astrophysics||重力波天文学"
        )

    def test_same_subject_from_different_documents_shares_the_key(self):
        a = schema.build_cluster_key("astrophysics", "cosmology", "Cosmic  Web")
        b = schema.build_cluster_key("astrophysics", "cosmology", "cosmic web")
        assert a == b

    def test_key_is_version_independent(self):
        """凍結版が上がっても同じキー（却下ゾンビを作らない・§4.2）。

        版は cluster_key の構成要素ではない（signals 側の刻印列にしか無い）ことを、
        シグネチャ（引数3つ）と出力の両方で固定する。
        """
        import inspect

        params = list(inspect.signature(schema.build_cluster_key).parameters)
        assert params == ["domain_key", "parent_region_id", "proposed_label"]
        key = schema.build_cluster_key("astrophysics", "cosmology", "Cosmic Web")
        assert "2026" not in key

    def test_domain_and_parent_separate_the_namespace(self):
        base = schema.build_cluster_key("astrophysics", "cosmology", "X")
        assert base != schema.build_cluster_key("particle_physics", "cosmology", "X")
        assert base != schema.build_cluster_key("astrophysics", "galaxies", "X")

    def test_whitespace_in_ids_is_trimmed(self):
        assert schema.build_cluster_key(
            " astrophysics ", " cosmology ", "X"
        ) == schema.build_cluster_key("astrophysics", "cosmology", "X")

    @pytest.mark.parametrize(
        "domain,parent,label",
        [
            ("astrophysics", "cosmology", "Cosmic Web"),
            ("astrophysics", "", "重力波天文学"),
            ("modified_gravity", "field_equations", "f(R) gravity"),
            # ラベルに区切り文字が入っても往復できる
            ("astrophysics", "cosmology", "a|b|c"),
        ],
    )
    def test_parse_cluster_key_roundtrip(self, domain, parent, label):
        key = schema.build_cluster_key(domain, parent, label)
        assert schema.parse_cluster_key(key) == (
            domain,
            parent,
            schema.normalize_label(label),
        )

    @pytest.mark.parametrize("bad", ["", "nonsense", "other|a|b|c", "gap|only"])
    def test_parse_cluster_key_is_fail_safe(self, bad):
        assert schema.parse_cluster_key(bad) == ("", "", "")

    def test_domain_prefix_matches_its_own_keys(self):
        prefix = schema.cluster_key_domain_prefix("astrophysics")
        assert schema.build_cluster_key("astrophysics", "c", "x").startswith(prefix)
        # domain_key の `_` を LIKE のワイルドカードとして扱わせない（前方一致で使う）
        assert not schema.build_cluster_key(
            "astrophysicsX", "c", "x"
        ).startswith(prefix)


# ---------------------------------------------------------------------------
# 3. 語彙
# ---------------------------------------------------------------------------


class TestVocabulary:
    def test_layers(self):
        assert schema.GAP_LAYERS == ("region", "concept")
        assert set(schema.GAP_LAYER_LABELS) == set(schema.GAP_LAYERS)
        assert schema.is_valid_layer("region")
        assert not schema.is_valid_layer("universe")

    def test_signal_statuses(self):
        assert set(schema.SIGNAL_STATUSES) == {"active", "superseded"}
        assert schema.SUPERSEDABLE_SIGNAL_STATUSES == (schema.SIGNAL_STATUS_ACTIVE,)
        assert not schema.is_valid_signal_status("deleted")

    def test_decision_statuses(self):
        assert set(schema.DECISION_STATUSES) == {
            "candidate",
            "accepted",
            "dismissed",
            "merged",
        }
        assert set(schema.DECISION_STATUS_LABELS) == set(schema.DECISION_STATUSES)
        assert set(schema.SUPPRESSED_DECISION_STATUSES) < set(schema.DECISION_STATUSES)
        assert schema.DECISION_STATUS_CANDIDATE not in schema.SUPPRESSED_DECISION_STATUSES
        assert schema.DECISION_STATUS_ACCEPTED not in schema.SUPPRESSED_DECISION_STATUSES

    def test_review_note_is_required_only_for_dismissal(self):
        assert schema.REVIEW_NOTE_REQUIRED_STATUSES == (schema.DECISION_STATUS_DISMISSED,)

    def test_repetition_threshold_matches_the_ruling(self):
        # §4.1 裁定: 2論文以上（D層 assumption_mining と同値）
        assert schema.MIN_DOCUMENTS_FOR_CANDIDATE == 2

    def test_audit_actions_cover_every_decision_status(self):
        assert set(schema.DECISION_STATUS_AUDIT_ACTIONS) == set(schema.DECISION_STATUSES)
        assert set(schema.DECISION_STATUS_AUDIT_ACTIONS.values()) <= set(
            schema.AUDIT_ACTIONS
        )
        assert schema.audit_action_for_status("nonsense") == ""

    def test_labels_are_factual(self):
        forbidden = ("埋めましょう", "不足", "未整備", "急いで", "疑え")
        text = " ".join(
            list(schema.DECISION_STATUS_LABELS.values())
            + list(schema.GAP_LAYER_LABELS.values())
            + list(schema.CONFIDENCE_LABELS)
        )
        assert not [w for w in forbidden if w in text]


# ---------------------------------------------------------------------------
# 4. confidence（LS5: 生値ではなく段階ラベル）
# ---------------------------------------------------------------------------


class TestConfidence:
    @pytest.mark.parametrize(
        "value,label",
        [
            (1.0, "高"),
            (0.75, "高"),
            (0.74, "中"),
            (0.5, "中"),
            (0.49, "低"),
            (0.0, "低"),
            (None, "低"),
            ("なんとなく", "低"),
        ],
    )
    def test_label_boundaries(self, value, label):
        assert schema.confidence_label(value) == label

    def test_normalize_keeps_unmeasured_as_none(self):
        assert schema.normalize_confidence(None) is None
        assert schema.normalize_confidence("x") is None
        assert schema.normalize_confidence(-1) == 0.0
        assert schema.normalize_confidence(2) == 1.0
        assert schema.normalize_confidence(0.42) == pytest.approx(0.42)

    def test_forbidden_numeric_keys_include_confidence(self):
        assert "confidence" in schema.FORBIDDEN_NUMERIC_KEYS
