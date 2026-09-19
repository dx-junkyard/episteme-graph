"""stable_key — 内容由来・版非依存の同一性キー（knowledge_objects_design.md §5.1 / KO2）。

材料は ``document_id`` + 正規化テキスト + 出典 block_id 集合（+ 種別固有の少数の構造項）。
run_id・出現順・agent ID・confidence は **材料にしない**。正規化は agent 側 ``content_hash`` と
同じ ``episteme_graph.agents.content_normalization``（stdlib のみ）を使い、新しい正規化を書かない。

すべて純関数。DB・FastAPI・LLM を import しない。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Sequence
from typing import Any, TypeVar

from episteme_graph.agents.content_normalization import (
    normalize_equation_for_hash,
    normalize_text_for_hash,
)

from .schema import STABLE_KEY_VERSION_PREFIX

_SEP = "\x1f"

T = TypeVar("T")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _join_sorted(values: Iterable[Any]) -> str:
    return ",".join(sorted({_clean(v) for v in values if _clean(v)}))


def digest(parts: Sequence[str]) -> str:
    """``k1:`` + sha256(parts を \\x1f 連結)[:32]。"""
    raw = _SEP.join(str(p) for p in parts).encode("utf-8")
    return STABLE_KEY_VERSION_PREFIX + hashlib.sha256(raw).hexdigest()[:32]


# ---------------------------------------------------------------------------
# 種別ごとのキー
# ---------------------------------------------------------------------------


def claim_stable_key(document_id: str, text: str, block_ids: Iterable[str]) -> str:
    """claim。``text`` は normalized_text（無ければ text）。block_ids は出典 block の集合。"""
    return digest(["claim", _clean(document_id), normalize_text_for_hash(text), _join_sorted(block_ids)])


def component_stable_key(
    document_id: str, label: str, operation: str, block_ids: Iterable[str]
) -> str:
    """component。label + operation（primary_operation でも可）+ 出典 block 集合。"""
    return digest([
        "component", _clean(document_id), normalize_text_for_hash(label),
        _clean(operation), _join_sorted(block_ids),
    ])


def equation_stable_key(
    document_id: str,
    latex: str | None,
    plain_text: str | None,
    block_id: str | None,
    label: str | None = None,
) -> str:
    """equation。正規化 LaTeX（無ければ plain / raw）+ block_id（無ければ label）。"""
    return digest([
        "equation", _clean(document_id), normalize_equation_for_hash(latex, plain_text),
        _clean(block_id) or _clean(label),
    ])


def evidence_stable_key(document_id: str, block_id: str, evidence_text: str) -> str:
    """evidence（逐語根拠）。block_id + 正規化本文。"""
    return digest(["evidence", _clean(document_id), _clean(block_id), normalize_text_for_hash(evidence_text)])


def derivation_step_agent_id(derivation_id: str, step_id: str) -> str:
    """derivation step の **文書内一意な agent ID**（``"{derivation_id}:{step_id}"``）。

    agent 側の ``step_id``（``step_001`` 等）はチェーン内でしか一意でなく、別チェーンの
    同名 step が同じ ID を名乗る。ID が衝突したままだと参照の解決先が曖昧になるので
    （``uq_knowledge_derivation_steps_stable_key_live`` 違反の原因でもあった。2026-09-13 の
    実データ検証 V-2）、ここで文書内一意にする。書き手（パイプライン）と取り込み
    （``core/knowledge_import/rows.py``）が**同じ規則**を使うため、ここを正本にする。
    """
    chain = _clean(derivation_id)
    step = _clean(step_id)
    if not chain:
        return step
    return f"{chain}:{step}" if step else chain


def derivation_step_stable_key(
    document_id: str,
    operation: str,
    input_equation_keys: Iterable[str],
    output_equation_keys: Iterable[str],
    *,
    derivation_id: str = "",
    step_index: int | None = None,
) -> str:
    """derivation step。operation + 入力式キー集合 + 出力式キー集合（式は stable_key、解決不能なら agent ID）。

    ``derivation_id`` / ``step_index`` は **チェーン内の位置**を材料に加える
    （KO2「出現順・agent ID を材料にしない」の明示例外。2026-09-13 の実データ検証 V-5）。
    実論文では 1 チェーンに同じ operation の step が何十個も並び、式参照が解決できないと
    素キーが数種類に潰れて大半が ``#n`` サフィックスになる。``#n`` は**文書全体の項目順**で
    振られるため、別チェーンの step が 1 つ増減しただけで付け替わり、内容が変わっていない
    step まで supersede が連鎖した。チェーン ID と序数を材料に含めると、他チェーンの変化が
    このチェーンのキーに波及しない。**同一チェーン内で step を挿入すると以降の序数がずれる**
    という限界は残る（`#n` と同じで悪化はしない）。
    """
    return digest([
        "derivation_step", _clean(document_id), _clean(operation),
        _join_sorted(input_equation_keys), _join_sorted(output_equation_keys),
        _clean(derivation_id),
        "" if step_index is None else str(int(step_index)),
    ])


def symbol_stable_key(
    document_id: str,
    canonical_symbol: str,
    scope: str,
    defining_equation_keys: Iterable[str],
) -> str:
    """symbol。canonical_symbol + scope + 定義式キー集合。"""
    return digest([
        "symbol", _clean(document_id), _clean(canonical_symbol), _clean(scope),
        _join_sorted(defining_equation_keys),
    ])


def learning_unit_stable_key(
    document_id: str,
    unit_kind: str,
    text: str,
    refs: Iterable[str],
) -> str:
    """学ぶ単位（learning_units_design.md §5・migration 081）。

    材料は種別 + 正規化テキスト + 参照集合だけで、``order_index`` / run_id /
    confidence は入れない（章の並びが変わっても同じ単位を指し続ける）。``refs`` の
    中身は種別ごとに決まる（section_block = 出典 block 集合 / thesis_support =
    出所 ID + claim・equation の agent ID 集合 / parent_component = 子の出典 block 集合 /
    dsl_node = 空 / figure = figure_id）。集合なので順序には依存しない。
    """
    return digest([
        "learning_unit", _clean(document_id), _clean(unit_kind),
        normalize_text_for_hash(text), _join_sorted(refs),
    ])


# ---------------------------------------------------------------------------
# 同一 run 内の衝突解消
# ---------------------------------------------------------------------------


def assign_stable_keys(
    items: Sequence[T],
    *,
    key_of: Callable[[T], str],
    agent_id_of: Callable[[T], str],
) -> list[str]:
    """同一 run 内の stable_key 衝突を **項目ごとに** 解いて ``#2`` ``#3`` … を付ける。

    ``items`` と同じ長さ・同じ並びの最終キー列を返す。並び順の規則は
    「agent ID 昇順 → 入力順」で、1 番目が素のキー、2 番目以降が ``#n``。

    **agent ID が重複していても項目は潰れない**のがこの関数の要点（A層は agent ID の
    一意性を保証しない。例: 数式 ID は印字番号由来なので ``eq_7`` が別ブロックで再び
    現れ得る）。agent ID をキーにした写像で配ると、同じ ID の項目が全部同じキーを
    受け取り ``uq_<table>_stable_key_live`` に当たって同期全体が落ちる。行に配るキーは
    必ずこの関数で決める（:func:`dedupe_stable_keys` は ID が一意な呼び出し側専用）。

    agent ID が空の項目は ID 持ちの後ろに入力順で回す（位置で代用しない）。
    """
    groups: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        groups.setdefault(_clean(key_of(item)), []).append(index)
    out: list[str] = [""] * len(items)
    for key, indexes in groups.items():
        ordered = sorted(
            indexes,
            key=lambda index: (
                0 if _clean(agent_id_of(items[index])) else 1,
                _clean(agent_id_of(items[index])),
                index,
            ),
        )
        for rank, index in enumerate(ordered):
            out[index] = key if rank == 0 else f"{key}#{rank + 1}"
    return out


def dedupe_stable_keys(
    items: Sequence[T],
    *,
    key_of: Callable[[T], str],
    agent_id_of: Callable[[T], str],
) -> dict[str, str]:
    """同一 run 内で同じ stable_key を持つ項目に決定論的に ``#2`` ``#3`` … を付ける。

    Returns:
        ``{agent_id: final_stable_key}``。衝突が無い項目は元のキーのまま。衝突した組は
        agent ID 昇順で 1 番目が素のキー、2 番目以降が ``#n``。agent ID が空の項目は
        位置で代用しない（その項目は ``agent_id_of`` の戻り値 ``""`` をキーに1件だけ載る）。

    **agent ID が一意な呼び出し側専用**（例: DB の行 id をキーにする backfill）。同じ ID の
    項目が2件以上あると1件に潰れるので、行に配るキーには :func:`assign_stable_keys` を使う。
    """
    finals = assign_stable_keys(items, key_of=key_of, agent_id_of=agent_id_of)
    out: dict[str, str] = {}
    for item, final in zip(items, finals):
        agent_id = _clean(agent_id_of(item))
        if agent_id not in out:
            out[agent_id] = final
    return out
