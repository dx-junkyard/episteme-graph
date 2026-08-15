"""コスト見通しの一行（静かな計器, Phase 4 教員支援 v1 §3.1）。

正本: ``docs/features/teacher_triage_instruments_design.md`` §3.1 / §5 精査⑤。

「今日のAI利用枠」に対応する**単一カウンタは存在しない** — 実体はパイプライン末端
4ステージの独立カウンタ（``contextual_explanation`` / ``discuss_opening`` /
``landscape_placement`` はプロセスローカル CostGate、``apparatus_semantics`` は
DB 集計）である。本モジュールはそれらの**最小残数による正直な近似**を行い、
日次枠に収まらない可能性があるときだけ固定の事実文を返す。

不変条項:

- **TT2 数値を見せない** — 返却は ``{show: bool, message: str}`` のみ。残回数・
  トークン数・金額は一切返さない。
- **TT4 開始をブロックしない** — 計器は事実文の提示まで。導出に失敗したら
  ``{show: False}`` で何も出さない（fail-open）。enforcement には使わない。
- **TT5 学習者データ非入力** — 入力はカウンタと教材由来の見積りのみ。
- CostGate はプロセスローカルで厳密になり得ず、回数枠とトークン量の単位も揃わない。
  その不確かさは仮説文体（「可能性があります」）が吸収する（§5 精査⑤）。

FastAPI は import しない。orchestrator は import が重いため遅延 import する。
"""

from __future__ import annotations

import logging

from core.llm_usage.document_estimate import estimate_document_run

logger = logging.getLogger(__name__)

# 表示メッセージ（設計書 §3.1 の固定文。数値・残回数を含めない）。
FORECAST_MESSAGE = (
    "この規模の処理は、今日のAI利用枠に収まらない可能性があります。"
    "分けて実行することもできます"
)

# 「収まらない可能性」の保守判定しきい値: いずれかのカウンタの残数が上限の
# この割合を下回った（または 0 になった）とき show=True。
# **この閾値は近似である** — 4カウンタは互いに独立で、回数枠とトークン量の単位も
# 揃わないため、厳密な「収まる/収まらない」は導出できない。発明した閾値の不確かさは
# メッセージの仮説文体（「可能性があります」）に織り込む（§5 精査⑤）。
FORECAST_LOW_REMAINING_RATIO = 0.25


def _gate_remainings(analyze_images: bool) -> list[tuple[str, int, int]]:
    """末端4ステージの (name, daily_remaining, daily_limit) を集める。

    - contextual_explanation / discuss_opening / landscape_placement:
      プロセスローカル CostGate（``daily_remaining`` は読み取りのみ・カウントしない）。
    - apparatus_semantics（``analyze_images=True`` のときのみ）:
      ``orchestrator._apparatus_daily_remaining``（当日 run の vision_calls DB 集計）。

    orchestrator の import は重い（A層 agent 群を掴む）ため遅延 import。
    """
    from core.document_pipeline import orchestrator
    from core.landscape import builder as landscape_builder
    from core.llm_worker.cost_gate import today_str

    day = today_str()
    gates: list[tuple[str, int, int]] = []

    ctxexpl_limit = orchestrator._ctxexpl_max_calls_per_day()
    gates.append((
        "contextual_explanation",
        orchestrator._ctxexpl_cost_gate.daily_remaining(
            daily_limit=ctxexpl_limit, daily_key=day
        ),
        ctxexpl_limit,
    ))

    discuss_limit = orchestrator._discuss_opening_max_calls_per_day()
    gates.append((
        "discuss_opening",
        orchestrator._discuss_opening_cost_gate.daily_remaining(
            daily_limit=discuss_limit, daily_key=day
        ),
        discuss_limit,
    ))

    landscape_limit = landscape_builder._max_calls_per_day()
    gates.append((
        "landscape_placement",
        landscape_builder._landscape_cost_gate.daily_remaining(
            daily_limit=landscape_limit, daily_key=day
        ),
        landscape_limit,
    ))

    if analyze_images:
        from core.config import get_settings

        settings = get_settings()
        apparatus_limit = max(
            0, int(getattr(settings, "apparatus_max_calls_per_day", 30) or 0)
        )
        gates.append((
            "apparatus_semantics",
            orchestrator._apparatus_daily_remaining(settings),
            apparatus_limit,
        ))

    return gates


def _should_show(gates: list[tuple[str, int, int]]) -> bool:
    """最小残数による保守判定（近似。上のコメント参照）。"""
    for _name, remaining, limit in gates:
        if limit <= 0 or remaining <= 0:
            return True
        if remaining / limit < FORECAST_LOW_REMAINING_RATIO:
            return True
    return False


def _result(show: bool) -> dict:
    return {"show": bool(show), "message": FORECAST_MESSAGE if show else ""}


def forecast_run_capacity(*, analyze_images: bool = False) -> dict:
    """document 不要版（アップロードゾーン用）: カウンタの残数のみで判定する。

    まだ存在しない document の規模は見積れないため、判定材料はカウンタだけ
    （§5 精査⑤「アップロードゾーンはカウンタのみ」）。fail-open: 例外時は
    ``{show: False, message: ""}`` を返し、処理を止めない（TT4）。
    """
    try:
        return _result(_should_show(_gate_remainings(analyze_images)))
    except Exception:  # noqa: BLE001 — 計器の失敗で操作を止めない（TT4）
        logger.warning("llm usage forecast (capacity) failed; fail-open", exc_info=True)
        return {"show": False, "message": ""}


def forecast_document_run(session, document_id: str, *, analyze_images: bool = False) -> dict:
    """document 版（再解析モーダル用）: 見積り上振れ × カウンタ残数の合成判定。

    ``estimate_document_run`` の ``total_tokens_range[1]``（上振れ）が 0
    （= 見積れる素材が無い）なら見通しを出さない。素材があるときはカウンタの
    最小残数による保守判定（``_should_show``）で「収まらない可能性」を出す。
    回数枠とトークン量の単位不整合は仮説文体に織り込む（§5 精査⑤）。
    fail-open: 例外時・document 不在時は ``{show: False, message: ""}``（TT4）。
    """
    try:
        estimate = estimate_document_run(session, document_id, analyze_images=analyze_images)
        if not estimate:
            return {"show": False, "message": ""}
        tokens_range = estimate.get("total_tokens_range") or [0, 0]
        upper = int(tokens_range[1]) if len(tokens_range) > 1 else 0
        if upper <= 0:
            return {"show": False, "message": ""}
        return _result(_should_show(_gate_remainings(analyze_images)))
    except Exception:  # noqa: BLE001 — 計器の失敗で操作を止めない（TT4）
        logger.warning(
            "llm usage forecast (document) failed; fail-open", exc_info=True
        )
        return {"show": False, "message": ""}
