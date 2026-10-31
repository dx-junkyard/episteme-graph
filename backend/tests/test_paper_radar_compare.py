"""論文レーダー AI 比較の関係の種類（relation_kind）と共通点の soft 検査。

設計正本: ``docs/features/paper_radar_design.md`` §18（型
``fallback-fabricates-missing-link`` — 「近くない」を言えずに一般論で共通点を埋める）。
"""

from __future__ import annotations

from core.paper_discovery import compare
from core.paper_discovery.compare import (
    RELATION_KINDS,
    RELATION_LABELS,
    _CompareOutput,
    _ItemOut,
    build_prompt,
    validate_items,
)
from core.paper_discovery.schema import ArxivEntry

_SUMMARY = (
    "We study semileptonic B -> D* decays and derive sum rules for b->c "
    "transitions using QCD factorization."
)
_SEED_TEXTS = ["Kurtosis consistency relation in DHOST theories", "重力理論の検証"]


def _candidate() -> ArxivEntry:
    return ArxivEntry(arxiv_id="2610.20611", title="B decays", summary=_SUMMARY)


def _run(relation_kind: str, common_ground: str, labels=None):
    parsed = _CompareOutput(
        items=[
            _ItemOut(
                arxiv_id="2610.20611",
                relation_kind=relation_kind,
                common_ground=common_ground,
            )
        ]
    )
    items, _ = validate_items(
        parsed, [_candidate()], labels or [], seed_texts=_SEED_TEXTS
    )
    assert len(items) == 1  # item は常に保持（P4）
    return items[0]


def test_no_notable_common_ground_empties_the_common_ground():
    item = _run("no_notable_common_ground", "どちらも理論と観測を結ぶかもしれません。")
    assert item["relation_kind"] == "no_notable_common_ground"
    assert item["common_ground"] == ""
    assert item["relation_label"] == RELATION_LABELS["no_notable_common_ground"]
    assert item["caveat"] == compare.CAVEAT


def test_generic_common_ground_is_demoted_to_adjacent():
    item = _run(
        "shared_subject",
        "どちらもモデルのパラメータを観測データで検証する枠組みを持つかもしれません。",
    )
    assert item["relation_kind"] == "adjacent"
    assert item["common_ground"] == ""
    assert item["relation_label"] == RELATION_LABELS["adjacent"]


def test_concrete_common_ground_is_kept():
    text = "どちらも QCD による b->c 遷移の和則を扱っているようです。"
    item = _run("shared_method", text)
    assert item["relation_kind"] == "shared_method"
    assert item["common_ground"] == text


def test_common_ground_naming_a_seed_component_is_kept():
    item = _run("shared_subject", "どちらも状態方程式を扱っているようです。", ["状態方程式"])
    assert item["relation_kind"] == "shared_subject"
    assert item["common_ground"]


def test_unknown_relation_kind_falls_back_to_adjacent():
    item = _run("totally_related", "どちらも QCD を扱うようです。")
    assert item["relation_kind"] == "adjacent"
    assert item["common_ground"] == "どちらも QCD を扱うようです。"
    empty = _run("", "どちらも QCD を扱うようです。")
    assert empty["relation_kind"] == "adjacent"


def test_dto_has_no_numbers_and_labels_cover_the_vocabulary():
    assert set(RELATION_LABELS) == set(RELATION_KINDS)
    item = _run("shared_method", "どちらも QCD を扱うようです。")
    assert not [v for v in item.values() if isinstance(v, (int, float))]


def test_prompt_offers_the_none_exit_and_forbids_generic_fill():
    prompt = build_prompt({"title": "t", "summary": "s"}, [_candidate()])
    assert "no_notable_common_ground" in prompt
    assert "common_ground は空文字にする" in prompt
    assert "一般論" in prompt


# ---------------------------------------------------------------------------
# IK-0709: 部品リストが空のときの指示文 / ファイル名の題名
# ---------------------------------------------------------------------------


def test_prompt_without_components_does_not_mention_absent_list():
    """部品ゼロでは「下の部品リスト」に言及せず、overlaps を空にする指示を出す。"""
    material = {"title": "T", "summary": "S"}
    content = build_prompt(material, [_candidate()], [])
    assert "下の部品リスト" not in content
    assert "【起点論文の部品リスト】" not in content
    assert "承認済みの理論部品がまだ無いので、overlaps は空配列にし" in content
    # 部品があるときは従来の契約フレーズのまま。
    with_list = build_prompt(material, [_candidate()], ["Kurtosis"])
    assert "下の部品リストにある名前をそのまま使う" in with_list
    assert "承認済みの理論部品がまだ無い" not in with_list


def test_seed_title_prefers_arxiv_title_when_filename_like():
    """教材名が arXiv のファイル名なら写しの arXiv 題名をプロンプトに渡す。"""
    seed = {
        "title": "arXiv-2508.06322v2",
        "display_title": "Kurtosis consistency relation",
        "arxiv_title": "Kurtosis consistency relation",
        "summary": "S",
    }
    assert compare.build_seed_material(seed)["title"] == "Kurtosis consistency relation"
    # display_title が無くても arxiv_title に倒す。
    seed.pop("display_title")
    assert compare.seed_prompt_title(seed) == "Kurtosis consistency relation"
    # 題名が普通の文なら教材名のまま（arXiv 題名で上書きしない）。
    plain = {"title": "My lecture notes", "arxiv_title": "Other"}
    assert compare.seed_prompt_title(plain) == "My lecture notes"
    # 写しが無ければファイル名のまま（捏造しない）。
    assert compare.seed_prompt_title({"title": "arXiv-2508.06322v2"}) == "arXiv-2508.06322v2"
