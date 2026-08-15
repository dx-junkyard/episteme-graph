"""静かな計器（教員支援 Phase 4 §3）の core テスト。

対象:
- コスト見通し ``core/llm_usage/forecast.py``（§3.1）: fail-open・show=false 既定・
  数値キー非返却（TT2）・固定メッセージ・保守判定の閾値挙動。
- WMレンズ ``core/lecture_wm.py``（§3.2）: 非LLM・決定論・textual 縮退（degraded）・
  最低段の省略（平常時は視界に無い）・fact 文の逐語要素・段階ラベルが label_vocab 由来。
- 学習者データ非参照（TT5）: 両モジュールのソースに学習者テーブル語彙が無い。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core import label_vocab, lecture_wm  # noqa: E402
from core.llm_usage import forecast  # noqa: E402

_FORECAST_SRC = (BACKEND / "core" / "llm_usage" / "forecast.py").read_text(encoding="utf-8")
_LECTURE_WM_SRC = (BACKEND / "core" / "lecture_wm.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# コスト見通し（§3.1）
# ---------------------------------------------------------------------------


class TestForecastShape:
    def test_result_keys_are_exactly_show_and_message(self, monkeypatch):
        """TT2: 数値・残回数・トークン数を返さない — キーは show / message のみ。"""
        monkeypatch.setattr(forecast, "_gate_remainings", lambda analyze: [("x", 0, 20)])
        result = forecast.forecast_run_capacity()
        assert set(result.keys()) == {"show", "message"}
        assert isinstance(result["show"], bool)
        assert isinstance(result["message"], str)
        assert not any(isinstance(v, (int, float)) and not isinstance(v, bool) for v in result.values())

    def test_message_is_the_fixed_fact_line_with_no_numbers(self, monkeypatch):
        monkeypatch.setattr(forecast, "_gate_remainings", lambda analyze: [("x", 0, 20)])
        result = forecast.forecast_run_capacity()
        assert result["show"] is True
        assert result["message"] == (
            "この規模の処理は、今日のAI利用枠に収まらない可能性があります。"
            "分けて実行することもできます"
        )
        # 仮説文体（CostGate の不厳密さを吸収する監査済み表現）。
        assert "可能性があります" in result["message"]
        assert not any(ch.isdigit() for ch in result["message"])

    def test_message_is_empty_when_not_shown(self, monkeypatch):
        monkeypatch.setattr(forecast, "_gate_remainings", lambda analyze: [("x", 20, 20)])
        assert forecast.forecast_run_capacity() == {"show": False, "message": ""}


class TestForecastConservativeJudgement:
    @pytest.mark.parametrize(
        "remaining,limit,expected",
        [
            (20, 20, False),   # 余裕あり
            (0, 20, True),     # 使い切り
            (4, 20, True),     # 残 20% < 25%
            (5, 20, False),    # 残 25% は閾値ちょうど（< でない）
            (10, 0, True),     # 上限 0 は常に「収まらない可能性」
        ],
    )
    def test_low_remaining_ratio_threshold(self, monkeypatch, remaining, limit, expected):
        monkeypatch.setattr(
            forecast, "_gate_remainings", lambda analyze: [("x", remaining, limit)]
        )
        assert forecast.forecast_run_capacity()["show"] is expected

    def test_minimum_across_gates_decides(self, monkeypatch):
        """複数カウンタの最小残数による近似（§5 精査⑤）。"""
        monkeypatch.setattr(
            forecast,
            "_gate_remainings",
            lambda analyze: [("a", 20, 20), ("b", 15, 20), ("c", 1, 20)],
        )
        assert forecast.forecast_run_capacity()["show"] is True

    def test_threshold_is_documented_as_an_approximation(self):
        """発明した閾値は近似であることをコメントに明記する（設計指示）。"""
        assert "近似" in _FORECAST_SRC
        assert "FORECAST_LOW_REMAINING_RATIO" in _FORECAST_SRC


class TestForecastFailOpen:
    def test_capacity_forecast_fails_open(self, monkeypatch):
        def _boom(analyze):
            raise RuntimeError("gate unavailable")

        monkeypatch.setattr(forecast, "_gate_remainings", _boom)
        assert forecast.forecast_run_capacity() == {"show": False, "message": ""}

    def test_document_forecast_fails_open_on_estimate_error(self, monkeypatch):
        def _boom(session, document_id, *, analyze_images=False):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(forecast, "estimate_document_run", _boom)
        assert forecast.forecast_document_run(object(), "doc-1") == {
            "show": False, "message": "",
        }

    def test_document_forecast_defaults_to_hidden_without_material(self, monkeypatch):
        """document 不在（estimate None）・見積り 0 は show=false 既定。"""
        monkeypatch.setattr(
            forecast, "estimate_document_run",
            lambda session, document_id, *, analyze_images=False: None,
        )
        assert forecast.forecast_document_run(object(), "doc-1")["show"] is False

        monkeypatch.setattr(
            forecast, "estimate_document_run",
            lambda session, document_id, *, analyze_images=False: {
                "total_tokens_range": [0, 0]
            },
        )
        assert forecast.forecast_document_run(object(), "doc-1")["show"] is False

    def test_document_forecast_combines_estimate_with_gate_remaining(self, monkeypatch):
        monkeypatch.setattr(
            forecast, "estimate_document_run",
            lambda session, document_id, *, analyze_images=False: {
                "total_tokens_range": [100, 300]
            },
        )
        monkeypatch.setattr(forecast, "_gate_remainings", lambda analyze: [("x", 0, 20)])
        result = forecast.forecast_document_run(object(), "doc-1")
        assert result["show"] is True
        assert set(result.keys()) == {"show", "message"}

        monkeypatch.setattr(forecast, "_gate_remainings", lambda analyze: [("x", 20, 20)])
        assert forecast.forecast_document_run(object(), "doc-1")["show"] is False


# ---------------------------------------------------------------------------
# WMレンズ（§3.2）
# ---------------------------------------------------------------------------


_HIGH_FORMULAS = [
    {"latex": r"\beta = \Omega_{m} \gamma"},
    {"latex": r"\beta + \gamma"},
]


class TestWmLensDeterminism:
    def test_same_input_gives_the_same_output(self):
        first = lecture_wm.wm_for_slide("本文", _HIGH_FORMULAS, known_symbols=None)
        second = lecture_wm.wm_for_slide("本文", _HIGH_FORMULAS, known_symbols=None)
        assert first == second
        assert first is not None

    def test_symbol_extraction_is_ordered_and_deduped(self):
        candidates = lecture_wm.extract_symbol_candidates("", _HIGH_FORMULAS)
        assert candidates == ["β", "Ω_m", "γ"]


class TestWmLensThresholdAndOmission:
    def test_lowest_level_returns_none(self):
        """平常時は視界に無い: 最低段（few）は wm 自体を返さない。"""
        assert lecture_wm.wm_for_slide("", [{"latex": "E=mc^2"}], known_symbols=None) is None
        assert lecture_wm.wm_for_slide("ただの本文です。", [], known_symbols=None) is None

    def test_annotate_slides_omits_the_wm_key_on_low_slides(self):
        slides = [
            {"display_text": "ただの本文", "formulas": []},
            {"display_text": "", "formulas": list(_HIGH_FORMULAS)},
        ]
        lecture_wm.annotate_slides(slides, document_id=None)
        assert "wm" not in slides[0]
        assert "wm" in slides[1]

    def test_level_label_comes_from_label_vocab(self):
        wm = lecture_wm.wm_for_slide("", _HIGH_FORMULAS, known_symbols=None)
        assert wm["level"] in ("many", "very_many")
        assert wm["level_label"] == label_vocab.WM_INTERACTION_LABELS[wm["level"]]

    def test_registry_match_narrows_the_count(self):
        """document 突合時は registry と突合できた distinct 記号のみ数える。"""
        # β と Ω_m だけが registry 既知 → matched 2 + 数式 2 = 4 → few（省略）。
        assert (
            lecture_wm.wm_for_slide("", _HIGH_FORMULAS, known_symbols={"β", "Ω_m"}) is None
        )
        # 3記号とも既知なら 3 + 2 = 5 → many。
        wm = lecture_wm.wm_for_slide("", _HIGH_FORMULAS, known_symbols={"β", "Ω_m", "γ"})
        assert wm is not None and wm["level"] == "many"
        assert "degraded" not in wm


class TestWmLensFactLine:
    def test_fact_contains_the_verbatim_elements(self):
        """設計書 §3.2 の型: 記号列挙（・区切り）+ 数式n件 + 音声制約の逐語。"""
        wm = lecture_wm.wm_for_slide("", _HIGH_FORMULAS, known_symbols={"β", "Ω_m", "γ"})
        assert "相互に依存する記号 β・Ω_m・γ と数式2件が同時に現れます" in wm["fact"]
        assert "読み上げ音声は添字・上付きを運べません。" in wm["fact"]

    def test_symbols_listed_in_fact_are_capped_at_five(self):
        formulas = [{"latex": r"\alpha \beta \gamma \delta \epsilon \zeta \eta"}]
        wm = lecture_wm.wm_for_slide("", formulas, known_symbols=None)
        assert wm is not None
        listed = wm["fact"].split("記号 ")[1].split(" ")[0]
        assert listed.count("・") == 4  # 5記号 = 区切り4つ
        assert "など" in wm["fact"]

    def test_no_raw_numeric_fields_in_wm(self):
        """生値フィールドを返さない（件数は日本語文中の事実としてのみ）。"""
        wm = lecture_wm.wm_for_slide("", _HIGH_FORMULAS, known_symbols=None)
        assert set(wm.keys()) <= {"level", "level_label", "fact", "degraded"}
        assert not any(
            isinstance(v, (int, float)) and not isinstance(v, bool) for v in wm.values()
        )


class TestWmLensTextualDegradation:
    def test_textual_mode_is_flagged_and_explained(self):
        """記号照合が textual になる縮退は degraded + 事実文で正直に表示（§3.2）。"""
        wm = lecture_wm.wm_for_slide("", _HIGH_FORMULAS, known_symbols=None)
        assert wm["degraded"] is True
        assert lecture_wm.WM_DEGRADED_NOTICE in wm["fact"]

    def test_registry_failure_falls_back_to_textual(self, monkeypatch):
        """symbol_records が読めないときは None（textual 縮退）。プレビューは止めない。"""
        def _boom(document_id):
            raise RuntimeError("artifact unavailable")

        monkeypatch.setattr("core.deliberation.refs.symbol_records", _boom)
        assert lecture_wm.build_symbol_lookup("doc-1") is None

    def test_no_document_id_never_touches_the_registry(self, monkeypatch):
        def _boom(document_id):  # pragma: no cover - 呼ばれないことの検証
            raise AssertionError("must not read the registry without a document_id")

        monkeypatch.setattr("core.deliberation.refs.symbol_records", _boom)
        assert lecture_wm.build_symbol_lookup(None) is None
        assert lecture_wm.build_symbol_lookup("") is None
        slides = [{"display_text": "", "formulas": list(_HIGH_FORMULAS)}]
        lecture_wm.annotate_slides(slides, document_id=None)
        assert slides[0]["wm"]["degraded"] is True


# ---------------------------------------------------------------------------
# モジュール規律（非LLM・素材由来入力のみ・学習者データ非参照, TT5）
# ---------------------------------------------------------------------------


class TestModuleDiscipline:
    @pytest.mark.parametrize("src,name", [
        (_LECTURE_WM_SRC, "core/lecture_wm.py"),
        (_FORECAST_SRC, "core/llm_usage/forecast.py"),
    ])
    def test_no_learner_data_references(self, src, name):
        """TT5: 計器の入力は素材由来のみ — 学習者テーブルに触れない。"""
        for forbidden in ("interest_traces", "learning_states", "learning_chat_history"):
            assert forbidden not in src, f"{name} must not reference {forbidden}"

    @pytest.mark.parametrize("src,name", [
        (_LECTURE_WM_SRC, "core/lecture_wm.py"),
        (_FORECAST_SRC, "core/llm_usage/forecast.py"),
    ])
    def test_no_fastapi_or_llm_calls(self, src, name):
        for forbidden in ("fastapi", "openai", "generate_text", "generate_structured"):
            assert forbidden not in src, f"{name} must not import/call {forbidden}"

    def test_wm_lens_does_not_write_split_markers(self):
        """TT6: 分割マーカー ``===`` を書くのは教員の手のみ — 自動挿入コードを持たない。"""
        assert '"==="' not in _LECTURE_WM_SRC
        assert "'==='" not in _LECTURE_WM_SRC

    def test_wm_scale_lives_in_label_vocab(self):
        """段階の正本は label_vocab（固定閾値型。独自辞書禁止, TT2）。"""
        assert "WM_INTERACTION_LEVEL_SCALE" in _LECTURE_WM_SRC
        assert label_vocab.WM_INTERACTION_LEVEL_SCALE.labels == ("very_many", "many", "few")
        assert label_vocab.WM_INTERACTION_DENSITY.labels == ("非常に多い", "多い", "少ない")
        # 未測定は最も慎重な段階（few = 表示省略）へ倒れる。
        assert label_vocab.WM_INTERACTION_LEVEL_SCALE.label_for(None) == "few"
