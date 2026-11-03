from __future__ import annotations

import json

import pytest

from uxsim.llm import (CachingPersonaLLM, ReplayMiss, ReplayPersonaLLM, ReplayThenLivePersonaLLM,
                       ScriptedPersonaLLM, cache_key, load_cache_jsonl, parse_json_object)

MSGS = [{"role": "user", "content": "こんにちは"}]


def test_cache_records_and_replays(tmp_path):
    path = tmp_path / "cache" / "persona_llm.jsonl"
    live = ScriptedPersonaLLM([{"action_id": "learning.chat.ask"}])
    caching = CachingPersonaLLM(live, path)
    out = caching.complete_json("SYS", MSGS, "{}")
    assert out == {"action_id": "learning.chat.ask"}
    assert caching.calls == 1
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert rows[0]["key"] == cache_key("SYS", MSGS)
    replay = ReplayPersonaLLM(load_cache_jsonl(path))
    assert replay.complete_json("SYS", MSGS, "{}") == out
    assert replay.calls == 0  # replay は予算を消費しない


def test_replay_miss_raises():
    replay = ReplayPersonaLLM({})
    with pytest.raises(ReplayMiss):
        replay.complete_json("SYS", MSGS, "{}")


def test_key_depends_on_system_and_messages():
    assert cache_key("A", MSGS) != cache_key("B", MSGS)
    assert cache_key("A", MSGS) != cache_key("A", [{"role": "user", "content": "x"}])


def test_replay_then_live_switches_on_miss():
    replay = ReplayPersonaLLM({cache_key("S", MSGS): {"v": 1}})
    live = ScriptedPersonaLLM([{"v": 2}])
    llm = ReplayThenLivePersonaLLM(replay, live)
    assert llm.complete_json("S", MSGS, "") == {"v": 1}
    assert llm.complete_json("S", [{"role": "user", "content": "変わった"}], "") == {"v": 2}
    assert llm.diverged and llm.diverged_at_call == 2


def test_parse_json_object_tolerates_fences():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('前置き {"a": 2} 後書き') == {"a": 2}
