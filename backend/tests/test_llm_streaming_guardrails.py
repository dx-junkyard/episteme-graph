"""ストリーミング Phase 3-a の構造ガードレール（設計書 §9 guardrails）。

正本: ``docs/features/llm_response_streaming_design.md``（ST1〜ST9 / §3.2 / §3.3 / §6）。

| # | 条項 | ここで固定する事実 |
|---|---|---|
| ST1/ST5 | 途中経過を正本にしない | 中断は `GeneratorExit` で後処理へ進まない構造（`except Exception` のまま） |
| ST2 | quota は最初の1バイトより前 | `yield ("start"` が `_consume_quota()` より後 |
| ST3/§6 | U層の帰属を落とさない | `operation` は "chat" のまま（`OPERATIONS` に "stream" 無し）・`metadata.streamed` |
| ST4 | 衛生は delta に掛ける | `_sse_frames` に `strip_control_sequences` がある |
| ST6 | delta に本文以外を載せない | delta フレームのペイロードは `{"t": ...}` だけ |
| ST7 | 非ストリーム API は不変 | 逐語3点・patch seam（`generate_text`）・`learning_chat` のスライス前提 |
| §3.2 | contextvar を yield で跨がない | `_learning_chat_core` の yield が `usage_context` / `model_override` の with の内側に無い |
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(BACKEND), str(BACKEND / "api")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tests.guardrail_helpers import (  # noqa: E402
    assert_module_tree_does_not_import,
    extract_function_source,
)

_LEARNING_PATH = BACKEND / "api" / "routes" / "learning.py"
_LEARNING_SRC = _LEARNING_PATH.read_text(encoding="utf-8")
_CORE_SRC = extract_function_source(_LEARNING_SRC, "_learning_chat_core")
_LLM_SRC = (BACKEND / "core" / "llm.py").read_text(encoding="utf-8")
_LEARNING_TREE = ast.parse(_LEARNING_SRC)


def _function_node(name: str) -> ast.FunctionDef:
    for node in ast.walk(_LEARNING_TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} が learning.py に無い")


# ===========================================================================
# core/llm.py 側（§3.1 / §6）
# ===========================================================================


class TestStreamGeneratorIsInCoreLlm:
    def test_generate_text_stream_lives_in_core_llm(self):
        from core.llm import generate_text_stream
        import inspect

        assert inspect.isgeneratorfunction(generate_text_stream)
        params = inspect.signature(generate_text_stream).parameters
        assert "usage_ctx" in params
        assert params["usage_ctx"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_core_llm_does_not_import_fastapi(self):
        assert_module_tree_does_not_import(BACKEND / "core" / "llm.py", ["fastapi"])

    def test_openai_call_asks_for_usage_in_the_stream(self):
        body = extract_function_source(_LLM_SRC, "generate_text_stream")
        assert "stream=True" in body
        assert 'stream_options={"include_usage": True}' in body

    def test_observation_is_in_a_finally_block(self):
        body = extract_function_source(_LLM_SRC, "generate_text_stream")
        assert "finally:" in body
        assert body.count("observe_chat(") == 1
        assert '"streamed": True' in body
        assert '"client_aborted"' in body
        # operation は "chat" のまま（転送方式は操作種別ではない = §6）。
        assert 'operation="chat"' in body
        assert '"stream"' not in body.replace('"streamed"', "")

    def test_operation_vocabulary_has_no_stream(self):
        from core.llm_usage.schema import OPERATIONS

        assert "stream" not in OPERATIONS
        assert "chat" in OPERATIONS

    def test_existing_generate_text_still_takes_no_stream_flag(self):
        """ST7: 既存3関数は非改変（ストリームは追加であって置き換えではない）。"""
        import inspect

        from core.llm import generate_text

        assert "stream" not in inspect.signature(generate_text).parameters


# ===========================================================================
# 継ぎ目（§3.3）
# ===========================================================================


class TestSeam:
    def test_core_is_a_generator_with_a_stream_kwarg(self):
        import inspect

        import routes.learning as learning_mod

        assert inspect.isgeneratorfunction(learning_mod._learning_chat_core)
        params = inspect.signature(learning_mod._learning_chat_core).parameters
        assert params["stream"].kind is inspect.Parameter.KEYWORD_ONLY
        assert params["stream"].default is False

    def test_stream_answer_is_called_exactly_once(self):
        assert _CORE_SRC.count("yield from _stream_answer(") == 1

    def test_generate_text_stream_is_called_in_one_place_only(self):
        """ストリーム生成の入口は `_stream_answer` 1箇所（§3.3）。"""
        assert _LEARNING_SRC.count("generate_text_stream(") == 1
        assert "    generate_text_stream,\n" in _LEARNING_SRC  # import は行1本
        body = extract_function_source(_LEARNING_SRC, "_stream_answer")
        assert body.count("generate_text_stream(") == 1

    def test_start_event_comes_after_the_quota(self):
        """ST2: 最初の1バイトより前に CostGate を消費している。"""
        quota_idx = _CORE_SRC.index("\n    _consume_quota()")
        start_idx = _CORE_SRC.index('yield ("start"')
        assert quota_idx < start_idx

    def test_start_event_comes_after_the_screen_context_injection(self):
        """SA層 Phase 4 の注入は継ぎ目の前（§3.3）。"""
        injection_idx = _CORE_SRC.index("_selection_block = render_selection_block(")
        start_idx = _CORE_SRC.index('yield ("start"')
        assert injection_idx < start_idx

    def test_non_stream_path_still_calls_generate_text_as_a_module_attribute(self):
        """ST7: 20箇所の patch seam（`api.routes.learning.generate_text`）を維持する。"""
        assert "            answer = generate_text(\n" in _CORE_SRC
        assert "if not stream:" in _CORE_SRC

    def test_both_chat_routes_go_through_the_driver(self):
        for fn in ("learning_chat", "document_discuss_chat"):
            body = extract_function_source(_LEARNING_SRC, fn)
            assert "_run_learning_turn(" in body, fn

    def test_learning_chat_slice_still_contains_the_core_body(self):
        """新ルートを `learning_chat` と `_learning_chat_core` の間に挟んでいない
        （`test_mirroring_prompt_guardrails.py` のスライス前提）。"""
        chat_fn = _LEARNING_SRC.split("def learning_chat(")[1].split("\n@router")[0]
        for literal in (
            "extract_mirror(clean_answer, body.message)",
            "_persisted = persist_chat_history(",
            "record_interest_trace(",
        ):
            assert literal in chat_fn, literal

    def test_core_verbatim_anchors_unchanged(self):
        """ST7: 既存ガードレールが固定する逐語3点（生成器化の回帰検出）。"""
        assert "window_history(body.history, max_messages=20, max_chars=2000)" in _LEARNING_SRC
        assert "if _is_discuss:\n        _scaffold_user_instruction = (" in _LEARNING_SRC
        assert "messages: list[dict] = [" in _LEARNING_SRC


# ===========================================================================
# §3.2: contextvar を yield で跨がない
# ===========================================================================


class TestNoYieldInsideContextVarBlocks:
    def test_no_yield_inside_usage_context_or_model_override(self):
        node = _function_node("_learning_chat_core")
        offending: list[int] = []
        for with_node in ast.walk(node):
            if not isinstance(with_node, (ast.With, ast.AsyncWith)):
                continue
            items_src = " ".join(
                ast.get_source_segment(_LEARNING_SRC, item.context_expr) or ""
                for item in with_node.items
            )
            if "usage_context(" not in items_src and "model_override(" not in items_src:
                continue
            for child in ast.walk(with_node):
                if isinstance(child, (ast.Yield, ast.YieldFrom)):
                    offending.append(getattr(child, "lineno", -1))
        assert not offending, f"contextvar ブロックの内側に yield がある: {offending}"

    def test_effective_model_is_resolved_inside_the_override(self):
        """M1: 実効モデルは contextvar の内側で1回だけ確定し、値で渡す（§3.2）。"""
        block = _CORE_SRC.split("with usage_context(_chat_feature")[1].split("except Exception:")[0]
        assert '_effective_model = resolve_model("learning_chat_llm_model", fallback="analysis")' in block
        assert "model=_effective_model," in block


# ===========================================================================
# ST4 / ST6 / ST8: フレームの中身
# ===========================================================================


class TestFrames:
    def test_hygiene_is_applied_to_deltas(self):
        body = extract_function_source(_LEARNING_SRC, "_sse_frames")
        assert "strip_control_sequences(" in body

    def test_delta_payload_has_only_the_text_key(self):
        body = extract_function_source(_LEARNING_SRC, "_sse_frames")
        for line in body.splitlines():
            if '_sse_frame("delta"' not in line:
                continue
            payload = line.split('_sse_frame("delta",', 1)[1]
            assert payload.strip().startswith('{"t":'), line
            for forbidden in ("sources", "tier", "confidence", "tokens", "score"):
                assert forbidden not in payload, line

    def test_final_is_the_whole_response_dto(self):
        """ST7: `final` はキーを間引かない。"""
        body = extract_function_source(_LEARNING_SRC, "_sse_frames")
        assert '_sse_frame("final", response.model_dump())' in body

    def test_no_numbers_are_sent_to_the_learner(self):
        """ST8: トークン数・経過秒・残回数を学習者向けフレームに載せない。"""
        stream_src = "\n".join([
            extract_function_source(_LEARNING_SRC, "_sse_frames"),
            extract_function_source(_LEARNING_SRC, "learning_chat_stream"),
            extract_function_source(_LEARNING_SRC, "learning_client_features"),
        ])
        for forbidden in (
            "tokens", "prompt_tokens", "completion_tokens", "elapsed",
            "remaining", "usage", "duration_ms", "cost",
        ):
            assert forbidden not in stream_src, forbidden

    def test_client_features_exposes_a_single_boolean(self):
        body = extract_function_source(_LEARNING_SRC, "learning_client_features")
        assert '"chat_streaming"' in body
        assert "model" not in body.split('"""')[-1]


# ===========================================================================
# ST1 / ST5: 中断した往復は記録しない
# ===========================================================================


class TestAbortDoesNotPersist:
    def test_core_catches_exception_not_base_exception(self):
        """`GeneratorExit` は BaseException なので `except Exception` を素通りし、
        保存・痕跡へ進まない（ST1/ST5 の非保存はこの構造で成立する）。"""
        block = _CORE_SRC.split("yield from _stream_answer(")[1][:600]
        assert "except Exception:" in block
        assert "except BaseException" not in block
        assert "except GeneratorExit" not in block

    def test_frames_forward_close_to_the_core_generator(self):
        body = extract_function_source(_LEARNING_SRC, "_sse_frames")
        assert "except GeneratorExit:" in body
        assert "gen.close()" in body


# ===========================================================================
# ST9: 段階導入
# ===========================================================================


class TestFeatureFlag:
    def test_setting_defaults_to_off(self):
        from core.config import Settings

        assert Settings.model_fields["learning_chat_streaming_enabled"].default is False

    def test_stream_route_is_gated_by_the_flag(self):
        body = extract_function_source(_LEARNING_SRC, "learning_chat_stream")
        assert "learning_chat_streaming_enabled" in body
        assert "status_code=404" in body

    def test_first_next_happens_before_the_streaming_response(self):
        """原則11 / §3.4 手順2: 権限・可視性・429 は 200 を返す前に出す。"""
        body = extract_function_source(_LEARNING_SRC, "learning_chat_stream")
        assert body.index("first = next(gen)") < body.index("return StreamingResponse(")
