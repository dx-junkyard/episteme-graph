"""学習者に見せる日付・「今日」の境界の正本（日本時間, JST = UTC+9）。

DB の ``timestamptz`` は UTC で返る。学習者向けの「9/27」「今日の言葉」をそのまま UTC
の暦日で作ると、日本の朝の学習が前日に見え、前日の夜の発話が「今日」に混ざる
（ペルソナ通し受講 第 8 周・IK-0416 / IK-0417）。JST には夏時間が無いので固定オフセット
で扱い、新しい env は足さない。

FastAPI / DB / LLM を import しない純関数。
"""

from __future__ import annotations

import datetime as _dt

__all__ = ["LEARNER_TZ", "learner_today_start_utc", "to_learner_local"]

#: 学習者向け表示のタイムゾーン（日本時間）。
LEARNER_TZ = _dt.timezone(_dt.timedelta(hours=9), "JST")


def to_learner_local(value) -> _dt.datetime | None:
    """datetime / ISO 文字列を日本時間の aware datetime にする（naive は UTC とみなす）。"""
    if value is None or value == "":
        return None
    dt = value
    if isinstance(value, str):
        try:
            dt = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(dt, _dt.datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(LEARNER_TZ)


def learner_today_start_utc(now: _dt.datetime | None = None) -> _dt.datetime:
    """日本時間の「今日」の 0 時を UTC の aware datetime で返す。"""
    current = now or _dt.datetime.now(_dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=_dt.timezone.utc)
    local = current.astimezone(LEARNER_TZ)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(_dt.timezone.utc)
