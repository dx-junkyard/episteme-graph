"""core/llm_worker/single_shot.py — 単発 LLM 呼び出し + JSON 取り出しの共通骨格。

検証の軸:

1. ``extract_json`` の既定経路が正本（``client.py::parse_json_response``）と
   同じ入力に同じ結果を返すこと（フェンス・前後プロース・最外ブレース）。
2. opt-in（LaTeX バックスラッシュ修復 / 切り詰め復元）が既定経路を変えないこと。
3. LLM 関数が**注入**され、モジュール側で ``core.llm`` を掴まないこと
   （呼び出し側の monkeypatch 面を保つ設計上の要）。
4. 失敗時に ``degraded`` があれば返し、無ければ ``LLMSingleShotError``。
5. 修復再呼び出しは **高々1回**。
"""

from __future__ import annotations

import pytest

from core.llm_worker.client import parse_json_response
from core.llm_worker.single_shot import (
    LLMSingleShotError,
    extract_json,
    json_call,
    strip_code_fence,
    structured_call,
)


# ---------------------------------------------------------------------------
# 1. extract_json — 既定経路
# ---------------------------------------------------------------------------


class TestExtractJsonDefaultPath:
    PARITY_CASES = [
        '{"a": 1}',
        '  {"a": 1}  ',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        'ここに説明があります。\n{"a": 1}\nおしまい。',
        '```json\n{"nested": {"b": [1, 2]}}\n```',
    ]

    @pytest.mark.parametrize("raw", PARITY_CASES)
    def test_matches_canonical_parse_json_response(self, raw):
        """正本と同じ入力に同じ dict を返す（アルゴリズムの同一性）。"""
        assert extract_json(raw) == parse_json_response(raw)

    def test_strips_language_tagged_fence(self):
        assert extract_json('```JSON\n{"a": 1}\n```') == {"a": 1}

    def test_falls_back_to_outer_braces(self):
        assert extract_json('前置き {"a": 1} 後書き') == {"a": 1}

    def test_reads_control_characters_inside_strings(self):
        """移行元のコピー群と同じ ``strict=False``（生の改行を含む応答を落とさない）。"""
        assert extract_json('{"a": "1行目\n2行目"}') == {"a": "1行目\n2行目"}

    @pytest.mark.parametrize("raw", ["", "   ", "説明だけで JSON がない", "{壊れた", '[1, 2]', "null"])
    def test_raises_when_no_json_object(self, raw):
        with pytest.raises(ValueError):
            extract_json(raw)

    def test_prefers_the_object_when_top_level_is_an_array(self):
        """配列は dict ではないので候補として採らない（次の候補＝最外ブレース）。"""
        assert extract_json('[{"a": 1}]') == {"a": 1}


class TestStripCodeFence:
    def test_only_strips_when_fenced(self):
        assert strip_code_fence("plain text") == "plain text"
        assert strip_code_fence("```json\nbody\n```") == "body"


# ---------------------------------------------------------------------------
# 2. opt-in（LaTeX バックスラッシュ / 切り詰め復元）
# ---------------------------------------------------------------------------


class TestExtractJsonOptIns:
    LATEX = '{"text": "係数 \\Lambda と \\sum_i x_i"}'

    def test_latex_backslashes_fail_without_repair(self):
        with pytest.raises(ValueError):
            extract_json(self.LATEX)

    def test_latex_backslashes_recovered_with_repair(self):
        parsed = extract_json(self.LATEX, repair_backslashes=True)
        assert parsed["text"] == "係数 \\Lambda と \\sum_i x_i"

    def test_repair_leaves_json_escape_letters_as_escapes(self):
        """既知の限界（移行元と同じ挙動）: ``\\frac`` の ``\\f`` は JSON の改ページ escape。

        修復は「JSON が許さない単独バックスラッシュ」だけを2重化する規則なので、
        ``b f n r t u`` で始まる LaTeX コマンドは元から復元できない。共通化に
        あたって挙動を変えていないことを明示的に固定する。
        """
        parsed = extract_json('{"text": "\\frac{1}{2}"}', repair_backslashes=True)
        assert parsed["text"] == "\x0crac{1}{2}"

    def test_repair_keeps_valid_escapes_untouched(self):
        raw = '{"text": "行1\\n行2", "path": "a\\/b"}'
        assert extract_json(raw, repair_backslashes=True) == extract_json(raw)

    def test_repair_does_not_change_the_default_result(self):
        raw = '```json\n{"a": 1}\n```'
        assert extract_json(raw, repair_backslashes=True) == {"a": 1}

    def test_truncated_json_recovered_only_when_requested(self):
        raw = '{"items": [{"id": "a"}, {"id": "b"}'
        with pytest.raises(ValueError):
            extract_json(raw)
        parsed = extract_json(raw, recover_truncated=True)
        assert [item["id"] for item in parsed["items"]] == ["a", "b"]

    def test_truncation_recovery_still_raises_when_nothing_salvageable(self):
        with pytest.raises(ValueError):
            extract_json("まったく JSON ではない", recover_truncated=True)


# ---------------------------------------------------------------------------
# 3-5. json_call
# ---------------------------------------------------------------------------


class _Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return response


class TestJsonCall:
    def test_uses_the_injected_call_and_returns_parsed_json(self):
        call = _Recorder('```json\n{"ok": true}\n```')
        assert json_call("質問", call=call) == {"ok": True}
        assert call.calls[0]["messages"] == [{"role": "user", "content": "質問"}]

    def test_passes_messages_through_unchanged(self):
        call = _Recorder('{"ok": true}')
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        json_call(messages, call=call)
        assert call.calls[0]["messages"] == messages

    def test_omits_kwargs_the_caller_did_not_pass(self):
        call = _Recorder('{"ok": true}')
        json_call("q", call=call, temperature=0.1)
        assert call.calls[0] == {
            "messages": [{"role": "user", "content": "q"}],
            "temperature": 0.1,
        }

    def test_forwards_an_explicit_none(self):
        """「省略」と「明示的な None」を潰さない（M層の解決を入口へ委ねる意思表示）。"""
        call = _Recorder('{"ok": true}')
        json_call("q", call=call, model=None)
        assert call.calls[0]["model"] is None

    def test_forwards_model_and_effort_when_given(self):
        call = _Recorder('{"ok": true}')
        json_call("q", call=call, model="gpt-x", reasoning_effort="low", max_tokens=10)
        assert call.calls[0]["model"] == "gpt-x"
        assert call.calls[0]["reasoning_effort"] == "low"
        assert call.calls[0]["max_tokens"] == 10

    def test_raises_single_shot_error_without_degraded(self):
        call = _Recorder("JSON ではない")
        with pytest.raises(LLMSingleShotError) as exc:
            json_call("q", call=call, log_label="unit")
        assert exc.value.raw == "JSON ではない"
        assert isinstance(exc.value.cause, ValueError)

    def test_returns_degraded_instead_of_raising(self):
        call = _Recorder("JSON ではない")
        assert json_call("q", call=call, degraded={}) == {}

    def test_degraded_none_is_a_valid_value(self):
        call = _Recorder("JSON ではない")
        assert json_call("q", call=call, degraded=None) is None

    def test_transport_failure_is_degraded_too(self):
        call = _Recorder(RuntimeError("boom"))
        assert json_call("q", call=call, degraded={"fallback": True}) == {"fallback": True}
        assert len(call.calls) == 1

    def test_repair_re_call_happens_at_most_once(self):
        call = _Recorder("壊れている", "まだ壊れている")
        with pytest.raises(LLMSingleShotError):
            json_call("q", call=call, repair_prompt="JSON だけを返してください")
        assert len(call.calls) == 2
        assert call.calls[1]["messages"][-1] == {
            "role": "user",
            "content": "JSON だけを返してください",
        }

    def test_repair_recovers(self):
        call = _Recorder("壊れている", '{"ok": true}')
        assert json_call("q", call=call, repair_prompt="やり直して") == {"ok": True}

    def test_validate_failure_triggers_repair(self):
        call = _Recorder('{"a": 1}', '{"required": 2}')

        def _validate(parsed):
            if "required" not in parsed:
                raise ValueError("required 欠落")

        assert json_call("q", call=call, validate=_validate, repair_prompt="足りません") == {
            "required": 2
        }

    def test_no_repair_prompt_means_a_single_call(self):
        call = _Recorder("壊れている")
        json_call("q", call=call, degraded={})
        assert len(call.calls) == 1


# ---------------------------------------------------------------------------
# structured_call
# ---------------------------------------------------------------------------


class _Model:
    def __init__(self, **fields):
        self.fields = fields


class TestStructuredCall:
    def test_returns_the_structured_result_untouched(self):
        sentinel = _Model(a=1)
        calls: list[dict] = []

        def _structured(**kwargs):
            calls.append(kwargs)
            return sentinel

        assert structured_call("q", _Model, structured_fn=_structured) is sentinel
        assert calls[0]["response_format"] is _Model
        assert "model" not in calls[0], "呼び出し側が渡していない引数は素通ししない"

    def test_forwards_an_explicit_none_model(self):
        """明示的な ``model=None`` はそのまま渡す（呼び出し面を検査するテストがある）。"""
        calls: list[dict] = []

        def _structured(*, messages, response_format, model):
            calls.append({"model": model})
            return _Model()

        structured_call("q", _Model, structured_fn=_structured, model=None)
        assert calls[0]["model"] is None

    def test_passes_model_when_requested(self):
        calls: list[dict] = []

        def _structured(*, messages, response_format, model):
            calls.append({"model": model})
            return _Model()

        structured_call("q", _Model, structured_fn=_structured, model="gpt-x")
        assert calls[0]["model"] == "gpt-x"

    def test_text_fallback_is_used_only_when_enabled(self):
        def _structured(**kwargs):
            raise RuntimeError("structured unsupported")

        def _text(**kwargs):
            raise AssertionError("text_fallback=False では呼ばない")

        with pytest.raises(LLMSingleShotError):
            structured_call("q", _Model, structured_fn=_structured, text_fn=_text)

    def test_text_fallback_parses_json(self):
        def _structured(**kwargs):
            raise RuntimeError("structured unsupported")

        text = _Recorder('```json\n{"a": 1}\n```')
        parsed = structured_call(
            "q", _Model, structured_fn=_structured, text_fn=text, text_fallback=True
        )
        assert parsed == {"a": 1}
        assert len(text.calls) == 1

    def test_text_fallback_can_repair_latex_backslashes(self):
        def _structured(**kwargs):
            raise RuntimeError("boom")

        text = _Recorder('{"body": "\\Lambda"}')
        parsed = structured_call(
            "q",
            _Model,
            structured_fn=_structured,
            text_fn=text,
            text_fallback=True,
            repair_backslashes=True,
        )
        assert parsed == {"body": "\\Lambda"}

    def test_text_fallback_failure_degrades_when_asked(self):
        def _boom(**kwargs):
            raise RuntimeError("boom")

        assert (
            structured_call(
                "q",
                _Model,
                structured_fn=_boom,
                text_fn=_boom,
                text_fallback=True,
                degraded={},
            )
            == {}
        )

    def test_text_fallback_failure_raises_without_degraded(self):
        def _boom(**kwargs):
            raise RuntimeError("boom")

        with pytest.raises(LLMSingleShotError):
            structured_call(
                "q", _Model, structured_fn=_boom, text_fn=_boom, text_fallback=True
            )
