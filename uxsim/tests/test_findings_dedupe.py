from __future__ import annotations

from uxsim.oracles.findings import FindingFactory, dedupe, fingerprint, layers_for_action, normalize_hypothesis
from uxsim.schema import TranscriptStep


def test_fingerprint_normalizes_numbers_ids_and_quotes():
    a = fingerprint("A", "learning", "composer.send",
                    "GET /api/x/123e4567-e89b-12d3-a456-426614174000 が 500 を返す「一」")
    b = fingerprint("A", "learning", "composer.send",
                    "GET /api/x/00000000-0000-0000-0000-000000000000 が 503 を返す「二」")
    assert a == b
    assert a != fingerprint("B", "learning", "composer.send", "GET /api/x/1 が 500 を返す")
    assert normalize_hypothesis("  A  12 ") == "a #"


def test_dedupe_merges_steps_and_personas():
    f = FindingFactory()
    s1 = TranscriptStep(seq=1, persona_id="st-1", action_id="learning.chat.ask", screen="learning")
    s2 = TranscriptStep(seq=7, persona_id="st-2", action_id="learning.chat.ask", screen="learning")
    x = f.make(oracle="A", severity="blocked", step=s1, hypothesis="同じ症状")
    y = f.make(oracle="A", severity="blocked", step=s2, hypothesis="同じ症状")
    z = f.make(oracle="A", severity="blocked", step=s2, hypothesis="別の症状")
    out = dedupe([x, y, z])
    assert len(out) == 2
    merged = next(o for o in out if o.hypothesis == "同じ症状")
    assert merged.evidence.transcript_steps == [1, 7]
    assert merged.persona_id == "st-1,st-2"
    assert x.finding_id != y.finding_id


def test_layers_use_issue_knowledge_vocabulary():
    import re
    from uxsim.config import REPO_ROOT

    vocab = set(re.findall(r"^\| `([a-z_]+)`", (REPO_ROOT / "docs/issue_knowledge/layers.md").read_text(),
                           re.MULTILINE))
    from uxsim.oracles.findings import _LAYER_BY_PREFIX

    for layers in _LAYER_BY_PREFIX.values():
        assert set(layers) <= vocab
    assert layers_for_action("learning.chat.ask") == ["rag_chat", "frontend_learning_ui"]
    assert layers_for_action("admin.copilot.chat", "admin") == ["admin_copilot", "frontend_admin_ui"]


def test_finding_has_no_score_keys():
    from uxsim.schema import Finding

    assert not {"score", "confidence", "rank", "priority"} & set(Finding.model_fields)
