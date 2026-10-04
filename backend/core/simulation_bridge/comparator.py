"""比較器（SB層 §5.3・決定論・非LLM・DB 非接触）。

理論の出力・模擬観測・（任意）論文の記載値を突き合わせ、段階ラベル・閉世界の事実文・
教員向けの数値（``metrics``）を返す。段階の閾値とラベルの正本は ``core/label_vocab.py``
（``SIMULATION_AGREEMENT_*`` / ``SIMULATION_SEPARATION_*``）。

- 一致: 相対差 |obs − theory| / max(|theory|, ε)。記号ごとに求め、最も悪い（大きい）値で代表する。
- 分離: |theory| / std。最も悪い（小さい）値で代表する。std が無い・0 なら測らない（割らない）。
- 記載値: reference があるときだけ、模擬観測（無ければ理論）の平均との相対差。

事実文は数値を含まず、閉世界（「このモデルと配線のもとでは」）で書く。最後は必ず
``FACT_NOT_VERIFICATION``（SB1）。confidence は出さない（SB6）。
"""

from __future__ import annotations

import math
from typing import Any

from core import label_vocab

from . import schema

__all__ = ["EPSILON", "compare", "comparison_statement", "scalar"]

#: 相対差の分母の下限（理論の出力が 0 のとき）。
EPSILON = 1e-12
# 理論の出力がゼロの記号の事実文は ``schema.zero_theory_fact``（第 21 周 R6: 記号を名指しする）。

_REFERENCE_SENTENCES: tuple[tuple[str, str], ...] = (
    (schema.AGREEMENT_LEVEL_MATCH, "論文の記載値と一致しています。"),
    (schema.AGREEMENT_LEVEL_ROUGH, "論文の記載値とおおむね一致しています。"),
    (schema.AGREEMENT_LEVEL_DIVERGE, "論文の記載値と乖離しています。"),
    (schema.AGREEMENT_LEVEL_UNMEASURED, "論文の記載値との差は測れませんでした。"),
)


def _scalar(value: Any) -> float | None:
    """数値か数値の一覧を 1 つの有限値に畳む（一覧は平均）。畳めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    if isinstance(value, (list, tuple)):
        flat: list[float] = []
        stack = list(value)
        while stack:
            item = stack.pop()
            if isinstance(item, (list, tuple)):
                stack.extend(item)
                continue
            numeric = _scalar(item)
            if numeric is None:
                return None
            flat.append(numeric)
        if not flat:
            return None
        return sum(flat) / len(flat)
    return None


def scalar(value: Any) -> float | None:
    """公開名（配線なしの観測を理論値で埋めるときに service が使う）。"""
    return _scalar(value)


def _rel_diff(value: float, base: float) -> float | None:
    """相対差 |value − base| / |base|。理論の出力がゼロ（EPSILON 未満）のときは測れないので None
    （実機 2026-10-03: 直交する波数で理論出力が 0 になり、ノイズだけで「乖離」と出た空疎な判定の是正）。"""
    if abs(base) < EPSILON:
        return None
    return abs(value - base) / abs(base)


def _observed_stats(entry: Any) -> tuple[float | None, float | None]:
    if isinstance(entry, dict):
        mean = _scalar(entry.get("mean"))
        std = _scalar(entry.get("std"))
        return mean, std
    return _scalar(entry), None


def _reference_value(entry: Any) -> float | None:
    if isinstance(entry, dict):
        return _scalar(entry.get("value"))
    return _scalar(entry)


def compare(
    theory_outputs: dict,
    observed: dict | None,
    *,
    reference: dict | None = None,
    wiring_name: str = "",
) -> schema.Comparison:
    """理論の出力と模擬観測（と任意の記載値）を比較する。"""
    theory: dict[str, float] = {}
    for symbol, value in (theory_outputs or {}).items():
        numeric = _scalar(value)
        if numeric is not None:
            theory[str(symbol)] = numeric

    no_wiring = observed is None
    by_symbol: dict[str, dict[str, float | None]] = {}
    rel_values: list[float] = []
    sep_values: list[float] = []
    sep_unmeasured = False
    zero_theory: list[str] = []
    point_values: dict[str, float] = {}

    for symbol in sorted(theory):
        base = theory[symbol]
        if no_wiring:
            mean, std = base, None
        else:
            if symbol not in observed:
                continue
            mean, std = _observed_stats(observed[symbol])
            if mean is None:
                continue
        # 配線なしは観測が理論値そのものなので、記号ごとの相対差も測らない（0.0 を「一致」と読ませない — 第 21 周の再演）
        rel = None if no_wiring else _rel_diff(mean, base)
        # ゼロの記号を名指しするのは、配線ありで相対差を測ろうとして測れなかったときだけ（配線なしは
        # そもそも測らないので「ゼロ」ではない — 第 22 周 (i): B_0 = 2.4e-4 に「ゼロの記号」と出た）。
        if rel is None and not no_wiring and abs(base) < EPSILON:
            zero_theory.append(symbol)
        ratio: float | None = None
        if not no_wiring and std is not None and std > 0:
            ratio = abs(base) / std
            sep_values.append(ratio)
        else:
            sep_unmeasured = True
        if rel is not None:
            rel_values.append(rel)
        point_values[symbol] = mean
        by_symbol[symbol] = {
            schema.METRIC_RELATIVE_DIFFERENCE: rel,
            schema.METRIC_SEPARATION_RATIO: ratio,
            schema.METRIC_REFERENCE_DIFFERENCE: None,
        }

    # 配線なし（観測なし）のときは模擬観測が理論の出力そのものなので、一致は測っていない（空疎な「一致」を
    # 出さない — 実機 2026-10-03 の是正）。相対差は None にして段階ラベルを未測定に落とす。
    relative_difference = None if no_wiring else (max(rel_values) if rel_values else None)
    # 1 記号でも分離を測れなければ、分離は測れなかったとする（最悪値を見落とさない）。
    separation_ratio = min(sep_values) if sep_values and not sep_unmeasured else None

    reference_values: list[float] = []
    reference_given = bool(reference)
    if reference_given:
        for symbol, entry in reference.items():
            ref = _reference_value(entry)
            symbol = str(symbol)
            if ref is None:
                continue
            point = point_values.get(symbol)
            if point is None and symbol in theory:
                point = theory[symbol]
            if point is None:
                continue
            diff = _rel_diff(point, ref)
            if diff is None:  # 記載値がゼロのときは測れない（None を max() に混ぜない）
                continue
            reference_values.append(diff)
            by_symbol.setdefault(
                symbol,
                {
                    schema.METRIC_RELATIVE_DIFFERENCE: None,
                    schema.METRIC_SEPARATION_RATIO: None,
                    schema.METRIC_REFERENCE_DIFFERENCE: None,
                },
            )[schema.METRIC_REFERENCE_DIFFERENCE] = diff
    reference_difference = max(reference_values) if reference_values else None

    agreement_level = label_vocab.simulation_agreement_level(relative_difference)
    separation_level = label_vocab.simulation_separation_level(separation_ratio)
    reference_level = label_vocab.simulation_agreement_level(reference_difference)

    facts = [
        schema.closed_world(schema.AGREEMENT_FACTS[agreement_level], wiring_name=wiring_name),
        schema.closed_world(schema.SEPARATION_FACTS[separation_level], wiring_name=wiring_name),
    ]
    if reference_given:
        sentence = dict(_REFERENCE_SENTENCES)[reference_level]
        facts.append(schema.closed_world("模擬観測は" + sentence, wiring_name=wiring_name))
        # 何を・どの単位で比べたかを名指しする（第 22 周 (i): 単位が画面にも AI の文脈にも渡らず、換算の誤りを疑わせた）
        reference_fact = schema.reference_input_fact(reference)
        if reference_fact:
            facts.append(reference_fact)
    else:
        facts.append(schema.FACT_REFERENCE_MISSING)
    if no_wiring:
        facts.append(schema.FACT_NO_WIRING)
    if zero_theory:
        facts.append(schema.zero_theory_fact(zero_theory))
    facts.append(schema.FACT_NOT_VERIFICATION)

    metrics: dict[str, Any] = {
        schema.METRIC_RELATIVE_DIFFERENCE: relative_difference,
        schema.METRIC_SEPARATION_RATIO: separation_ratio,
        schema.METRIC_REFERENCE_DIFFERENCE: reference_difference,
        "by_symbol": by_symbol,
    }
    return schema.Comparison(
        agreement_level=agreement_level,
        separation_level=separation_level,
        facts=facts,
        metrics=metrics,
        reference_level=reference_level,
    )


def comparison_statement(comparison: Any, *, module_label: str, wiring_name: str) -> str:
    """台帳候補の ``statement`` に使う閉世界の 1 文（数値なし）。"""
    if isinstance(comparison, schema.Comparison):
        level = comparison.agreement_level
    elif isinstance(comparison, dict):
        level = str(comparison.get("agreement_level") or schema.AGREEMENT_LEVEL_UNMEASURED)
    else:
        level = schema.AGREEMENT_LEVEL_UNMEASURED
    if level not in schema.AGREEMENT_FACTS:
        level = schema.AGREEMENT_LEVEL_UNMEASURED
    label = (module_label or "").strip()
    fact = schema.AGREEMENT_FACTS[level]
    sentence = f"理論モジュール『{label}』について、{fact}" if label else fact
    return schema.closed_world(sentence, wiring_name=wiring_name)
