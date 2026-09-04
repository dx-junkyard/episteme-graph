"""制度指標カタログ（``core/indicator_catalog.py``）のユニットテスト。

対象仕様: ``docs/features/indicator_governance_design.md``（IG1〜IG5）。
DB・FastAPI・ネットワークに触れない（カタログは純宣言）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import indicator_catalog as ic  # noqa: E402


# ---------------------------------------------------------------------------
# 1. カタログ全体の整合
# ---------------------------------------------------------------------------


class TestCatalogIntegrity:
    def test_validate_catalog_passes(self):
        ic.validate_catalog()  # 例外が出ないこと（重複 id / 重複 route の検出）

    def test_catalog_is_not_empty(self):
        assert len(ic.all_indicators()) >= 15

    def test_ids_are_kebab_case(self):
        for spec in ic.all_indicators():
            assert spec.id == spec.id.lower()
            assert "_" not in spec.id
            assert " " not in spec.id

    def test_indicators_mapping_matches_declaration_order(self):
        assert list(ic.INDICATORS) == [spec.id for spec in ic.all_indicators()]

    def test_indicators_mapping_is_immutable(self):
        with pytest.raises(TypeError):
            ic.INDICATORS["x"] = None  # type: ignore[index]

    def test_get_indicator_returns_none_for_unknown(self):
        assert ic.get_indicator("no-such-indicator") is None
        assert ic.get_indicator("") is None

    def test_indicators_for_route_resolves(self):
        spec = ic.all_indicators()[0]
        assert ic.indicators_for_route(spec.route) == (spec,)
        assert ic.indicators_for_route("/api/nope") == ()


# ---------------------------------------------------------------------------
# 2. IG2 — 非利用4項目は全計器に必須（構造強制）
# ---------------------------------------------------------------------------


class TestNonUses:
    def test_every_spec_declares_the_four_non_uses(self):
        for spec in ic.all_indicators():
            for term in (
                ic.NON_USE_RANKING,
                ic.NON_USE_GRADING,
                ic.NON_USE_RECOMMENDATION,
                ic.NON_USE_AUTO_GATE,
            ):
                assert term in spec.not_used_for, f"{spec.id} に {term} の非利用宣言が無い"

    def test_a_spec_without_all_non_uses_cannot_be_constructed(self):
        """IG2 は宣言時点で落ちる（後から緩められない）。"""
        with pytest.raises(ValueError) as exc:
            ic.IndicatorSpec(
                id="bad-one",
                label="だめな計器",
                definition="x",
                purpose="x",
                values_audience=ic.AUDIENCE_TEACHER,
                granularity=ic.GRANULARITY_AGGREGATE_SYSTEM,
                source="x",
                retention="x",
                k_anonymity=False,
                route="/api/admin/bad",
                consumer="core/x.py::y",
                design_doc="docs/features/x.md",
                not_used_for=(ic.NON_USE_RANKING,),  # 成績・推薦・自動ゲートを外した
            )
        assert "not_used_for" in str(exc.value)

    def test_every_non_use_has_a_japanese_label(self):
        for term in ic.NON_USES:
            assert ic.NON_USE_LABELS[term].strip()

    def test_side_effect_review_is_declared_and_not_automated(self):
        for spec in ic.all_indicators():
            assert spec.side_effect_review.strip()
        # IG3: 副作用レビューに自動判定を置かない（既定文言がそれを明言する）。
        assert "自動判定なし" in ic.SIDE_EFFECT_REVIEW_OWNER


# ---------------------------------------------------------------------------
# 3. 粒度と k-匿名の対応
# ---------------------------------------------------------------------------


class TestGranularity:
    def test_k_anonymity_iff_aggregate_k_anonymous(self):
        for spec in ic.all_indicators():
            expected = spec.granularity == ic.GRANULARITY_AGGREGATE_K_ANONYMOUS
            assert spec.k_anonymity is expected, spec.id

    def test_mismatched_k_anonymity_cannot_be_constructed(self):
        with pytest.raises(ValueError) as exc:
            ic.IndicatorSpec(
                id="bad-k",
                label="x",
                definition="x",
                purpose="x",
                values_audience=ic.AUDIENCE_TEACHER,
                granularity=ic.GRANULARITY_AGGREGATE_SYSTEM,
                source="x",
                retention="x",
                k_anonymity=True,  # 粒度と矛盾
                route="/api/admin/bad-k",
                consumer="core/x.py::y",
                design_doc="docs/features/x.md",
            )
        assert "k_anonymity" in str(exc.value)

    def test_unknown_vocabulary_is_rejected(self):
        for kwargs in (
            {"values_audience": "everyone"},
            {"granularity": "per_learner_ranked"},
        ):
            base = dict(
                id="bad-vocab",
                label="x",
                definition="x",
                purpose="x",
                values_audience=ic.AUDIENCE_TEACHER,
                granularity=ic.GRANULARITY_AGGREGATE_SYSTEM,
                source="x",
                retention="x",
                k_anonymity=False,
                route="/api/admin/bad-vocab",
                consumer="core/x.py::y",
                design_doc="docs/features/x.md",
            )
            base.update(kwargs)
            with pytest.raises(ValueError):
                ic.IndicatorSpec(**base)  # type: ignore[arg-type]

    def test_per_account_operational_is_only_the_account_ledger(self):
        """1アカウント単位の運用データは丸めずに専用語彙で宣言する（学習データではない）。"""
        per_account = [
            s.id for s in ic.all_indicators()
            if s.granularity == ic.GRANULARITY_PER_ACCOUNT_OPERATIONAL
        ]
        assert per_account == ["account-activity"]
        spec = ic.get_indicator("account-activity")
        assert spec is not None
        # 個人単位であることを隠さない（「集約」と偽らない）。
        assert "集約でも匿名化でもありません" in spec.definition


# ---------------------------------------------------------------------------
# 4. IG1 — 公開ビューは定義だけ（値を1つも含まない）
# ---------------------------------------------------------------------------


_VALUE_LIKE_KEYS = (
    "count", "counts", "value", "values", "total", "totals", "score", "scores",
    "weight", "weights", "confidence", "rate", "ratio", "percent", "rank",
    "n", "num", "amount", "cost", "tokens",
)


def _walk_keys(node, path=""):
    """dict / list を再帰し (キー名, パス) を列挙する。"""
    if isinstance(node, dict):
        for key, val in node.items():
            yield str(key), f"{path}.{key}"
            yield from _walk_keys(val, f"{path}.{key}")
    elif isinstance(node, list):
        for i, val in enumerate(node):
            yield from _walk_keys(val, f"{path}[{i}]")


class TestPublicView:
    def test_public_view_field_set_is_fixed(self):
        for item in ic.catalog_public_view():
            assert tuple(item.keys()) == ic.PUBLIC_VIEW_FIELDS

    def test_public_view_has_no_value_like_keys(self):
        """IG1: カタログは定義だけを持ち、値（件数・トークン数・レンジ）を持たない。"""
        offenders = [
            (key, path)
            for key, path in _walk_keys({"indicators": ic.catalog_public_view()})
            if key.lower() in _VALUE_LIKE_KEYS
        ]
        assert offenders == [], f"公開ビューに値らしいキーがある: {offenders}"

    def test_public_view_contains_no_numbers(self):
        """文字列・真偽値・リストだけ（int / float を1つも含まない）。"""
        def _scan(node, path=""):
            if isinstance(node, bool):
                return []
            if isinstance(node, (int, float)):
                return [path]
            if isinstance(node, dict):
                out = []
                for k, v in node.items():
                    out += _scan(v, f"{path}.{k}")
                return out
            if isinstance(node, list):
                out = []
                for i, v in enumerate(node):
                    out += _scan(v, f"{path}[{i}]")
                return out
            return []

        assert _scan(ic.catalog_public_view()) == []

    def test_public_view_carries_japanese_labels(self):
        for item in ic.catalog_public_view():
            assert item["values_audience_label"] in ic.AUDIENCE_LABELS.values()
            assert item["granularity_label"] in ic.GRANULARITY_LABELS.values()
            assert len(item["not_used_for_labels"]) == len(ic.NON_USES)

    def test_catalog_note_states_the_non_uses_and_the_change_rule(self):
        note = ic.CATALOG_NOTE
        assert "計器" in note
        assert "成績" in note
        assert "自動判定" in note
        assert "設計書" in note  # IG5


# ---------------------------------------------------------------------------
# 5. readable_by_me のロール判定（値の宛先の投影であって認可ではない）
# ---------------------------------------------------------------------------


class TestReadability:
    @pytest.mark.parametrize(
        "role,audience,expected",
        [
            ("STUDENT", "learner_self", True),
            ("TEACHER", "learner_self", True),
            ("SYSTEM_ADMIN", "learner_self", True),
            ("STUDENT", "teacher", False),
            ("TEACHER", "teacher", True),
            ("SYSTEM_ADMIN", "teacher", True),
            ("STUDENT", "system_admin", False),
            ("TEACHER", "system_admin", False),
            ("SYSTEM_ADMIN", "system_admin", True),
            ("", "teacher", False),
            ("TEACHER", "unknown-audience", False),
        ],
    )
    def test_can_read_values(self, role, audience, expected):
        pytest.importorskip("fastapi")
        from routes.indicators import _can_read_values

        assert _can_read_values(role, audience) is expected
