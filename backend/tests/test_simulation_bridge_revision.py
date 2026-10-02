"""SB層 配線の改訂提案（core/simulation_bridge/revision.py）— LLM はフェイク。"""

from __future__ import annotations

from core.simulation_bridge import comparator, revision, schema
from core.text_hygiene import UNTRUSTED_SOURCE_NOTICE


def _catalog():
    return {
        "amp": {
            "entry_id": "amp",
            "version_no": 2,
            "entry_type": schema.PART_TYPE_INSTRUMENT,
            "name": "線形増幅器",
            "body": {"transfer": "x * params['gain']", "params": {"gain": {"default": 2.0, "min": 0.0, "max": 10.0}}},
        },
        "noise": {
            "entry_id": "noise",
            "version_no": 1,
            "entry_type": schema.PART_TYPE_NOISE,
            "name": "白色ノイズ",
            "body": {"noise_kind": "gaussian", "params": {"sigma": {"default": 1.0, "min": 0.0, "max": 5.0}}},
        },
        "avg": {
            "entry_id": "avg",
            "version_no": 1,
            "entry_type": schema.PART_TYPE_PROCESSING,
            "name": "平均化",
            "body": {"transfer": "x", "params": {}, "removes": ["gaussian"]},
        },
    }


def _wiring():
    return {
        "parts": [
            {"slot": "amp", "entry_id": "amp", "version_no": 2, "params": {}},
            {"slot": "noise", "entry_id": "noise", "version_no": 1, "params": {}},
        ],
        "edges": [{"from_slot": "amp", "to_slot": "noise"}],
    }


def _comparison():
    return comparator.compare({"y": 1.0}, {"y": {"mean": 2.0, "std": 3.0}}, wiring_name="W")


class _Fake:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def test_empty_catalog_does_not_call_llm():
    fake = _Fake({"changes": []})
    p = revision.propose_revision(_wiring(), _comparison(), {}, structured_fn=fake)
    assert fake.calls == []
    assert p.facts == [schema.FACT_NO_PARTS_CATALOG]
    assert p.empty


def test_llm_failure_degrades():
    fake = _Fake(error=RuntimeError("boom"))
    p = revision.propose_revision(_wiring(), _comparison(), _catalog(), structured_fn=fake)
    assert len(fake.calls) == 1
    assert p.facts == [revision.FACT_REVISION_DEGRADED]
    assert p.empty
    assert p.parts == _wiring()["parts"]


def test_prompt_has_untrusted_block_and_catalog():
    fake = _Fake({"changes": []})
    revision.propose_revision(_wiring(), _comparison(), _catalog(), structured_fn=fake)
    content = fake.calls[0]["messages"][-1]["content"]
    assert UNTRUSTED_SOURCE_NOTICE in content
    assert '"entry_id": "avg"' in content
    assert "add" in content and "set_param" in content


def test_invented_entry_and_out_of_range_dropped_valid_add_applied():
    fake = _Fake(
        {
            "changes": [
                {"op": "add", "slot": "lockin", "entry_id": "lockin_amp", "reason": "ロックインで除く"},
                {"op": "set_param", "slot": "noise", "param": "sigma", "value": 99.0, "reason": "下げる"},
                {"op": "add", "slot": "", "entry_id": "avg", "reason": "平均化でばらつきを抑える"},
            ]
        }
    )
    p = revision.propose_revision(_wiring(), _comparison(), _catalog(), structured_fn=fake)
    assert [c.op for c in p.changes] == ["add"]
    added = p.changes[0]
    assert added.entry_id == "avg"
    assert added.slot == "signal_processing_1"
    assert len(p.dropped) == 2
    assert any("目録に無い部品" in d for d in p.dropped)
    assert any("範囲の外" in d for d in p.dropped)
    assert [part["slot"] for part in p.parts] == ["amp", "noise", "signal_processing_1"]
    assert {"from_slot": "noise", "to_slot": "signal_processing_1"} in p.edges
    assert p.facts == []
    # 元の配線は変更しない
    assert len(_wiring()["parts"]) == 2


def test_set_param_and_remove_splice():
    wiring = {
        "parts": [
            {"slot": "amp", "entry_id": "amp", "params": {}},
            {"slot": "noise", "entry_id": "noise", "params": {}},
            {"slot": "avg", "entry_id": "avg", "params": {}},
        ],
        "edges": [{"from_slot": "amp", "to_slot": "noise"}, {"from_slot": "noise", "to_slot": "avg"}],
    }
    fake = _Fake(
        {
            "changes": [
                {"op": "set_param", "slot": "amp", "param": "gain", "value": 4.0, "reason": "上げる"},
                {"op": "remove", "slot": "noise", "reason": "外す"},
                {"op": "set_param", "slot": "amp", "param": "bias", "value": 1.0},
                {"op": "rewrite", "slot": "amp"},
            ]
        }
    )
    p = revision.propose_revision(wiring, _comparison(), _catalog(), structured_fn=fake)
    assert [c.op for c in p.changes] == ["set_param", "remove"]
    assert p.parts[0]["params"] == {"gain": 4.0}
    assert [x["slot"] for x in p.parts] == ["amp", "avg"]
    assert p.edges == [{"from_slot": "amp", "to_slot": "avg"}]
    # 上限 3 件を超えた分は外され、その旨が残る
    assert any("3 件まで" in d for d in p.dropped)
    assert any("宣言の無いパラメータ" in d for d in p.dropped)


def test_change_that_breaks_wiring_is_dropped():
    wiring = {"parts": [{"slot": "amp", "entry_id": "amp", "params": {}}], "edges": []}
    fake = _Fake({"changes": [{"op": "remove", "slot": "amp", "reason": "外す"}]})
    p = revision.propose_revision(wiring, _comparison(), _catalog(), structured_fn=fake)
    assert p.empty
    assert p.facts == [schema.FACT_NO_PROPOSAL]
    assert any("規約を満たさなく" in d for d in p.dropped)


def test_missing_slot_dropped_and_no_proposal_fact():
    fake = _Fake({"changes": [{"op": "remove", "slot": "ghost"}]})
    p = revision.propose_revision(_wiring(), _comparison(), _catalog(), structured_fn=fake)
    assert p.empty and p.facts == [schema.FACT_NO_PROPOSAL]
    assert any("配線に無い slot" in d for d in p.dropped)
