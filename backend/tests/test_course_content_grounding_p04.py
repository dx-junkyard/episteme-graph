"""P0-4: コース生成の出典が「位置代入」ではなく構造から決まることの回帰テスト。

背景（docs/architecture/knowledge_structure_review_2026-09-12 の C-4 / C-10 / S-11）:

- 旧実装は ``_fallback_chunk_for_topic`` = ``chunks[topic_index]`` で「出典」を決めて
  いたため、対応付けに失敗したトピックにも無関係な段落が ``source_excerpt`` として
  並んでいた（原則8「出所の正直さ」違反）。
- 旧実装は位置代入チャンクの ``formulas`` を丸ごと ``content_blocks`` に足していた
  ため、全チャンクが同じ式集合を持つ論文では「全トピック × 全式」が凍結スナップ
  ショットに複製されていた。

ここで固定するのは次の4点:
1. 位置代入をしない（無接続トピックは出典が空 + 事実文）。
2. 接続トピックの出典は evidence の block_id ∩ chunk.block_ids で決まり、
   チャンクの並び順を入れ替えても結果が変わらない。
3. content_blocks の式は linked_equation_ids ∪ 本文参照に限られる。
4. ``_fallback_chunk_for_topic`` という位置代入の関数自体が存在しない。
"""
from __future__ import annotations

import core.course_content_builder as ccb


# ---------------------------------------------------------------------------
# 共通フィクスチャ
# ---------------------------------------------------------------------------

DOC = "doc-1"


def _chunk(chunk_id: str, index: int, block_ids: list[str], text: str, formulas=None) -> dict:
    return {
        "id": chunk_id,
        "material_id": "m1",
        "chunk_index": index,
        "text": text,
        "formulas": formulas or [],
        "chapter": None,
        "section": None,
        "document_id": DOC,
        "block_ids": list(block_ids),
    }


def _bundle(*, components=None, mapping_topics=None, equations=None, claims=None, evidence=None) -> dict:
    return {
        "mapping_topics": list(mapping_topics or []),
        "components": dict(components or {}),
        "equations": dict(equations or {}),
        "claims": dict(claims or {}),
        "evidence": dict(evidence or {}),
        "figure_claim_links": {},
    }


def _evidence(evidence_id: str, block_id: str, text: str = "根拠本文") -> dict:
    return {
        "evidence_id": evidence_id,
        "document_id": DOC,
        "source": {"page": 1, "section_id": "s1", "block_id": block_id},
        "evidence_text": text,
        "evidence_role": "source_quote",
    }


# ---------------------------------------------------------------------------
# 1. 位置代入の廃止
# ---------------------------------------------------------------------------

def test_position_assignment_helper_is_gone():
    """位置代入の実装そのものが残っていないこと（復活の予防）。"""
    assert not hasattr(ccb, "_fallback_chunk_for_topic")


def test_unlinked_topic_has_no_source_and_states_the_fact():
    """mapping も component も無いトピックは出典を捏造せず事実文だけを載せる。"""
    chunks = {
        "m1": [
            _chunk("chunk-a", 0, ["b_001"], "第1章: 暗黒物質の観測的証拠"),
            _chunk("chunk-b", 1, ["b_002"], "第2章: 共振器の基礎"),
        ]
    }
    topics = [{"id": "t1", "title": "Ideal resonance assumptions"}]

    enriched = ccb._enrich_topics(topics, _bundle(), chunks)

    topic = enriched[0]
    assert topic["material_chunk_ids"] == []
    assert topic["source_excerpt"] == ""
    assert topic["content_source"] == "unlinked"
    assert topic["grounding_note"] == ccb.UNLINKED_TOPIC_GROUNDING_NOTE
    # 事実文であって、督促でも数値でもない。
    assert "%" not in topic["grounding_note"]
    # 原稿スタジオのカバレッジ表示が "missing" のままになること（content_source の
    # 語彙変更で lsTopicCoverageStatus の分岐が外れる分を coverage で明示している）。
    assert topic["coverage"]["status"] == "missing"


def test_unlinked_topic_summary_is_empty_not_an_unrelated_excerpt():
    """要約にも無関係な段落を流し込まない（_topic_summary の fallback_chunk 廃止）。"""
    chunks = {"m1": [_chunk("chunk-a", 0, ["b_001"], "第1章の本文がここにある。" * 20)]}
    topics = [{"id": "t1", "title": "まったく別の題目"}]

    enriched = ccb._enrich_topics(topics, _bundle(), chunks)

    assert enriched[0]["summary"] == ""
    assert enriched[0]["content"] == ""


def test_topic_summary_signature_no_longer_takes_a_fallback_chunk():
    assert ccb._topic_summary({"description": "説明"}, []) == "説明"
    assert ccb._topic_summary({}, [{"summary": "component の要約"}]) == "component の要約"
    assert ccb._topic_summary({}, []) == ""


# ---------------------------------------------------------------------------
# 2. 接続トピックの出典は block_id の交差で決まる
# ---------------------------------------------------------------------------

def _linked_fixture():
    components = {
        "comp_001": {
            "component_id": "comp_001",
            "document_id": DOC,
            "label": "共振条件",
            "summary": "共振器の共振条件を定義する。",
            "linked_evidence_ids": ["ev_7"],
            "linked_claim_ids": [],
            "linked_equation_ids": [],
        }
    }
    bundle = _bundle(
        components=components,
        mapping_topics=[{
            "title": "共振条件",
            "description": "共振器の共振条件。",
            "linked_component_ids": ["comp_001"],
        }],
        evidence={"ev_7": _evidence("ev_7", "b_555", "共振条件は …")},
    )
    return bundle


def test_linked_topic_source_comes_from_evidence_block_intersection():
    bundle = _linked_fixture()
    chunks = {
        "m1": [
            _chunk("chunk-a", 0, ["b_001"], "無関係な第1章"),
            _chunk("chunk-b", 1, ["b_555"], "共振条件を述べる段落。"),
            _chunk("chunk-c", 2, ["b_900"], "また別の段落"),
        ]
    }
    topics = [{"id": "t1", "title": "共振条件"}]

    topic = ccb._enrich_topics(topics, bundle, chunks)[0]

    assert topic["material_chunk_ids"] == ["chunk-b"]
    assert topic["source_excerpt"].startswith("共振条件を述べる段落")
    assert topic["content_source"] == "agent_mapping"
    assert "grounding_note" not in topic


def test_linked_topic_source_is_independent_of_chunk_order():
    """位置と無関係であることを、チャンクの並びを変えて確認する。"""
    bundle = _linked_fixture()
    ordered = [
        _chunk("chunk-a", 0, ["b_001"], "無関係な第1章"),
        _chunk("chunk-b", 1, ["b_555"], "共振条件を述べる段落。"),
        _chunk("chunk-c", 2, ["b_900"], "また別の段落"),
    ]
    reordered = [ordered[2], ordered[0], ordered[1]]
    topics = [{"id": "t1", "title": "共振条件"}]

    first = ccb._enrich_topics(topics, _linked_fixture(), {"m1": ordered})[0]
    second = ccb._enrich_topics(topics, bundle, {"m1": reordered})[0]

    assert first["material_chunk_ids"] == second["material_chunk_ids"] == ["chunk-b"]
    assert first["source_excerpt"] == second["source_excerpt"]


def test_linked_topic_with_no_block_intersection_keeps_source_empty():
    """根拠は宣言されているがチャンクに載っていない場合、出典は空のまま。"""
    bundle = _linked_fixture()
    chunks = {"m1": [_chunk("chunk-a", 0, ["b_001"], "別の段落")]}

    topic = ccb._enrich_topics([{"id": "t1", "title": "共振条件"}], bundle, chunks)[0]

    assert topic["material_chunk_ids"] == []
    assert topic["source_excerpt"] == ""
    # 対応付け自体はあるので unlinked ではない（出典が引けないだけ）。
    assert topic["content_source"] == "agent_mapping"


def test_claim_route_resolves_source_chunk_through_its_evidence():
    components = {
        "comp_002": {
            "component_id": "comp_002",
            "document_id": DOC,
            "label": "主結果",
            "summary": "主結果を述べる。",
            "linked_claim_ids": ["claim_9"],
        }
    }
    bundle = _bundle(
        components=components,
        mapping_topics=[{
            "title": "主結果",
            "description": "主結果。",
            "linked_component_ids": ["comp_002"],
        }],
        claims={"claim_9": {"claim_id": "claim_9", "document_id": DOC, "source_evidence_ids": ["ev_9"]}},
        evidence={"ev_9": _evidence("ev_9", "b_777")},
    )
    chunks = {
        "m1": [
            _chunk("chunk-a", 0, ["b_111"], "前置き"),
            _chunk("chunk-b", 1, ["b_777"], "主結果の段落。"),
        ]
    }

    topic = ccb._enrich_topics([{"id": "t1", "title": "主結果"}], bundle, chunks)[0]

    assert topic["material_chunk_ids"] == ["chunk-b"]


def test_source_chunks_are_capped_and_ordered_by_chunk_position():
    evidence = {f"ev_{i}": _evidence(f"ev_{i}", f"b_{i}") for i in range(8)}
    components = {
        "comp_003": {
            "component_id": "comp_003",
            "document_id": DOC,
            "label": "広い要素",
            "summary": "多数の根拠を持つ。",
            # 宣言順をわざと逆にして、出力がチャンクの並び順であることを確認する。
            "linked_evidence_ids": [f"ev_{i}" for i in reversed(range(8))],
        }
    }
    bundle = _bundle(
        components=components,
        mapping_topics=[{"title": "広い要素", "linked_component_ids": ["comp_003"]}],
        evidence=evidence,
    )
    chunks = {"m1": [_chunk(f"chunk-{i}", i, [f"b_{i}"], f"段落{i}") for i in range(8)]}

    topic = ccb._enrich_topics([{"id": "t1", "title": "広い要素"}], bundle, chunks)[0]

    assert topic["material_chunk_ids"] == ["chunk-0", "chunk-1", "chunk-2", "chunk-3", "chunk-4"]


def test_block_id_is_not_matched_across_documents():
    """block_id は document 内でのみ一意。別論文のチャンクを出典にしない。"""
    other = _chunk("chunk-other", 0, ["b_555"], "別の論文の段落")
    other["document_id"] = "doc-2"
    mine = _chunk("chunk-mine", 1, ["b_555"], "この論文の段落")
    chunks = {"m1": [other, mine]}

    topic = ccb._enrich_topics([{"id": "t1", "title": "共振条件"}], _linked_fixture(), chunks)[0]

    assert topic["material_chunk_ids"] == ["chunk-mine"]


# ---------------------------------------------------------------------------
# 3. fallback formulas の絞り込み（C-10 / S-11）
# ---------------------------------------------------------------------------

def _formula(formula_id: str) -> dict:
    return {"id": formula_id, "latex": f"X_{{{formula_id}}} = 1", "spoken": ""}


def test_chunk_formulas_are_limited_to_linked_equation_ids():
    """チャンクが持つ 52 式のうち、リンクされた式だけが content_blocks に入る。"""
    all_formulas = [_formula(f"eq_{i}") for i in range(52)]
    components = {
        "comp_004": {
            "component_id": "comp_004",
            "document_id": DOC,
            "label": "要素",
            "summary": "要素の要約。",
            "linked_evidence_ids": ["ev_1"],
            # equation_semantics artifact 側には無い式 ID（＝ _equations_for_components
            # では拾えず、チャンク由来の補充枠でのみ入りうる）。
            "linked_equation_ids": ["eq_3"],
        }
    }
    bundle = _bundle(
        components=components,
        mapping_topics=[{"title": "要素", "linked_component_ids": ["comp_004"]}],
        evidence={"ev_1": _evidence("ev_1", "b_001")},
    )
    chunks = {"m1": [_chunk("chunk-a", 0, ["b_001"], "本文", formulas=all_formulas)]}

    topic = ccb._enrich_topics([{"id": "t1", "title": "要素"}], bundle, chunks)[0]

    equation_blocks = [b for b in topic["content_blocks"] if b.get("type") == "equations"]
    assert len(equation_blocks) == 1
    ids = [item["equation_id"] for item in equation_blocks[0]["items"]]
    assert ids == ["eq_3"]


def test_unlinked_topic_gets_no_chunk_formulas_at_all():
    """無接続トピックには出典チャンクが無いので式も複製されない。"""
    all_formulas = [_formula(f"eq_{i}") for i in range(52)]
    chunks = {"m1": [_chunk("chunk-a", 0, ["b_001"], "本文", formulas=all_formulas)]}

    topic = ccb._enrich_topics([{"id": "t1", "title": "無関係"}], _bundle(), chunks)[0]

    assert [b for b in topic["content_blocks"] if b.get("type") == "equations"] == []


def test_same_formula_set_is_not_replicated_across_every_topic():
    """S-11 の複製（全トピック × 全式）が起きないことを2トピックで確認する。"""
    all_formulas = [_formula(f"eq_{i}") for i in range(52)]
    components = {
        "comp_a": {
            "component_id": "comp_a",
            "document_id": DOC,
            "label": "A",
            "summary": "A の要約。",
            "linked_evidence_ids": ["ev_a"],
            "linked_equation_ids": ["eq_1"],
        },
        "comp_b": {
            "component_id": "comp_b",
            "document_id": DOC,
            "label": "B",
            "summary": "B の要約。",
            "linked_evidence_ids": ["ev_b"],
            "linked_equation_ids": ["eq_2"],
        },
    }
    bundle = _bundle(
        components=components,
        mapping_topics=[
            {"title": "A", "linked_component_ids": ["comp_a"]},
            {"title": "B", "linked_component_ids": ["comp_b"]},
        ],
        evidence={
            "ev_a": _evidence("ev_a", "b_001"),
            "ev_b": _evidence("ev_b", "b_002"),
        },
    )
    chunks = {
        "m1": [
            _chunk("chunk-a", 0, ["b_001"], "A の本文", formulas=all_formulas),
            _chunk("chunk-b", 1, ["b_002"], "B の本文", formulas=all_formulas),
        ]
    }

    enriched = ccb._enrich_topics(
        [{"id": "t1", "title": "A"}, {"id": "t2", "title": "B"}], bundle, chunks
    )

    def equation_ids(topic):
        for block in topic["content_blocks"]:
            if block.get("type") == "equations":
                return [item["equation_id"] for item in block["items"]]
        return []

    assert equation_ids(enriched[0]) == ["eq_1"]
    assert equation_ids(enriched[1]) == ["eq_2"]


def test_referenced_formula_ids_reads_both_notations():
    found = ccb._referenced_formula_ids(
        "冒頭 [[FORMULA_3]] の説明", "ここで ![[equation:eq_2_7]] を使う"
    )
    assert found == {"FORMULA_3", "eq_2_7"}


def test_relevant_chunk_formulas_returns_nothing_without_allowed_ids():
    chunks = [_chunk("chunk-a", 0, ["b_001"], "本文", formulas=[_formula("eq_1")])]
    assert ccb._relevant_chunk_formulas(chunks, set()) == []


def test_relevant_chunk_formulas_deduplicates_across_chunks():
    formulas = [_formula("eq_1")]
    chunks = [
        _chunk("chunk-a", 0, ["b_001"], "本文", formulas=formulas),
        _chunk("chunk-b", 1, ["b_002"], "本文", formulas=formulas),
    ]
    assert [f["id"] for f in ccb._relevant_chunk_formulas(chunks, {"eq_1"})] == ["eq_1"]


# ---------------------------------------------------------------------------
# 4. 式の block_id 経路
# ---------------------------------------------------------------------------

def test_equation_block_id_reads_both_artifact_shapes():
    nested = {"source_extraction": {"source_location": {"block_id": "b_42"}}}
    flat = {"source_location": {"block_id": "b_43"}}
    assert ccb._equation_block_id(nested) == "b_42"
    assert ccb._equation_block_id(flat) == "b_43"
    assert ccb._equation_block_id({}) == ""


def test_equation_source_location_contributes_a_source_chunk():
    components = {
        "comp_005": {
            "component_id": "comp_005",
            "document_id": DOC,
            "label": "式の要素",
            "summary": "式で定義する。",
            "linked_equation_ids": ["eq_7"],
        }
    }
    bundle = _bundle(
        components=components,
        mapping_topics=[{"title": "式の要素", "linked_component_ids": ["comp_005"]}],
        equations={
            "eq_7": {
                "equation_id": "eq_7",
                "document_id": DOC,
                "latex": "a = b",
                "source_extraction": {"source_location": {"block_id": "b_321"}},
            }
        },
    )
    chunks = {
        "m1": [
            _chunk("chunk-a", 0, ["b_100"], "前置き"),
            _chunk("chunk-b", 1, ["b_321"], "式が載っている段落。"),
        ]
    }

    topic = ccb._enrich_topics([{"id": "t1", "title": "式の要素"}], bundle, chunks)[0]

    assert topic["material_chunk_ids"] == ["chunk-b"]


# ---------------------------------------------------------------------------
# 5. _load_chunks が block_ids / document_id を運ぶこと
# ---------------------------------------------------------------------------

def test_load_chunks_carries_document_id_and_block_ids():
    class _Result:
        def fetchall(self):
            return [(
                "chunk-1", "m1", 0, "表示本文", "本文", [], None, None,
                "doc-1", ["b_001", "b_002"],
            )]

    class _Session:
        def __init__(self):
            self.sql = ""

        def execute(self, query, params=None):
            self.sql = str(query)
            return _Result()

    session = _Session()
    chunks = ccb._load_chunks(session, ["m1"])

    assert "block_ids" in session.sql
    assert chunks["m1"][0]["document_id"] == "doc-1"
    assert chunks["m1"][0]["block_ids"] == ["b_001", "b_002"]
