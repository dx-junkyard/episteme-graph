"""``learning_states.personal_graph`` (JSONB) の正本アクセサ。

``core.course_data`` と同じ「素の dict アクセス禁止」方式（Pydantic・``extra="allow"``
で未知キーを一切落とさない）。

**列の位置づけ（設計書 §5）**: この列は「AI が見つけた誤解や個別メモの差分」の置き場であり、
**個人ネットワークの格納庫ではない**（PN-2: 個人ネットワークは常に
``core.personal_graph.derive`` が痕跡から導出するものであり、保存しない）。

現行の実データ形状（``backend/api/services.py`` の ``get_personal_layer`` /
``record_personal_misconception`` / ``detect_and_record_misconception`` を確認して
一致させたもの）:

    {"misconceptions_by_topic": {"<topic_id>": [{"label": str, "wrong": str,
     "correct": str}, ...]}, "chat_anchors": {...}}

``misconceptions_by_topic`` の値は topic_id ごとに最新5件を保持する自由形式 dict の配列。
``chat_anchors`` 等の未知キーは ``extra="allow"`` により失われない。

呼び出し側（``services.py`` の ``get_personal_layer`` / ``record_personal_misconception``）は
本アクセサ経由に移行済み（Phase P-0。以降、この列への素の dict アクセスを新規に書かない）。

FastAPI は import しない（core/ 規約）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# services.py::record_personal_misconception が踏襲している「topic ごとに最新 N 件」の上限。
_MISCONCEPTION_LIMIT_PER_TOPIC = 5


class PersonalGraphData(BaseModel):
    """``learning_states.personal_graph`` 列全体のスキーマ正本。"""

    model_config = ConfigDict(extra="allow")

    misconceptions_by_topic: dict[str, list] = Field(default_factory=dict)
    # チャット履歴由来の注釈データ（将来拡張用。services.get_personal_layer が返す既存キー）
    chat_anchors: dict = Field(default_factory=dict)


def parse_personal_graph(raw: dict | None) -> PersonalGraphData:
    """DB から読んだ生 JSONB（None・非 dict も含む）を検証済みモデルへ変換する。

    旧: ``row[0] if isinstance(row[0], dict) else json.loads(row[0])`` に続く
    ``personal.get("misconceptions_by_topic", {}) or {}`` の置換先。
    """
    if not isinstance(raw, dict):
        return PersonalGraphData()
    return PersonalGraphData.model_validate(raw)


def to_jsonb(data: PersonalGraphData) -> dict:
    """UPDATE 文へ渡す JSONB 用 dict に戻す（``extra="allow"`` の未知キーも落とさない）。"""
    return data.model_dump(mode="json")


def misconceptions_for_topic(data: PersonalGraphData, topic_id: str) -> list[dict]:
    """指定 topic_id の誤解一覧を返す（無ければ空リスト。非 dict 要素は防御的に除外する）。"""
    entries = data.misconceptions_by_topic.get(topic_id) or []
    return [e for e in entries if isinstance(e, dict)]


def append_misconception(data: PersonalGraphData, topic_id: str, entry: dict) -> PersonalGraphData:
    """topic_id の誤解一覧の先頭に entry を追加し、最新5件までを保持した新しいモデルを返す。

    純関数（``data`` 自体は変更しない）。呼び出し側は返り値を ``to_jsonb`` で JSONB 化して
    UPDATE すること。既存 ``record_personal_misconception`` の「最新5件」上限を踏襲する。
    """
    updated = data.model_copy(deep=True)
    current = list(updated.misconceptions_by_topic.get(topic_id) or [])
    current = [entry] + current
    updated.misconceptions_by_topic[topic_id] = current[:_MISCONCEPTION_LIMIT_PER_TOPIC]
    return updated
