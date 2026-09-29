"""IK-0458: レクチャー（スライド）の DTO が受講画面と同じ evidence_items を運ぶ。

- ``routes.lecture.get_lecture_sequence`` のトピック教材経路のセグメントが
  ``build_topic_evidence_items`` の投影を持つ（受講画面 ``get_topic_material`` と同一）。
- 引けない ``![[component|claim|source:id]]`` はスライドの表示本文から外れる
  （学習者に内部 ID を見せない）。スライド数・読み上げ原稿は変わらない。
- ``app.js`` のレクチャー描画が evidence_items を渡し、未解決カード（kind:id）を描かない。
"""

from __future__ import annotations

import os
import re
import sys
from unittest.mock import patch

_API_DIR = os.path.join(os.path.dirname(__file__), "..", "api")
if _API_DIR not in sys.path:
    sys.path.insert(0, _API_DIR)

_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
APP_JS = os.path.join(_ROOT, "frontend", "public", "js", "app.js")
INDEX_HTML = os.path.join(_ROOT, "frontend", "public", "index.html")


def _topic():
    return {
        "id": "topic-1",
        "title": "状態方程式",
        "student_material": {
            "source_text": "本文 ![[claim:claim_1]] と ![[component:comp_1]] 、未知 ![[claim:claim_missing]] 。",
        },
        "spoken_script": "状態方程式について話します。",
        "evidence_links": [
            {"kind": "claim", "target_id": "claim_1", "label": "主張A", "summary": "主張の要約"},
            {"kind": "component", "target_id": "comp_1", "label": "部品A", "summary": "部品の要約"},
        ],
    }


def _course(topic):
    return {"topics": [topic], "sources": [{"material_id": "mat-1"}]}


@patch("api.routes.lecture._get_topic_slide_audio_map", return_value={})
@patch("api.routes.lecture._attach_figure_explanations")
@patch("api.routes.lecture._load_course_figures_by_id", return_value={})
@patch("api.routes.lecture.get_course_data")
def test_topic_material_segment_carries_evidence_items(mock_course, _figs, _expl, _audio):
    from api.routes.lecture import get_lecture_sequence
    from core.course_content_builder import build_topic_evidence_items
    from core.lecture import build_topic_slides

    topic = _topic()
    mock_course.return_value = _course(topic)

    resp = get_lecture_sequence("c1", "topic-1", {"id": "u1"})

    seg = resp.segments[0]
    assert seg.chunk_id == "topic:topic-1"
    # 受講画面と同じ投影（再実装ではなく同じ関数の出力）
    assert seg.evidence_items == build_topic_evidence_items(topic)
    refs = {(i["kind"], i["id"]) for i in seg.evidence_items}
    assert ("claim", "claim_1") in refs and ("component", "comp_1") in refs
    # 引ける埋め込みは残り、引けない埋め込みは表示本文から外れる
    joined = "\n".join(s.display_text for s in seg.slides)
    assert "![[claim:claim_1]]" in joined
    assert "![[component:comp_1]]" in joined
    assert "claim_missing" not in joined
    assert "claim_missing" not in seg.text
    # スライド境界・読み上げは build_topic_slides のまま
    slide_dicts, _d, _s, _f = build_topic_slides(topic, figures_by_id={})
    assert len(seg.slides) == len(slide_dicts)
    assert [s.spoken_text for s in seg.slides] == [sd["spoken_text"] for sd in slide_dicts]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _body(src, start_marker, end_marker):
    i = src.index(start_marker)
    j = src.index(end_marker, i)
    return src[i:j]


def test_lecture_stage_passes_evidence_items_and_drops_unresolved():
    src = _read(APP_JS)
    block = _body(src, "function renderLectureStage() {", "function fitLectureSlideContent")
    assert "pseudoChunk.evidence_items = slide.evidence_items || [];" in block
    assert "pseudoChunk.drop_unresolved_embeds = true;" in block
    assert "renderMaterialChunk(pseudoChunk)" in block
    # レクチャー描画に生の埋め込み記法・id を直接印字するフォールバックを書かない
    assert "![[" not in block
    assert "renderMaterialMissingEmbed" not in block


def test_lecture_deck_carries_segment_evidence_items():
    src = _read(APP_JS)
    block = _body(src, "function buildLectureDeck", "function showLectureSlideNotice")
    assert "evidence_items: seg.evidence_items || []" in block


def test_render_material_chunk_removes_missing_cards_in_drop_mode():
    src = _read(APP_JS)
    block = _body(src, "function renderMaterialChunk(chunk) {", "\n  function renderMaterialMissingEmbed")
    assert "var dropUnresolvedEmbeds = !!chunk.drop_unresolved_embeds;" in block
    assert re.search(r"if \(dropUnresolvedEmbeds\) \{\s*embedBlocks\.forEach", block)
    assert "html = html.split(missingHtml).join(\"\");" in block


def test_app_js_cache_buster_bumped():
    html = _read(INDEX_HTML)
    assert "/js/app.js?v=ik0458-" in html


def test_drop_mode_renders_chips_and_omits_raw_ids(tmp_path):
    """Node で renderMaterialChunk を評価: drop_unresolved_embeds のとき、引ける claim/component
    は ⚓ チップ・source はカードで出て、引けない埋め込みは id を含む未解決カードにならない。
    drop 指定なし（受講画面）は従来どおり未解決カードのまま。"""
    import json
    import shutil
    import subprocess

    import pytest

    if not shutil.which("node"):
        pytest.skip("node not available")
    from tests.test_learning_material_embed_resolution import _EXTRACT  # noqa: WPS433

    script = r"""
const fs=require("fs");
const app=fs.readFileSync(process.argv[2],"utf8");
var window={katex:null};
function escHtml(s){return String(s==null?"":s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
var materialEvidenceChipItems = {};
eval(loadElementVocab(process.argv[2]));
eval(extractFrom(app,"materialEvidenceKindLabel"));
eval(extractVar(app,"MATERIAL_ELEMENT_CONTEXT_TYPES"));
eval(extractMany(app,["normalizeMaterialEvidenceId","normalizeMaterialLineBreaks","normalizeKatexFormula",
 "renderMaterialKatex","renderMaterialEquationBody","renderMaterialMissingEmbed",
 "shortMaterialEvidenceSummary","renderMaterialFigureCard","registerMaterialEvidenceChipEntry",
 "renderMaterialEvidenceChip","renderMaterialElementContextButton","mdBlocksToHtml","renderMaterialChunk"]));
function mk(drop){ return {
  text:"本文 ![[claim:claim_1]] と ![[component:comp_1]] 出典 ![[source:ev_1]] 未知 ![[claim:claim_missing]]",
  formulas:[], figures:[],
  evidence_items:[
    {kind:"claim",id:"claim_1",title:"主張A",summary:"s"},
    {kind:"component",id:"comp_1",title:"部品A",summary:"s"},
    {kind:"source",id:"ev_1",title:"原文引用",summary:"引用"}
  ],
  drop_unresolved_embeds: drop
};}
const lecture=renderMaterialChunk(mk(true));
const reading=renderMaterialChunk(mk(false));
process.stdout.write(JSON.stringify({
  chips:(lecture.match(/ls-material-evidence-chip"/g)||[]).length,
  source: lecture.indexOf("原文引用")>=0,
  lectureMissing: /ls-material-missing/.test(lecture) || lecture.indexOf("claim_missing")>=0,
  lectureRaw: lecture.indexOf("![[")>=0,
  readingMissing: /ls-material-missing/.test(reading)
}));
"""
    p = tmp_path / "h.js"
    p.write_text(_EXTRACT + script, encoding="utf-8")
    proc = subprocess.run(["node", str(p), APP_JS], capture_output=True, text=True, timeout=40)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["chips"] == 2, out
    assert out["source"], out
    assert not out["lectureMissing"], out
    assert not out["lectureRaw"], out
    assert out["readingMissing"], out
