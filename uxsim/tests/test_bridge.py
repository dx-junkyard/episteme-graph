"""課題ナレッジ候補エントリが TEMPLATE.md の必須キーと見出しを持つこと。"""
from __future__ import annotations

import re

import yaml

from uxsim.config import REPO_ROOT
from uxsim.report.issue_knowledge_bridge import candidate_entry, write_candidates
from uxsim.report.map_draft import build_map_draft
from uxsim.schema import Evidence, Finding, RunMeta

TEMPLATE = (REPO_ROOT / "docs" / "issue_knowledge" / "TEMPLATE.md").read_text(encoding="utf-8")


def _template_keys() -> tuple[set[str], set[str], list[str]]:
    block = TEMPLATE.split("```markdown", 1)[1].split("```", 1)[0]
    fm = block.split("---", 2)[1]
    top = {m.group(1) for m in re.finditer(r"^([a-z_]+):", fm, re.MULTILINE)}
    cls = {m.group(1) for m in re.finditer(r"^  ([a-z_]+):", fm.split("classification:", 1)[1].split("generalization:")[0],
                                            re.MULTILINE)}
    headings = re.findall(r"^## .+$", block.split("---", 2)[2], re.MULTILINE)
    return top, cls, headings


def _finding() -> Finding:
    return Finding(finding_id="f-r1-0001", fingerprint="ab" * 32, oracle="A", severity_label="blocked",
                   persona_id="st-1", scenario_id="s-x", screen="learning", affordance="composer.send",
                   hypothesis="学習チャットが 500 を返す", evidence=Evidence(transcript_steps=[3]),
                   suspected_layer=["rag_chat", "frontend_learning_ui"], run_id="r1", campaign_id="c-x")


def test_candidate_has_template_keys(tmp_path):
    text = candidate_entry(_finding(), RunMeta(run_id="r1", campaign_id="c-x", domain="a", snapshot="s"), tmp_path,
                           today="2026-09-27")
    fm = yaml.safe_load(text.split("---", 2)[1])
    top, cls, headings = _template_keys()
    assert top <= set(fm), top - set(fm)
    assert cls <= set(fm["classification"]), cls - set(fm["classification"])
    for h in headings:
        assert h in text
    assert fm["status"] == "open"
    assert fm["classification"]["review"] == "candidate"
    assert fm["classification"]["cause_status"] == "hypothesis"
    assert fm["classification"]["basis"].startswith("仮説:")
    assert fm["discovery"]["perspective"] == ["reproduction", "symptom_report"]
    assert fm["discovery"]["note"].startswith("ペルソナ通し受講で観測")
    assert fm["feature_context"]["layers"] == ["rag_chat", "frontend_learning_ui"]
    assert all(v == ["unknown"] for v in fm["classification"]["axes"].values())
    assert all(isinstance(s, str) and s.startswith("docs/") for s in fm["sources"])


def test_write_candidates_goes_to_run_dir_only(tmp_path):
    (tmp_path / "findings.jsonl").write_text(_finding().model_dump_json() + "\n")
    paths = write_candidates(tmp_path)
    assert len(paths) == 1 and paths[0].parent == tmp_path / "ik_candidates"
    assert re.match(r"^IK-XXXX-[a-z0-9]+(?:-[a-z0-9]+)*\.md$", paths[0].name)


def test_map_draft_has_six_viewpoints_and_no_scores(tmp_path):
    (tmp_path / "findings.jsonl").write_text(_finding().model_dump_json() + "\n")
    text = build_map_draft(tmp_path)
    assert "この波が前提とする利用状況" in text
    for v in ("目的への寄与", "利用状況での必要性", "放置時に起きること", "負担", "前提関係", "まだ分かっていないこと"):
        assert v in text
    assert "点" not in text.replace("観点", "")
