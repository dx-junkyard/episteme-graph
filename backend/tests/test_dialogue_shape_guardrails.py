"""応答の骨格（RS1〜RS5）のガードレール。

正本: docs/features/dialogue_response_shape_design.md。
- core/dialogue_shape.py は FastAPI / sqlalchemy / LLM を import しない。
- ``_learning_chat_core`` は骨格の組み立て（assemble）をちょうど 1 回だけ呼ぶ。
- 保存文の形（``\\n\\n---\\n\\n`` + 目印 + 逆質問）を保つ。
- 応答 DTO ``shape`` のキーは固定 3 つで数値を持たない。
- 逆質問を本文に直接継ぎ足す旧経路（``answer + "\\n\\n---\\n\\n" + ...``）へ戻さない。
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from tests.guardrail_helpers import assert_source_does_not_import  # noqa: E402

_CORE_SRC = (BACKEND / "core" / "dialogue_shape.py").read_text(encoding="utf-8")
_LEARNING_SRC = (BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
_SCHEMAS_SRC = (BACKEND / "api" / "schemas.py").read_text(encoding="utf-8")


def _core_fn() -> str:
    return _LEARNING_SRC.split("def _learning_chat_core(")[1].split("\ndef ")[0]


def test_core_is_pure():
    assert_source_does_not_import(
        _CORE_SRC,
        ("fastapi", "sqlalchemy", "core.llm", "openai", "core.postgres", "api", "routes"),
        context="core/dialogue_shape.py",
    )


def test_assemble_called_exactly_once_in_core_chat():
    assert _core_fn().count("assemble_response_shape(") == 1


def test_render_form_keeps_gate_separator():
    from core.dialogue_shape import GATE_SEPARATOR

    assert GATE_SEPARATOR == "\n\n---\n\n"
    assert "GATE_SEPARATOR + shape.gate_marker + shape.gate_text" in _CORE_SRC


def test_no_ad_hoc_gate_concatenation_in_route():
    assert 'answer = answer + "\\n\\n---\\n\\n"' not in _LEARNING_SRC


def test_assemble_runs_before_persist_and_anchor_confirm_uses_shape():
    fn = _core_fn()
    assert fn.index("assemble_response_shape(") < fn.index("_persisted = persist_chat_history(")
    assert "and _response_shape.allows_anchor_confirm" in fn
    assert fn.index("and _response_shape.allows_anchor_confirm") < fn.index(
        "check_and_count_confirm_prompt(current_user"
    )


def test_mirror_core_applied_after_extract_mirror():
    fn = _core_fn()
    pos = fn.index("clean_answer, _mirror = extract_mirror(clean_answer, body.message)")
    assert fn.index("_mirror = mirror_core(_mirror, body.message)") > pos


def test_shape_dto_keys_fixed_and_schema_field():
    from core.dialogue_shape import SHAPE_DTO_KEYS

    assert SHAPE_DTO_KEYS == ("closing_question", "has_gate", "mirror_kept")
    assert "shape: dict | None = None" in _SCHEMAS_SRC
    assert "shape=shape_dto(_response_shape, mirror=_mirror)" in _core_fn()


def test_previous_correction_injected_before_messages_assembly():
    fn = _core_fn()
    assert fn.index("_previous_correction_block(") < fn.index("messages: list[dict] = [")
    # m2: クライアントの body.history ではなくサーバ正本の履歴から読む。
    assert "_previous_correction_block(body.history)" not in fn
    assert "前の回答の訂正を撤回する場合は撤回だと明示してください" in _LEARNING_SRC


def test_frontend_renders_closing_question_without_duplication():
    app = (BACKEND.parent / "frontend" / "public" / "js" / "app.js").read_text(encoding="utf-8")
    assert "function splitClosingQuestion(msg, text)" in app
    assert "linkifyCitations(html, msg) + closingHtml" in app
    assert 'class="closing-question"' in app
    assert "shape: data.shape || null," in app
