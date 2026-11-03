"""画面文脈アダプター 4-e — 学習チャットが「開いている自分の実行記録」を見る（§11.17）。

対応: 第 21 周 R12（チャットが実行結果を見ていない）/ simulation_bridge_design.md §14.4。

固定するのは:

- core: ``selection.simulation_run_id`` と一致する run だけを事実文（≤8 行）にする。
  入力・理論の出力・模擬観測の値は本人の run なので載る（SB6 の読み替え・§14.1）。
  予想は並べるだけで当否を書かない（SB14）。成功した run は SB1 の 1 行を必ず含み、
  比較の事実より前に置く。内部 ID 形の記号・NaN・真偽値は載せない。入力を mutate しない。
- route: 本人所有・grounding 範囲内の run だけを引く（他人の run・範囲外・UUID でない
  ID は黙って空）。SB1 の 1 行が実際にブロックへ載ったときだけ system に
  ``LEARNING_SIMULATION_OUTPUT_CONSTRAINT`` を足す。casual は画面文脈なし・elicit は
  run を引かない。観測は既存 ``structured_grounding_present`` に相乗り（1 件・payload 空）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path
from unittest.mock import MagicMock

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _path in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from core.assistant_context import (  # noqa: E402
    SCREEN_LEARNING,
    ScreenContext,
    normalize_screen_context,
    resolve,
)
from core.assistant_context import schema as ac_schema  # noqa: E402
from core.assistant_context.resolvers import learning as lr  # noqa: E402
from core.simulation_bridge.schema import (  # noqa: E402
    FACT_NOT_VERIFICATION,
    LEARNING_SIMULATION_OUTPUT_CONSTRAINT,
)
from tests.test_assistant_context_learning_route import (  # noqa: E402,F401
    COURSE_ID,
    CURRENT_USER,
    DOC_ID,
    TOPIC_ID,
    _chat,
    _last_user,
    _make_body,
    _set_generation,
    _system,
    chat_env,
)
from tests.test_stakes_ledger_guardrails import TestClosedWorldVocabulary  # noqa: E402
import routes.learning as learning_mod  # noqa: E402

RUN_ID = "44444444-4444-4444-4444-444444444444"
OTHER_USER = "99999999-9999-9999-9999-999999999999"


def _run(**overrides) -> dict:
    run = {
        "id": RUN_ID,
        "document_id": DOC_ID,
        "program_id": "55555555-5555-5555-5555-555555555555",
        "wiring_id": None,
        "status": "succeeded",
        "owner_user_id": CURRENT_USER["id"],
        "course_id": COURSE_ID,
        "inputs": {"values": {"k": 0.5, "rho": 2}, "params": {}, "seed": 1},
        "prediction": [{"symbol": "M_J", "value": 3.0}],
        "result": {
            "theory_outputs": {"M_J": 2.5},
            "observed": {"M_J": {"mean": 2.4, "std": 0.1, "samples": [2.3, 2.5]}},
            "labels": {"agreement": "おおむね一致", "separation": "分離できる", "reference": "未測定"},
            "statement": "このモデルと配線『(なし)』のもとでは、理論モジュール『定義 → 導出: M_J』について、一致しています。",
            "facts": [
                "このモデルのもとでは、理論の出力と模擬観測はおおむね一致しています。",
                "論文の記載値は入力されていません。",
                FACT_NOT_VERIFICATION,
            ],
            "wiring_name": "",
        },
    }
    run.update(overrides)
    return run


def _ctx(run_id: str = RUN_ID) -> ScreenContext:
    return ScreenContext(
        screen=SCREEN_LEARNING,
        selection={"course_id": COURSE_ID, "topic_id": TOPIC_ID, "simulation_run_id": run_id},
        view={},
    )


def _facts(run: dict | None = None, ctx: ScreenContext | None = None) -> list[str]:
    return lr.resolve_simulation(ctx or _ctx(), {"simulation": run if run is not None else _run()})


# ---------------------------------------------------------------------------
# core
# ---------------------------------------------------------------------------


class TestSelection:
    def test_run_id_is_accepted_only_in_uuid_shape(self):
        assert ac_schema.selected_simulation_run_id({"simulation_run_id": RUN_ID}) == RUN_ID
        assert ac_schema.selected_simulation_run_id({"simulation_run_id": RUN_ID.upper()}) == RUN_ID
        for bad in ("", "run-1", "1; drop", None, 12, RUN_ID + "x"):
            assert ac_schema.selected_simulation_run_id({"simulation_run_id": bad}) == ""
        assert ac_schema.selected_simulation_run_id(None) == ""

    def test_normalize_keeps_the_run_id_in_selection(self):
        ctx = normalize_screen_context(
            {"screen": "learning", "selection": {"simulation_run_id": RUN_ID}}
        )
        assert ctx is not None
        assert ctx.selection["simulation_run_id"] == RUN_ID


class TestResolver:
    def test_facts_carry_the_learners_own_values(self):
        facts = _facts()
        blob = "\n".join(facts)
        assert "理論モジュール『定義 → 導出: M_J』" in facts[0]
        assert "（配線なし）" in facts[0]
        assert "入力: k = 0.5・rho = 2" in blob
        assert "理論の出力: M_J = 2.5" in blob
        assert "M_J = 2.4 ± 0.1" in blob
        assert "段階ラベル: おおむね一致／分離できる／未測定" in blob
        assert "論文の記載値は入力されていません。" in blob

    def test_prediction_is_listed_without_judgement(self):
        line = next(f for f in _facts() if f.startswith("あなたの予想"))
        assert "M_J = 3" in line
        assert "当否は判定していません" in line
        for judgement in ("正解", "不正解", "当たり", "外れ", "点数"):
            assert judgement not in line

    def test_not_verification_line_precedes_the_comparison_facts_and_appears_once(self):
        facts = _facts()
        assert facts.count(FACT_NOT_VERIFICATION) == 1
        comparison = next(i for i, f in enumerate(facts) if f.startswith("比較の事実"))
        assert facts.index(FACT_NOT_VERIFICATION) < comparison
        assert FACT_NOT_VERIFICATION not in facts[comparison]

    def test_at_most_eight_lines(self):
        many = {f"s{i}": float(i) for i in range(20)}
        run = _run(inputs={"values": many})
        run["result"]["theory_outputs"] = many
        facts = _facts(run)
        assert len(facts) <= ac_schema.MAX_LEARNING_SIMULATION_FACTS
        assert any("ほか" in f for f in facts)

    def test_failed_run_has_no_verification_line(self):
        run = _run(status="failed", result={"facts": ["実行環境に到達できません。"]})
        facts = _facts(run)
        assert "比較まで終わっていない実行です" in facts[0]
        assert FACT_NOT_VERIFICATION not in facts

    def test_mismatched_or_missing_run_yields_nothing(self):
        assert _facts(ctx=_ctx("66666666-6666-6666-6666-666666666666")) == []
        assert lr.resolve_simulation(_ctx(), {}) == []
        assert _facts(ctx=_ctx("not-a-uuid")) == []

    def test_internal_id_symbols_and_non_numbers_are_dropped(self):
        run = _run(inputs={"values": {"eq_tex_b12": 1.0, "flag": True, "n": float("nan"), "v": [1, 2, 3, 4, 5]}})
        blob = "\n".join(_facts(run))
        assert "eq_tex_b12" not in blob
        assert "flag" not in blob
        assert "n = " not in blob
        assert "v = (1、2、3、4、…)" in blob

    def test_input_is_not_mutated(self):
        run = _run()
        before = copy.deepcopy(run)
        _facts(run)
        assert run == before

    def test_broken_payload_does_not_raise(self):
        broken = _run(result="oops", inputs=None, prediction="x")
        facts = lr.resolve_simulation(_ctx(), {"simulation": broken})
        assert facts and FACT_NOT_VERIFICATION in facts

    def test_registered_and_reached_through_resolve(self):
        facts = resolve(_ctx(), {"simulation": _run()}, kinds=("simulation",))
        assert facts == _facts()
        assert resolve(_ctx(), {"simulation": _run()}, kinds=("view",)) == []

    def test_no_banned_closed_world_phrase(self):
        blob = "\n".join(_facts()) + LEARNING_SIMULATION_OUTPUT_CONSTRAINT
        for phrase in TestClosedWorldVocabulary.BANNED:
            assert phrase not in blob

    def test_output_constraint_states_the_limit(self):
        assert "このモデルと配線のもとでは" in LEARNING_SIMULATION_OUTPUT_CONSTRAINT
        assert "言い換えないでください" in LEARNING_SIMULATION_OUTPUT_CONSTRAINT


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


def _sim_screen(run_id: str = RUN_ID) -> dict:
    return {
        "screen": "learning",
        "selection": {"course_id": COURSE_ID, "topic_id": TOPIC_ID, "simulation_run_id": run_id},
        "view": {"mode": "chat"},
        "visible_entities": [],
    }


def _patch_get_run(monkeypatch, run: dict | None) -> MagicMock:
    mock = MagicMock(return_value=run)
    monkeypatch.setattr(learning_mod.simulation_store, "get_run", mock)
    return mock


class TestRoute:
    def test_own_run_reaches_the_turn_and_adds_the_constraint(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        get_run = _patch_get_run(monkeypatch, _run())

        _chat(_make_body("この結果はどういう意味？", screen_context=_sim_screen()))

        assert get_run.call_args.args[1] == RUN_ID
        content = _last_user(captured)
        assert "理論の出力: M_J = 2.5" in content
        assert FACT_NOT_VERIFICATION in content
        assert _system(captured).endswith(LEARNING_SIMULATION_OUTPUT_CONSTRAINT)
        events = [call.args[0] for call in chat_env.metric_mock.call_args_list]
        assert events.count("structured_grounding_present") == 1

    def test_saved_message_is_the_raw_utterance(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        _patch_get_run(monkeypatch, _run())

        _chat(_make_body("この結果はどういう意味？", screen_context=_sim_screen()))

        saved = repr(chat_env.persist_mock.call_args)
        assert "理論の出力" not in saved

    def test_someone_elses_run_is_silently_ignored(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        _patch_get_run(monkeypatch, _run(owner_user_id=OTHER_USER))

        _chat(_make_body("この結果は？", screen_context=_sim_screen()))

        assert "理論の出力" not in _last_user(captured)
        assert LEARNING_SIMULATION_OUTPUT_CONSTRAINT not in _system(captured)

    def test_unowned_legacy_run_is_ignored(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        _patch_get_run(monkeypatch, _run(owner_user_id=None))

        _chat(_make_body("この結果は？", screen_context=_sim_screen()))

        assert "理論の出力" not in _last_user(captured)

    def test_run_outside_the_grounding_scope_is_ignored(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        _patch_get_run(monkeypatch, _run(document_id="77777777-7777-7777-7777-777777777777"))

        _chat(_make_body("この結果は？", screen_context=_sim_screen()))

        assert "理論の出力" not in _last_user(captured)

    def test_non_uuid_id_does_not_query(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        get_run = _patch_get_run(monkeypatch, _run())

        _chat(_make_body("この結果は？", screen_context=_sim_screen("run-1")))

        get_run.assert_not_called()

    def test_lookup_failure_degrades_silently(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        monkeypatch.setattr(
            learning_mod.simulation_store, "get_run", MagicMock(side_effect=RuntimeError("db"))
        )

        _chat(_make_body("この結果は？", screen_context=_sim_screen()))

        assert "理論の出力" not in _last_user(captured)

    def test_failed_run_does_not_add_the_constraint(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        _patch_get_run(
            monkeypatch, _run(status="failed", result={"facts": ["実行環境に到達できません。"]})
        )

        _chat(_make_body("動かなかった", screen_context=_sim_screen()))

        assert "比較まで終わっていない実行です" in _last_user(captured)
        assert LEARNING_SIMULATION_OUTPUT_CONSTRAINT not in _system(captured)

    def test_elicit_does_not_read_the_run(self, chat_env, monkeypatch):
        _set_generation(monkeypatch)
        get_run = _patch_get_run(monkeypatch, _run())

        _chat(_make_body("予想してみる", cycle_mode="elicit", screen_context=_sim_screen()))

        get_run.assert_not_called()

    def test_casual_gets_no_simulation_block(self, chat_env, monkeypatch):
        captured: dict = {}
        _set_generation(monkeypatch, captured)
        get_run = _patch_get_run(monkeypatch, _run())

        _chat(_make_body("ねえ", intent_mode="casual", screen_context=_sim_screen()))

        get_run.assert_not_called()
        assert LEARNING_SIMULATION_OUTPUT_CONSTRAINT not in _system(captured)
