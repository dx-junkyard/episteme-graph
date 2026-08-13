"""理解サイクル（Understanding Cycle, UCサイクル）Phase 2 のテスト。

対象仕様: docs/features/understanding_cycle_design.md §6/§8（不変条項 §2）。

- schemas.py: `cycle_mode` フィールドの追加
- learning.py: `_cycle_mode` の定義順・422 precheck の位置・プロンプト分岐（cycle が
  discuss より先に評価される）・`_chat_feature` 分岐への2語彙の配線
- U層/M層: `learning:cycle_elicit` / `learning:cycle_diff` の KNOWN_FEATURES 登録 +
  scene_for_feature 解決
- Elicit/Diff プロンプトの契約フレーズ（テスト契約フレーズ化。§8）
- 帰り道の景色（core.cycle.map_diff）: 個人知識ネットワークの過去時点との構造差分を
  肯定形の事実文にする純粋関数（personal_graph パッケージ自体は変更しない）
- core.cycle.derive.build_revisit_facts の第3引数合流（構造差分優先・3件上限・
  2引数呼び出しの後方互換）
- discuss.js: AI Elicit/Diff ボタンの存在配線
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

LEARNING = BACKEND / "api" / "routes" / "learning.py"
SCHEMAS = BACKEND / "api" / "schemas.py"
DISCUSS_JS = ROOT / "frontend" / "public" / "js" / "discuss.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ===========================================================================
# 1. schemas.py — cycle_mode フィールド
# ===========================================================================


class TestSchemaField:
    def test_cycle_mode_field_exists(self):
        source = _read(SCHEMAS)
        body = source.split("class LearningChatRequest")[1].split("\nclass ")[0]
        assert "cycle_mode: str | None = None" in body
        assert "elicit" in body
        assert "diff" in body


# ===========================================================================
# 2. learning.py — 定義順・422 precheck・プロンプト分岐・feature 分岐
# ===========================================================================


class TestCycleModeWiring:
    def test_cycle_mode_defined_after_is_discuss(self):
        source = _read(LEARNING)
        is_discuss_idx = source.index('_is_discuss = (body.intent_mode or "").strip() == "discuss"')
        cycle_mode_idx = source.index('_cycle_mode = (body.cycle_mode or "").strip()')
        assert is_discuss_idx < cycle_mode_idx

    def test_prompt_branch_evaluates_cycle_before_discuss(self):
        """プロンプト分岐（# 5. 回答の生成）で cycle_mode が discuss より先に評価される。"""
        source = _read(LEARNING)
        block = source.split("# 5. 回答の生成（ルート統合）")[1][:700]
        elicit_pos = block.index('if _cycle_mode == "elicit":')
        diff_pos = block.index('elif _cycle_mode == "diff":')
        discuss_pos = block.index("elif _is_discuss:")
        casual_pos = block.index("elif _is_casual:")
        assert elicit_pos < diff_pos < discuss_pos < casual_pos
        assert "_get_cycle_elicit_system_prompt(domain, response_persona)" in block
        assert "_get_cycle_diff_system_prompt(domain, response_persona)" in block

    def test_invalid_cycle_mode_raises_422_before_truncate(self):
        """cycle_mode の値検証（discuss_scope precheck と同型）は、learning_chat 本体の
        truncate 呼び出し（機能3の書き直し）より前にある。"""
        source = _read(LEARNING)
        precheck_idx = source.index('_cycle_mode_precheck = (body.cycle_mode or "").strip()')
        # learning_chat 内の実呼び出し（他関数のインポート・別呼び出しと区別するため
        # 代入先の変数名まで含めて特定する）。
        truncate_idx = source.index("_trunc = truncate_chat_and_supersede(")
        assert precheck_idx < truncate_idx
        block = source[precheck_idx:precheck_idx + 500]
        assert 'if _cycle_mode_precheck and _cycle_mode_precheck not in ("elicit", "diff"):' in block
        assert "status_code=422" in block

    def test_chat_feature_branch_has_cycle_features(self):
        source = _read(LEARNING)
        assert '"elicit": "learning:cycle_elicit"' in source
        assert '"diff": "learning:cycle_diff"' in source
        block = source.split('messages.append({"role": "user", "content": body.message})')[1][:500]
        assert "if _cycle_chat_feature:" in block
        assert "_chat_feature = _cycle_chat_feature" in block
        # 既存の discuss/casual 分岐は壊れていない（test_discuss_mode.py が別途検査）。
        assert "elif _is_discuss:" in block
        assert '_chat_feature = "learning:chat_discuss"' in block


# ===========================================================================
# 3. Elicit/Diff プロンプトの契約フレーズ
# ===========================================================================


class TestCyclePrompts:
    def _elicit_body(self) -> str:
        source = _read(LEARNING)
        return source.split("def _get_cycle_elicit_system_prompt(")[1].split("\ndef ")[0]

    def _diff_body(self) -> str:
        source = _read(LEARNING)
        return source.split("def _get_cycle_diff_system_prompt(")[1].split("\ndef ")[0]

    def test_elicit_prompt_contract_phrases(self):
        body = self._elicit_body()
        for phrase in ("解を提示しないでください", "問いを一つだけ", "学生の直前の発話"):
            assert phrase in body, f"Elicit プロンプトの契約フレーズが欠落: {phrase}"

    def test_diff_prompt_contract_phrases(self):
        body = self._diff_body()
        for phrase in (
            "食い違いの可能性", "断定しないでください", "採点や点数評価をしないでください", "候補",
        ):
            assert phrase in body, f"Diff プロンプトの契約フレーズが欠落: {phrase}"

    def test_prompts_do_not_assert_correctness(self):
        """UC2: 「正解は」「不正解は」のように AI 自身が断定的に採点する指示文が
        無いこと（「正解/不正解の判定をしないでください」という禁止指示自体は許容）。"""
        for body in (self._elicit_body(), self._diff_body()):
            assert "正解は" not in body
            assert "不正解は" not in body


# ===========================================================================
# 4. U層/M層 — feature 登録・scene 解決
# ===========================================================================


class TestUsageAndPolicyRegistration:
    def test_known_features_include_cycle_features(self):
        import core.llm_usage.schema as schema

        assert "learning:cycle_elicit" in schema.KNOWN_FEATURES
        assert "learning:cycle_diff" in schema.KNOWN_FEATURES

    def test_scene_for_feature_resolves_cycle_features(self):
        import core.llm_policy as llm_policy

        assert llm_policy.scene_for_feature("learning:cycle_elicit") == "learning_chat"
        assert llm_policy.scene_for_feature("learning:cycle_diff") == "learning_chat"


# ===========================================================================
# 5. 帰り道の景色（core.cycle.map_diff）
# ===========================================================================


class TestMapDiffFacts:
    def _node(self, node_id, label, created_at="2026-08-13T00:00:00+00:00"):
        from core.personal_graph.schema import PersonalAnchor, PersonalNode

        return PersonalNode(
            id=node_id,
            node_kind="tension",
            label=label,
            anchor=PersonalAnchor(anchor_type="topic", anchor_id="t1"),
            topic_id="t1",
            created_at=created_at,
            facts=[],
            source={"kind": "tension", "status": "articulated"},
        )

    def _edge(self, from_node_id, ref_id="c1"):
        from core.personal_graph.schema import PersonalEdge

        return PersonalEdge(
            edge_kind="bridge",
            from_node_id=from_node_id,
            to_ref={"ref_type": "component", "ref_id": ref_id},
            fact="この引っかかりを自分でつないだ",
        )

    def test_new_node_produces_positive_fact(self):
        from core.cycle.map_diff import build_map_diff_facts
        from core.personal_graph.schema import PersonalNetwork

        before = PersonalNetwork(nodes=[self._node("n1", "境界条件の扱い")], edges=[])
        after = PersonalNetwork(
            nodes=[self._node("n1", "境界条件の扱い"), self._node("n2", "新しい引っかかり")],
            edges=[],
        )
        facts = build_map_diff_facts(before, after)
        assert facts
        assert any("新しい引っかかり" in f and "加わっています" in f for f in facts)

    def test_new_bridge_produces_positive_fact(self):
        from core.cycle.map_diff import build_map_diff_facts
        from core.personal_graph.schema import PersonalNetwork

        node = self._node("n1", "境界条件の扱い")
        before = PersonalNetwork(nodes=[node], edges=[])
        after = PersonalNetwork(nodes=[node], edges=[self._edge("n1")])
        facts = build_map_diff_facts(before, after)
        assert facts
        assert any("橋が増えています" in f for f in facts)

    def test_no_diff_produces_no_facts(self):
        from core.cycle.map_diff import build_map_diff_facts
        from core.personal_graph.schema import PersonalNetwork

        node = self._node("n1", "境界条件の扱い")
        edge = self._edge("n1")
        before = PersonalNetwork(nodes=[node], edges=[edge])
        after = PersonalNetwork(nodes=[node], edges=[edge])
        assert build_map_diff_facts(before, after) == []

    def test_max_two_facts(self):
        from core.cycle.map_diff import build_map_diff_facts
        from core.personal_graph.schema import PersonalNetwork

        before = PersonalNetwork(nodes=[], edges=[])
        after = PersonalNetwork(
            nodes=[
                self._node("n1", "引っかかり1"),
                self._node("n2", "引っかかり2"),
                self._node("n3", "引っかかり3"),
            ],
            edges=[self._edge("n1"), self._edge("n2", ref_id="c2")],
        )
        facts = build_map_diff_facts(before, after)
        assert len(facts) <= 2

    def test_facts_have_no_negation_or_numbers(self):
        """否定形の断言（「まだ」「ありませんでした」）と数値・件数を作らない。"""
        import re

        from core.cycle.map_diff import build_map_diff_facts
        from core.personal_graph.schema import PersonalNetwork

        before = PersonalNetwork(nodes=[], edges=[])
        node = self._node("n1", "引っかかり1")
        after = PersonalNetwork(nodes=[node], edges=[self._edge("n1")])
        facts = build_map_diff_facts(before, after)
        assert facts
        for f in facts:
            assert "まだ" not in f
            assert "ありませんでした" not in f
            assert not re.search(r"\d+\s*件", f)
            assert not re.search(r"\d+\s*%", f)


# ===========================================================================
# 6. core.cycle.derive.build_revisit_facts の第3引数合流
# ===========================================================================


class TestRevisitFactsMapDiffMerge:
    _CARRYOVER = {"id": "c1", "text": "問い", "created_at": "2026-08-12T00:00:00+00:00"}

    def test_backward_compatible_two_arg_call(self):
        from core.cycle.derive import build_revisit_facts

        assert build_revisit_facts(self._CARRYOVER, []) == []
        assert build_revisit_facts(None, []) == []

    def test_map_diff_facts_come_first(self):
        from core.cycle.derive import build_revisit_facts

        rows = [
            {
                "id": "t1",
                "kind": "tension",
                "status": "articulated",
                "payload": {"text": "既存の引っかかり", "learner_text": "既存の引っかかり"},
                "created_at": "2026-08-13T00:00:00+00:00",
            },
        ]
        map_diff_facts = ["前回の問いのあと、あなたの地図に『新しい引っかかり』が加わっています。"]
        facts = build_revisit_facts(self._CARRYOVER, rows, map_diff_facts=map_diff_facts)
        assert facts[0] == map_diff_facts[0]

    def test_total_capped_at_three(self):
        from core.cycle.derive import build_revisit_facts

        rows = [
            {
                "id": f"t{i}",
                "kind": "tension",
                "status": "articulated",
                "payload": {"text": f"引っかかり{i}", "learner_text": f"引っかかり{i}"},
                "created_at": "2026-08-13T00:00:00+00:00",
            }
            for i in range(5)
        ]
        map_diff_facts = ["構造差分1", "構造差分2"]
        facts = build_revisit_facts(self._CARRYOVER, rows, map_diff_facts=map_diff_facts)
        assert len(facts) == 3
        assert facts[0] == "構造差分1"
        assert facts[1] == "構造差分2"

    def test_no_carryover_returns_empty_even_with_map_diff_facts(self):
        from core.cycle.derive import build_revisit_facts

        assert build_revisit_facts(None, [], map_diff_facts=["何か"]) == []


# ===========================================================================
# 7. discuss.js — AI Elicit/Diff ボタン配線
# ===========================================================================


class TestDiscussJsCycleButtons:
    def test_elicit_button_exists(self):
        js = _read(DISCUSS_JS)
        assert "AIから問いをもらう" in js
        assert 'cycle_mode: "elicit"' in js

    def test_diff_button_exists(self):
        js = _read(DISCUSS_JS)
        assert "AIに違いの観点を出してもらう" in js
        assert 'cycle_mode: "diff"' in js

    def test_send_prompt_guarded_by_typeof_check(self):
        js = _read(DISCUSS_JS)
        assert 'typeof window.sendPrompt !== "function"' in js
