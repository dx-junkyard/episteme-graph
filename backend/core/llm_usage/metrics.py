"""集計クエリ（設計書 §7-1・アカウントライフサイクル管理設計書 §7-2）。

U1: reported / estimated は常に分離して返す（合算した単一数値を作らない）。
group_by はホワイトリスト（``_GROUP_BY_SQL``）経由でのみ SQL に反映し、文字列連結による
SQL インジェクションを構造的に防ぐ。

``user_id`` は group_by の1軸として使えるほか、``collect_metrics(..., user_id=...)`` で
個票フィルタとしても使える（アカウント個票の LLM 利用サマリ§7-3 用）。表示名解決
（``user_id`` → ``display_name``・「未帰属」「不明ユーザー」）は呼び出し側
（``routes/llm_usage.py``）の責務— この関数は集計値のみを返し、users テーブルを
JOIN しない（U5 の権限判定を呼び出し側に閉じ込めるため）。
"""

from __future__ import annotations

from sqlalchemy import text as sa_text

from core.llm_usage import pricing
from core.llm_usage.recorder import dropped_count

# group_by として許可するフィールドと、対応する SQL 式（値そのものはホワイトリスト外から
# 一切 SQL に混入しない — ここに書かれた固定文字列だけが SQL に入る）。
_GROUP_BY_SQL: dict[str, str] = {
    "day": "date_trunc('day', occurred_at)",
    "feature": "feature",
    "model": "model",
    "provider": "provider",
    "operation": "operation",
    "user_id": "user_id",
}

_EMPTY_BUCKET = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}


def _bucket_name(usage_source: str) -> str:
    return "reported" if usage_source == "reported" else "estimated"


def _format_group_value(field: str, value):
    if field == "day":
        if hasattr(value, "date"):
            return value.date().isoformat()
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return str(value)
    if field == "user_id" and value is not None:
        # UUID オブジェクトのままだと JSON 化できないため文字列化する。
        # None（未帰属）はそのまま None を返し、表示側（routes/llm_usage.py）が
        # 「未帰属」ラベルへ変換する（U1: 生の欠測を隠さない）。
        return str(value)
    return value


def _format_date_value(value) -> str:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def collect_metrics(
    session, *, date_from, date_to, group_by: list[str], user_id: str | None = None
) -> dict:
    """usage_source 別に分離した集計を返す（設計書 §7-1 のレスポンス形）。

    ``group_by`` は ``{"day","feature","model","provider","operation","user_id"}`` の
    部分集合のみ許可する。不正な値は ``ValueError``（SQL には決して混入しない）。

    ``user_id``（任意キーワード引数）を指定すると、集計対象を当該ユーザーの行のみに
    絞り込む（アカウント個票の LLM 利用サマリ §7-3 用）。既存呼び出し（省略時は
    フィルタなし）との後方互換を維持する。
    """
    if not group_by:
        raise ValueError("group_by must not be empty")

    invalid = [g for g in group_by if g not in _GROUP_BY_SQL]
    if invalid:
        raise ValueError(
            f"invalid group_by fields: {invalid!r}; must be a subset of {sorted(_GROUP_BY_SQL)}"
        )

    requested = list(group_by)
    # cost 換算のため、内部集計には常に model を含める（表示列自体は増やさない）。
    internal_group_cols = list(dict.fromkeys(requested + ["model"]))

    select_cols_sql = ", ".join(
        f"{_GROUP_BY_SQL[col]} AS grp_{i}" for i, col in enumerate(internal_group_cols)
    )
    group_clause_sql = ", ".join(_GROUP_BY_SQL[col] for col in internal_group_cols)

    where_sql = "occurred_at >= :date_from AND occurred_at < :date_to"
    params: dict = {"date_from": date_from, "date_to": date_to}
    if user_id is not None:
        where_sql += " AND user_id = :user_id"
        params["user_id"] = user_id

    query = sa_text(
        f"""
        SELECT {select_cols_sql}, usage_source,
               COALESCE(SUM(prompt_tokens), 0) AS sum_prompt,
               COALESCE(SUM(completion_tokens), 0) AS sum_completion,
               COALESCE(SUM(total_tokens), 0) AS sum_total,
               COALESCE(SUM(cached_tokens), 0) AS sum_cached,
               COUNT(*) AS calls
        FROM llm_usage_events
        WHERE {where_sql}
        GROUP BY {group_clause_sql}, usage_source
        """
    )
    result = session.execute(query, params)
    raw_rows = result.fetchall()

    price_table = pricing.load_price_table()

    aggregated: dict[tuple, dict] = {}

    for raw_row in raw_rows:
        row = tuple(raw_row)
        n_group = len(internal_group_cols)
        internal_values = row[:n_group]
        usage_source = row[n_group]
        sum_prompt, sum_completion, sum_total, sum_cached, calls = row[n_group + 1 : n_group + 6]

        value_by_col = dict(zip(internal_group_cols, internal_values))
        display_key = tuple(_format_group_value(col, value_by_col[col]) for col in requested)
        model_value = value_by_col.get("model")

        entry = aggregated.setdefault(
            display_key,
            {
                "reported": dict(_EMPTY_BUCKET),
                "estimated": dict(_EMPTY_BUCKET),
                "cost_usd": 0.0,
                "cost_known": False,
            },
        )

        bucket = entry[_bucket_name(usage_source)]
        bucket["prompt_tokens"] += int(sum_prompt or 0)
        bucket["completion_tokens"] += int(sum_completion or 0)
        bucket["total_tokens"] += int(sum_total or 0)
        bucket["calls"] += int(calls or 0)

        if price_table is not None and model_value:
            cost = pricing.compute_cost_usd(
                model_value,
                int(sum_prompt or 0),
                int(sum_cached or 0),
                int(sum_completion or 0),
                price_table,
            )
            if cost is not None:
                entry["cost_usd"] += cost
                entry["cost_known"] = True

    rows_out = []
    for display_key, entry in aggregated.items():
        key_dict = dict(zip(requested, display_key))
        cost_usd = entry["cost_usd"] if (price_table is not None and entry["cost_known"]) else None
        rows_out.append(
            {
                "key": key_dict,
                "reported": entry["reported"],
                "estimated": entry["estimated"],
                "cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
            }
        )

    return {
        "from": _format_date_value(date_from),
        "to": _format_date_value(date_to),
        "rows": rows_out,
        "dropped_events": dropped_count(),
        "price_table_loaded": price_table is not None,
    }
