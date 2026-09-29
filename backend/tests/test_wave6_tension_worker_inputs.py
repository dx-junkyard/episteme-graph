"""tension / structure_anchor worker の LLM 入力是正（ペルソナ通し受講 第 8 周）。

- IK-0401: 英語の学習者の paraphrase を英語の推量形で受ける（P2 は両言語で維持）。
- IK-0402: 修復プロンプトに「読めなかった出力」を空で渡さない / context blocks を集める。
- IK-0403: 予約疑似トピックの内部 id と内部参照プレースホルダーを LLM 入力に出さない。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.llm_worker.client import LLMOutputNotJSONError, parse_json_response
from core.llm_worker.repair import run_with_repair
from core.structure_anchor import worker as anchor_worker
from core.structure_anchor.input_builder import build_user_content as build_anchor_content
from core.structure_anchor.schema import AnchorContext, QuestionRecord
from core.tension import worker as tension_worker
from core.tension.input_builder import build_context_blocks, cited_chunk_ids_from_hints
from core.tension.prompt import build_instruction, build_repair_prompt
from core.tension.schema import (
    ConversationTurn,
    ConversationWindow,
    TensionMiningResult,
)
from core.tension.validator import learner_language, validate_output
from core.text_hygiene import scrub_internal_placeholders
from core.topic_labels import reserved_topic_label

_BACKEND = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# IK-0401 英語の推量形
# ---------------------------------------------------------------------------

_EN_LEARNER = (
    "If one assumption is wrong, does the 'magnetically subcritical' conclusion still hold?"
)


def _en_window() -> ConversationWindow:
    return ConversationWindow(
        course_id="c1", topic_id="t0", topic_title="T",
        turns=[
            ConversationTurn(turn_id="msg_0000", role="learner", text="Thanks, that is clear."),
            ConversationTurn(turn_id="msg_0001", role="tutor", text="..."),
            ConversationTurn(turn_id="msg_0002", role="learner", text=_EN_LEARNER, tension_hint=True),
        ],
    )


def _en_output(paraphrase: str) -> dict:
    return {
        "candidates": [{
            "tension_type": "boundary_probe",
            "evidence_quote": _EN_LEARNER,
            "turn_ids": ["msg_0002"],
            "target_refs": {"component_ids": [], "chunk_ids": [], "topic_id": "t0", "edge_ids": []},
            "is_tension_not_gap": True,
            "paraphrase": paraphrase,
            "confidence": 0.5,
            "reason": "boundary probe after acknowledgement (hedge + revisit)",
        }],
        "rejected_as_gap": [],
    }


class TestEnglishTentativeParaphrase:
    def test_learner_language_detects_english_and_japanese(self):
        assert learner_language([_EN_LEARNER]) == "en"
        assert learner_language(["カットオフの選び方が恣意的に思えて腑に落ちない"]) == "ja"
        # 日本語の発話に英語の術語・LaTeX が混じっても日本語のまま
        assert learner_language(["DCF 法の B_{POS} の推定が本当に正しいのか気になります"]) == "ja"
        assert learner_language([]) == "ja"

    @pytest.mark.parametrize("paraphrase", [
        "The conclusion may feel fragile, as it rests on the field-strength assumptions.",
        "The robustness of the subcritical conclusion might still seem open.",
        "Perhaps the estimate's assumptions are what keeps this unsettled.",
        "It appears the conclusion could hinge on one estimate.",
    ])
    def test_english_tentative_forms_pass_for_english_learner(self, paraphrase):
        result, errors, _ = validate_output(_en_output(paraphrase), _en_window(), 3)
        assert errors == [], errors
        assert result is not None

    def test_english_paraphrase_without_tentative_form_fails(self):
        _, errors, _ = validate_output(
            _en_output("The conclusion rests on the field-strength assumptions."), _en_window(), 3,
        )
        assert any("tentative" in e for e in errors)

    @pytest.mark.parametrize("paraphrase", [
        "You feel the conclusion may be fragile.",
        "The conclusion is definitely fragile, it may seem.",
        "This must be unsettling, it may seem.",
    ])
    def test_english_assertive_forms_are_rejected(self, paraphrase):
        _, errors, _ = validate_output(_en_output(paraphrase), _en_window(), 3)
        assert any("assertive" in e for e in errors)

    def test_japanese_learner_still_requires_japanese_ending(self):
        window = ConversationWindow(
            course_id="c1", topic_id="t0", topic_title="T",
            turns=[ConversationTurn(
                turn_id="msg_0000", role="learner", text="なんとなく腑に落ちないんです", tension_hint=True,
            )],
        )
        out = _en_output("The conclusion may feel fragile.")
        out["candidates"][0]["evidence_quote"] = "なんとなく腑に落ちない"
        out["candidates"][0]["turn_ids"] = ["msg_0000"]
        _, errors, _ = validate_output(out, window, 3)
        assert any("かもしれません" in e for e in errors)

    def test_prompt_rule_four_names_english_tentative_forms(self):
        text = build_instruction(3)
        assert "In English use a tentative form" in text
        assert "learner's language" in text


# ---------------------------------------------------------------------------
# IK-0402 修復プロンプトの前回出力 / context blocks
# ---------------------------------------------------------------------------


class _SeqClient:
    """complete_json の応答を順に返す（例外インスタンスは raise）。"""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls: list[str] = []

    def complete_json(self, content):
        self.calls.append(content)
        out = self._outputs.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


class TestRepairKeepsPreviousOutput:
    def test_parse_error_carries_raw_text(self):
        with pytest.raises(LLMOutputNotJSONError) as info:
            parse_json_response("I cannot produce JSON right now")
        assert info.value.raw_text == "I cannot produce JSON right now"
        assert isinstance(info.value, ValueError)  # 既存の except ValueError は不変

    def test_broken_braces_also_carry_raw_text(self):
        with pytest.raises(LLMOutputNotJSONError) as info:
            parse_json_response('prefix {"a": 1,, } suffix')
        assert "prefix" in info.value.raw_text

    def test_unreadable_reply_passes_its_raw_text(self):
        client = _SeqClient([
            {"bad": 1},
            LLMOutputNotJSONError("LLM output is not valid JSON", raw_text="half {broken"),
            {"ok": 1},
        ])
        run_with_repair(
            client, "BASE",
            validate=lambda d: (d, [], []) if d.get("ok") else (None, ["rule x"], []),
            build_repair_prompt=build_repair_prompt,
            on_repair_failed=lambda errs: None,
        )
        assert "half {broken" in client.calls[2]

    def test_empty_reply_keeps_last_readable_output_and_its_errors(self):
        """第 8 周の再現: 2 回目がタイムアウトで空 → 3 回目の修復に前の JSON が載る。"""
        previous = {"candidates": [{"paraphrase": "too long"}]}
        client = _SeqClient([
            previous,
            LLMOutputNotJSONError("LLM output is not valid JSON", raw_text=""),
            {"ok": 1},
        ])
        run_with_repair(
            client, "BASE",
            validate=lambda d: (d, [], []) if d.get("ok") else (None, ["paraphrase exceeds 120"], []),
            build_repair_prompt=build_repair_prompt,
            on_repair_failed=lambda errs: None,
        )
        third = client.calls[2]
        assert json.dumps(previous, ensure_ascii=False) in third
        assert "paraphrase exceeds 120" in third
        assert "last readable output" in third

    def test_no_readable_output_at_all_asks_for_full_json(self):
        prompt = build_repair_prompt("", ["output was not valid JSON: x"])
        assert "## Your previous output" not in prompt
        assert "produce the full JSON again" in prompt
        assert "Do not add new candidates" not in prompt


class TestContextBlocks:
    def test_cited_chunk_ids_are_collected_in_order_without_duplicates(self):
        payloads = [
            {"cited_chunk_ids": ["c2", "c1"]},
            {"cited_chunk_ids": ["c1", "c3", ""]},
            "not-a-dict",
            {},
        ]
        assert cited_chunk_ids_from_hints(payloads) == ["c2", "c1", "c3"]

    def test_build_context_blocks_scrubs_placeholders_and_drops_empty_ids(self):
        components, chunks = build_context_blocks(
            [("c1", "to a constant when [[eq_eqcand_inline_blk_001_0009_44_add97c11]]. While"),
             ("", "dropped")],
            [("k1", "DCF field strength"), ("k1", "dup"), (None, "x")],
        )
        assert chunks == [{"id": "c1", "head": "to a constant when （数式）. While"}]
        assert components == [{"id": "k1", "label": "DCF field strength"}]


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, script: dict, log: list):
        self._script = script
        self.log = log

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.log.append((sql, params))
        for key, rows in self._script.items():
            if key in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def _tension_env(monkeypatch):
    tension_worker._session_call_counts.clear()
    tension_worker._daily_call_counts.clear()

    def wire(script):
        log: list = []
        monkeypatch.setattr(tension_worker, "_pg_session", lambda: _FakeSession(script, log))
        agent = MagicMock()
        agent.run.return_value = TensionMiningResult()
        monkeypatch.setattr(tension_worker, "TensionMiningAgent", lambda: agent)
        return log, agent

    yield wire
    tension_worker._session_call_counts.clear()
    tension_worker._daily_call_counts.clear()


_HISTORY = [
    {"role": "user", "content": "なんとなく腑に落ちない"},
    {"role": "assistant", "content": "説明します"},
]


class TestTensionWorkerContext:
    def _base_script(self, course=None):
        return {
            "payload->>'tension_hint' = 'true'": [(
                "id-1",
                {"text": "なんとなく腑に落ちない", "tension_hint": True, "cited_chunk_ids": ["ch-1"]},
                None,
            )],
            "SET analyzed_at = now()": [("id-1",)],
            "FROM learning_chat_history": [(_HISTORY,)],
            "FROM learning_courses": [(course or {"topics": [], "sources": [{"material_id": "m1"}]},)],
        }

    def test_window_gets_cited_chunks_and_their_components(self, _tension_env):
        script = self._base_script()
        script["FROM chunks"] = [("ch-1", "head [[FORMULA_0]] text")]
        script["WHERE primary_chunk_id"] = [("comp-1", "Component A")]
        log, agent = _tension_env(script)
        tension_worker.run_tension_mining("u1", "c1", "t1")
        window = agent.run.call_args.args[0]
        assert window.chunks == [{"id": "ch-1", "head": "head （数式） text"}]
        assert window.components == [{"id": "comp-1", "label": "Component A"}]
        assert window.allowed_chunk_ids() == {"ch-1"}

    def test_falls_back_to_source_document_parent_components(self, _tension_env):
        script = self._base_script()
        script["FROM chunks"] = [("ch-1", "head")]
        script["FROM documents WHERE source_path"] = [("doc-1",)]
        script["parent_agent_component_id IS NULL"] = [("comp-p", "Parent component")]
        log, agent = _tension_env(script)
        tension_worker.run_tension_mining("u1", "c1", "t1")
        window = agent.run.call_args.args[0]
        assert window.components == [{"id": "comp-p", "label": "Parent component"}]
        fallback = [p for s, p in log if "parent_agent_component_id IS NULL" in s]
        assert fallback and fallback[0]["docs"] == ["doc-1"]

    def test_reserved_topic_title_is_the_label(self, _tension_env):
        log, agent = _tension_env(self._base_script())
        tension_worker.run_tension_mining("u1", "c1", "_discussion")
        window = agent.run.call_args.args[0]
        assert window.topic_title == "論文との議論"


# ---------------------------------------------------------------------------
# IK-0403 予約疑似トピック・内部参照プレースホルダー
# ---------------------------------------------------------------------------


class TestReservedTopicLabel:
    def test_labels_match_learning_route_constants(self):
        src = (_BACKEND / "api" / "routes" / "learning.py").read_text(encoding="utf-8")
        from core.discuss import context as dctx
        assert f'DISCUSSION_TOPIC_ID = "{dctx.DISCUSSION_TOPIC_ID}"' in src
        assert f'DISCUSSION_TOPIC_LABEL = "{dctx.DISCUSSION_TOPIC_LABEL}"' in src
        assert f'DOCUMENT_DISCUSSION_TOPIC_LABEL = "{dctx.DOCUMENT_DISCUSSION_TOPIC_LABEL}"' in src

    def test_reserved_topic_label(self):
        assert reserved_topic_label("c1", "_discussion") == "論文との議論"
        assert reserved_topic_label("_doc:abc", "_discussion") == "論文との議論（コース外）"
        assert reserved_topic_label("c1", "t0") is None

    def test_anchor_worker_topic_labels(self):
        title, label = anchor_worker._topic_labels({"topics": []}, "_discussion", course_id="c1")
        assert title == label == "論文との議論"


class TestAnchorPromptHygiene:
    def _context(self, topic_id="_discussion", title="論文との議論"):
        return AnchorContext(
            course_id="c1", topic_id=topic_id, topic_title=title,
            questions=[QuestionRecord(trace_id="tr-1", text="Core 6 とは？")],
            chunks=[{"id": "ch-1", "head": "[[eq_eqcand_inline_blk_002_0021_0_47af4791]]. Previous studies"},
                    {"id": "ch-2", "head": "gauge,\n\n[[FORMULA_0]]\n\nWe parametrize ![[figure:abc-123]]"}],
            claims=[{"id": "cl-1", "text": "see ![[equation:eq_3]]"}],
        )

    def test_internal_placeholders_do_not_reach_the_prompt(self):
        content = build_anchor_content(self._context())
        assert "[[eq_" not in content and "[[FORMULA_" not in content
        assert "equation:eq_3" not in content and "figure:abc" not in content
        assert "（数式）" in content and "（図）" in content
        # 実在 id の列は残す（帰属の対象）
        assert '"ch-1"' in content and '"cl-1"' in content

    def test_reserved_topic_id_is_not_printed(self):
        content = build_anchor_content(self._context())
        assert "_discussion" not in content
        assert "topic_title: 論文との議論" in content

    def test_normal_topic_id_is_still_printed(self):
        content = build_anchor_content(self._context(topic_id="t0", title="問題設定"))
        assert "topic_id: t0" in content

    def test_scrub_leaves_plain_brackets_alone(self):
        assert scrub_internal_placeholders("[1] and [[not a ref]]") == "[1] and [[not a ref]]"
        assert scrub_internal_placeholders("") == ""
