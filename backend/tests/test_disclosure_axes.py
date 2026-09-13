"""可視性6軸カタログ（``core/disclosure_axes.py``）の単体テスト。

仕様の正本は ``docs/features/disclosure_axes_design.md``（DA1〜DA6）。
DB・ネットワークには接続しない（純宣言モジュール）。

検証観点:
  1. 6軸の語彙と全 spec の網羅（軸を空欄で通さない）
  2. ``__post_init__`` の構造強制（宛先の語彙・provider プレースホルダ・学習者向け denylist）
  3. provider の解決（未知は総称へ縮退・カタログに provider 名を焼き込まない）
  4. 公開ビューが宣言だけを返す（値・数値を持たない）
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

from core import disclosure_axes as da  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 軸の語彙と網羅
# ---------------------------------------------------------------------------


class TestAxes:
    def test_six_axes_exactly(self):
        """vision §5.4 の6軸。増やさない・畳まない。"""
        assert len(da.AXES) == 6
        assert da.AXIS_IDS == (
            da.AXIS_AUDIENCE,
            da.AXIS_NAME_DISCLOSURE,
            da.AXIS_REUSE,
            da.AXIS_EVALUATION_USE,
            da.AXIS_EXTERNAL_TRANSFER,
            da.AXIS_WITHDRAWAL,
        )

    def test_every_axis_has_label_and_question(self):
        for axis in da.AXES:
            assert axis.label.strip()
            assert axis.question.strip()
            assert axis.question.endswith("。")

    def test_every_spec_declares_all_axes_non_empty(self):
        for spec in da.all_data_kinds():
            values = spec.axis_values()
            assert set(values) == set(da.AXIS_IDS), spec.data_kind
            for axis_id, text in values.items():
                assert text.strip(), f"{spec.data_kind}/{axis_id} が空"

    def test_catalog_validates(self):
        da.validate_catalog()   # import 時にも走るが、明示的にも固定する


# ---------------------------------------------------------------------------
# 2. 構造強制（__post_init__）
# ---------------------------------------------------------------------------


def _spec_kwargs(**overrides) -> dict:
    base = dict(
        data_kind="sample_kind",
        label="サンプル",
        what="サンプルの説明です。",
        note="この入力は外部の AI プロバイダ（{provider}）に送られます。",
        audience="あなただけが読めます。",
        name_disclosure="名前は出ません。",
        reuse="引用・再利用の経路はありません。",
        evaluation_use="成績評価には使いません。",
        external_transfer="送られます。入力した文が送られます。",
        withdrawal="後から取り消せます。",
        retention="行を削除せず残します。",
        aggregation="匿名の集計に入ります。",
        portability="持ち出せます。",
        source="sample_table",
        basis=("backend/core/disclosure_axes.py",),
        design_doc="docs/features/disclosure_axes_design.md",
        ai_touchpoints=(
            da.AiTouchpoint(
                operation="サンプルの操作",
                sends="入力した文",
                feature="learning:chat",
            ),
        ),
        without_ai=("この操作は外部の AI を通りません。",),
    )
    base.update(overrides)
    return base


class TestSpecEnforcement:
    def test_valid_spec_constructs(self):
        spec = da.DisclosureSpec(**_spec_kwargs())
        assert spec.note_text("OpenAI").endswith("送られます。")

    @pytest.mark.parametrize("field", ["label", "what", "note", "audience", "withdrawal",
                                       "retention", "aggregation", "portability"])
    def test_empty_required_field_is_rejected(self, field):
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(**{field: "   "}))

    def test_basis_is_required(self):
        """DA1: 宣言の出所を書かない spec は作れない。"""
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(basis=()))

    @pytest.mark.parametrize("bad_note", [
        "この入力は gpt-5.2 に送られます。",
        "1回の呼び出しで約1000トークンを送ります。",
        "1回あたり数円かかります。",
    ])
    def test_model_names_and_prices_are_rejected(self, bad_note):
        """DA2: 宛先に書けるのは provider までで、モデル名・料金・トークン数は書けない。"""
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(note=bad_note + "（{provider}）"))

    def test_touchpoint_rejects_model_names(self):
        with pytest.raises(ValueError):
            da.AiTouchpoint(operation="送信", sends="gpt-5.2 へ本文", feature="learning:chat")

    def test_note_without_provider_placeholder_is_rejected(self):
        """DA2: 外部送信があるなら provider は実行時解決のプレースホルダで書く。"""
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(note="外部の AI プロバイダに送られます。"))

    def test_note_naming_provider_without_touchpoints_is_rejected(self):
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(
                ai_touchpoints=(),
                external_transfer="送りません。",
            ))

    def test_zero_touchpoints_must_say_it_explicitly(self):
        """通過点ゼロで第5軸を曖昧にできない（黙った空欄を『不明』と区別する）。"""
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(
                ai_touchpoints=(),
                note="外部の AI には渡しません。",
                external_transfer="外部の AI には渡りません。",  # 「送りません」と明言しない
            ))
        ok = da.DisclosureSpec(**_spec_kwargs(
            ai_touchpoints=(),
            note="外部の AI に送りません。",
            external_transfer="送りません。",
        ))
        assert ok.ai_touchpoints == ()

    @pytest.mark.parametrize("term", ["ADMIN_PASSWORD", "/api/admin", "_require_teacher"])
    def test_learner_facing_denylist_is_enforced(self, term):
        """DA4: 学習者が読む事実文に内部名を出せない。"""
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(audience=f"{term} を使います。"))
        # 学習者向けでない spec なら通る（教員向けの技術的な説明を禁じない）。
        allowed = da.DisclosureSpec(**_spec_kwargs(
            audience=f"{term} を使います。", learner_facing=False,
        ))
        assert allowed.learner_facing is False

    @pytest.mark.parametrize("term", ["同意しました", "同意して", "利用規約に同意"])
    def test_consent_wording_is_rejected(self, term):
        """DA5: ここは告知の層で、同意を取る層ではない。"""
        with pytest.raises(ValueError):
            da.DisclosureSpec(**_spec_kwargs(what=f"利用者が{term}記録です。"))

    def test_data_kind_must_be_snake_case(self):
        for bad in ("Learning_Chat", "learning-chat", "learning chat", ""):
            with pytest.raises(ValueError):
                da.DisclosureSpec(**_spec_kwargs(data_kind=bad))


# ---------------------------------------------------------------------------
# 3. provider の解決
# ---------------------------------------------------------------------------


class TestProvider:
    @pytest.mark.parametrize("key,label", [
        ("openai", "OpenAI"),
        ("OpenAI", "OpenAI"),
        ("gemini", "Google"),
        ("google", "Google"),
        ("gemini-vertex", "Google"),
    ])
    def test_known_providers(self, key, label):
        assert da.provider_label(key) == label

    @pytest.mark.parametrize("key", [None, "", "   ", "unknown-vendor"])
    def test_unknown_provider_falls_back_to_generic(self, key):
        """推測で企業名を書かない（総称へ縮退する）。"""
        assert da.provider_label(key) == da.GENERIC_PROVIDER_LABEL

    def test_note_for_resolves_provider(self):
        text = da.note_for("learning_chat", "openai")
        assert "OpenAI" in text
        assert "{provider}" not in text

    def test_note_for_unknown_kind_is_empty(self):
        """未知の種別に文言を捏造しない（フロントは空なら何も描かない）。"""
        assert da.note_for("nope", "openai") == ""

    def test_catalog_does_not_hardcode_provider_names(self):
        """カタログ側には provider 名を焼き込まない（設定を変えたら嘘になる）。"""
        for spec in da.all_data_kinds():
            for text in (spec.note, spec.external_transfer):
                for label in set(da.PROVIDER_LABELS.values()):
                    assert label not in text, f"{spec.data_kind}: {label} が焼き込まれている"


# ---------------------------------------------------------------------------
# 4. 公開ビュー（宣言だけ）
# ---------------------------------------------------------------------------

_NUMERIC_KEY_HINTS = (
    "count", "total", "score", "weight", "confidence", "tokens", "cost",
    "price", "amount", "size", "rate",
)


class TestPublicView:
    def test_fields_match_the_declared_public_view(self):
        for item in da.catalog_public_view(provider_key="openai"):
            assert set(item) == set(da.PUBLIC_VIEW_FIELDS)

    def test_public_view_holds_no_numbers(self):
        """宣言だけ — 数値・件数を1つも含まない（再帰走査）。"""
        payload = {
            "axes": da.axes_public_view(),
            "data_kinds": da.catalog_public_view(provider_key="openai"),
        }

        offenders: list[str] = []

        def walk(node, path):
            if isinstance(node, dict):
                for key, value in node.items():
                    lowered = str(key).lower()
                    if any(hint in lowered for hint in _NUMERIC_KEY_HINTS):
                        offenders.append(f"{path}.{key}")
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")
            elif isinstance(node, (int, float)) and not isinstance(node, bool):
                offenders.append(f"{path}={node!r}")

        walk(payload, "$")
        assert offenders == [], f"公開ビューに値らしいものがあります: {offenders}"

    def test_order_is_the_declaration_order(self):
        kinds = [item["data_kind"] for item in da.catalog_public_view()]
        assert kinds == [spec.data_kind for spec in da.all_data_kinds()]

    def test_get_data_kind(self):
        assert da.get_data_kind("learning_chat") is not None
        assert da.get_data_kind(" learning_chat ") is not None
        assert da.get_data_kind("nope") is None

    def test_note_is_a_single_line(self):
        """対話 UI に置くのは1行（改行を含めない）。"""
        for spec in da.all_data_kinds():
            assert "\n" not in spec.note_text("OpenAI"), spec.data_kind
