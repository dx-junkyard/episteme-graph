"""解析 run の生成言語を A層 agent の prompt に足す経路（IK-0571）。

不変条項:
- 未指定なら prompt は従来と 1 バイトも変わらない（system は各 prompt の ``_SYSTEM_CONTENT`` そのもの）。
- 指定ありなら system の末尾に ``## Output Language`` 節が付き、対象フィールドが列挙される。
- 決定論の後段が英語のキーワードで読むフィールド（component の label / summary / reason、
  式の summary / reason 等）は対象にしない。
"""
from __future__ import annotations

import inspect

import pytest

from episteme_graph.agents import generation_language as gl
from episteme_graph.agents.component_assembly import prompt as ca_prompt
from episteme_graph.agents.narrative_annotator import prompt as na_prompt
from episteme_graph.agents.paper_skeleton import prompt as ps_prompt
from episteme_graph.agents.paper_skeleton.schema import SkeletonLLMInput
from episteme_graph.agents.thesis_reconstruction import prompt as th_prompt


class _Blank:
    """どの属性も空リストを返す入力（prompt の組み立てだけを見るためのもの）。"""

    def __getattr__(self, name):
        return []


class _Issue:
    severity = "error"
    rule_id = "some_rule"
    message = "some message"
    field = "some.field"


def _skeleton_input():
    return SkeletonLLMInput(
        document_id="doc-1", cartridge_id=None, title="A title",
        abstract_blocks=[], section_headers=[], representative_blocks=[], conclusion_blocks=[],
    )


CASES = [
    pytest.param(ps_prompt, ps_prompt.PaperSkeletonPromptFactory, _skeleton_input, id="paper_skeleton"),
    pytest.param(th_prompt, th_prompt.ThesisReconstructionPromptFactory, _Blank, id="thesis_reconstruction"),
    pytest.param(na_prompt, na_prompt.NarrativePromptFactory, _Blank, id="narrative_annotator"),
    pytest.param(ca_prompt, ca_prompt.ComponentAssemblyPromptFactory, _Blank, id="component_assembly"),
]


def _all(factory, make_input):
    return (
        factory.build_messages(make_input()),
        factory.build_repair_messages(make_input(), {"x": 1}, [_Issue()]),
    )


@pytest.mark.parametrize("module,factory_cls,make_input", CASES)
def test_unset_language_is_byte_identical(module, factory_cls, make_input):
    for factory in (factory_cls(), factory_cls(language=None), factory_cls(language="fr")):
        for messages in _all(factory, make_input):
            assert messages[0] == {"role": "system", "content": module._SYSTEM_CONTENT}
            assert all(gl.SECTION_HEADING not in m["content"] for m in messages)
    # 未指定の出力は内部の組み立て（_base_*）と完全に同じ。
    plain = factory_cls()
    assert plain.build_messages(make_input()) == plain._base_messages(make_input())


@pytest.mark.parametrize("language,name", [("ja", "Japanese"), ("en", "English"), ("JA", "Japanese")])
@pytest.mark.parametrize("module,factory_cls,make_input", CASES)
def test_set_language_appends_section_with_fields(module, factory_cls, make_input, language, name):
    factory = factory_cls(language=language)
    for messages in _all(factory, make_input):
        system = messages[0]["content"]
        assert system.startswith(module._SYSTEM_CONTENT)
        assert gl.SECTION_HEADING in system and name in system
        for field in module.GENERATED_PROSE_FIELDS:
            assert field in system
        # user メッセージには足さない（論文素材の区画を変えない）。
        assert gl.SECTION_HEADING not in messages[1]["content"]


def test_section_keeps_verbatim_and_symbols_in_original():
    section = gl.generation_language_section("ja", ["a.text"])
    assert "verbatim quotes" in section and "LaTeX" in section and "IDs" in section
    assert "a.text" in section


def test_apply_returns_same_object_when_unset_and_does_not_mutate_when_set():
    messages = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert gl.apply_generation_language(messages, None, ["f"]) is messages
    assert gl.apply_generation_language(messages, "de", ["f"]) is messages
    out = gl.apply_generation_language(messages, "ja", ["f"])
    assert out is not messages and messages[0]["content"] == "S"
    assert out[0]["content"].startswith("S\n\n" + gl.SECTION_HEADING)


def test_normalize_vocabulary():
    assert gl.normalize_generation_language(" EN ") == "en"
    assert gl.normalize_generation_language("ja") == "ja"
    for bad in (None, "", "fr", 1):
        assert gl.normalize_generation_language(bad) is None


def test_structure_driving_fields_are_excluded():
    """英語のキーワード照合で構造を導く後段が読むフィールドは言語指定の対象外。"""
    ca = set(ca_prompt.GENERATED_PROSE_FIELDS)
    for field in ("components[].label", "components[].summary", "components[].reason",
                  "components[].preconditions[].text", "components[].cautions[].text"):
        assert field not in ca
    # claim / 逐語引用・headline の主張文は論文の言語のまま。
    ps = set(ps_prompt.GENERATED_PROSE_FIELDS)
    assert "headline_claim.text" not in ps and "supporting_subclaims[].text" not in ps


@pytest.mark.parametrize("agent_path,cls_name", [
    ("episteme_graph.agents.paper_skeleton.agent", "PaperSkeletonAgent"),
    ("episteme_graph.agents.thesis_reconstruction.agent", "ThesisReconstructionAgent"),
    ("episteme_graph.agents.narrative_annotator.agent", "NarrativeAnnotator"),
    ("episteme_graph.agents.component_assembly.agent", "ComponentAssemblyAgent"),
])
def test_agent_run_accepts_language_and_defaults_to_none(agent_path, cls_name):
    import importlib

    cls = getattr(importlib.import_module(agent_path), cls_name)
    param = inspect.signature(cls.run).parameters["language"]
    assert param.default is None


def test_agent_run_sets_and_resets_prompt_language():
    """run(language=) が prompt factory に渡り、次の run の未指定で戻る。"""
    from episteme_graph.agents.thesis_reconstruction.agent import ThesisReconstructionAgent

    agent = ThesisReconstructionAgent()
    seen: list = []

    class _Stop(Exception):
        pass

    def _capture(*args, **kwargs):
        seen.append(agent._prompt_factory.language)
        raise _Stop

    agent._input_builder.build = _capture  # type: ignore[assignment]
    for language in ("ja", None, "xx"):
        with pytest.raises(_Stop):
            agent.run(skeleton=None, qualified_claims=None, language=language)
    assert seen == ["ja", None, None]

