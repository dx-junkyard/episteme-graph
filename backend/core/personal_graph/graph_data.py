"""``learning_states.personal_graph`` (JSONB) の正本アクセサ。

``core.course_data`` と同じ「素の dict アクセス禁止」方式（Pydantic・``extra="allow"``
で未知キーを一切落とさない）。

**列の位置づけ（設計書 §5）**: この列は「AI が見つけた誤解や個別メモの差分」の置き場であり、
**個人ネットワークの格納庫ではない**（PN-2: 個人ネットワークは常に
``core.personal_graph.derive`` が痕跡から導出するものであり、保存しない）。

現行の実データ形状（``backend/api/services.py`` の ``get_personal_layer`` /
``record_personal_misconception`` / ``detect_and_record_misconception`` を確認して
一致させたもの）:

    {"misconceptions_by_topic": {"<topic_id>": [{"id": str, "label": str,
     "wrong": str, "correct": str | None, "status": str, "source": str,
     "detected_at": str, "message_id": str | None, "decision": str,
     "reviewed_at": str}, ...]}, "chat_anchors": {...}}

``misconceptions_by_topic`` の値は topic_id ごとの自由形式 dict の配列（新しい順）。
``chat_anchors`` 等の未知キーは ``extra="allow"`` により失われない。

**誤解メモは AI の候補である（是正 F5 / 六つのレンズ 提案3, 2026-09-10）**:
検出は非LLM の文字列一致にすぎないので、書き込みは常に ``status="candidate"`` で行い、
「誤解」として確定するのは本人の3択（``confirmed`` / ``dismissed``）だけである
（原則1: AI は候補まで）。状態語彙は :data:`MISCONCEPTION_VOCAB` として宣言し、
遷移の可否判定は ``core.candidate_flow`` に委ねる（新しいワークフローを書かない）。
かつての「topic ごとに最新5件、6件目で古いものを黙って消す」上限は撤廃した
（原則3: 情報を落とさない。表示件数の制限は読み出し側が「畳む」で行う）。

呼び出し側（``services.py`` の ``get_personal_layer`` / ``record_personal_misconception`` /
``review_personal_misconception``）は本アクセサ経由に移行済み（Phase P-0。以降、この列への
素の dict アクセスを新規に書かない）。

FastAPI は import しない（core/ 規約）。
"""

from __future__ import annotations

import datetime as _datetime
import hashlib
import uuid

from pydantic import BaseModel, ConfigDict, Field

from core.candidate_flow import CandidateVocabulary

# ---------------------------------------------------------------------------
# 誤解メモの状態語彙（是正 F5）
# ---------------------------------------------------------------------------
#: AI（非LLM の訂正マーカー一致）が書ける唯一の状態。
MISCONCEPTION_STATUS_CANDIDATE = "candidate"
#: 本人が「そう、これは私の誤解だった」と引き受けた状態。
MISCONCEPTION_STATUS_CONFIRMED = "confirmed"
#: 本人が受け入れなかった状態（行は消さない, P4）。
MISCONCEPTION_STATUS_DISMISSED = "dismissed"
#: メッセージの書き直し・削除で置き換えられた履歴状態（将来の message_id 連携用）。
MISCONCEPTION_STATUS_SUPERSEDED = "superseded"

#: 候補 → 確定の共通制御フロー（``core.candidate_flow``）へ渡す語彙宣言。
MISCONCEPTION_VOCAB = CandidateVocabulary(
    candidate=MISCONCEPTION_STATUS_CANDIDATE,
    accepted=MISCONCEPTION_STATUS_CONFIRMED,
    dismissed=MISCONCEPTION_STATUS_DISMISSED,
    superseded=MISCONCEPTION_STATUS_SUPERSEDED,
)
MISCONCEPTION_STATUSES: tuple[str, ...] = MISCONCEPTION_VOCAB.statuses

#: エントリの出所（v1 は AI の訂正マーカー検出のみ）。
MISCONCEPTION_SOURCE_AI = "ai_detected"

#: 旧実装が「訂正文を抽出できなかった」ときに ``correct`` へ入れていた中身のない既定文。
#: 読み時に ``correct=None`` へ正規化し、「あなたは間違っていた」の判決文としては扱わない。
LEGACY_EMPTY_CORRECTION = "（AIの応答を参照してください）"


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


def _now_iso() -> str:
    """UTC の ISO8601（秒精度）。JSONB に入れるので文字列で持つ。"""
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")


def _legacy_entry_id(entry: dict) -> str:
    """``id`` を持たない旧エントリのための決定論 ID。

    旧形式（``{"label", "wrong", "correct"}`` の3キーのみ）には ID が無いため、位置
    （index）で参照すると新しい候補が先頭に積まれた瞬間にズレる。内容から決定論的に
    導出することで、読み直しても同じ行を指せるようにする。**まったく同一内容の行が
    複数あると同じ ID になる**（その場合は先頭の1行が対象になる）— 旧データにしか
    起きず、新規エントリは uuid を持つので発生しない。
    """
    seed = "\x1f".join(str(entry.get(key) or "") for key in ("label", "wrong", "correct"))
    return "legacy-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]


def misconception_entry_id(entry: dict) -> str:
    """エントリの安定 ID（無ければ内容から決定論導出する）。"""
    raw = str(entry.get("id") or "").strip()
    return raw or _legacy_entry_id(entry)


def normalize_misconception(entry: dict) -> dict:
    """1エントリを読み出し用の正規形へ整える（純関数・DB非依存）。

    - ``status`` が無い / 語彙外の旧行は **candidate として扱う**（旧行を「本人が
      確定した誤解」に昇格させない, 是正 F5）。
    - ``correct`` が空、または旧実装の中身のない既定文（
      :data:`LEGACY_EMPTY_CORRECTION`）なら ``None``。呼び出し側は「訂正文を抽出
      できなかった」事実文を出す（判決文を捏造しない）。
    """
    normalized = dict(entry)
    normalized["id"] = misconception_entry_id(entry)

    status = str(entry.get("status") or "").strip()
    normalized["status"] = (
        status if status in MISCONCEPTION_STATUSES else MISCONCEPTION_STATUS_CANDIDATE
    )
    normalized["source"] = str(entry.get("source") or "").strip() or MISCONCEPTION_SOURCE_AI
    normalized["label"] = str(entry.get("label") or "")
    normalized["wrong"] = str(entry.get("wrong") or "")

    correct_raw = entry.get("correct")
    correct = str(correct_raw).strip() if correct_raw is not None else ""
    normalized["correct"] = (
        None if (not correct or correct == LEGACY_EMPTY_CORRECTION) else correct
    )

    normalized["detected_at"] = str(entry.get("detected_at") or "") or None
    message_id = entry.get("message_id")
    normalized["message_id"] = str(message_id) if message_id else None
    normalized["decision"] = str(entry.get("decision") or "") or None
    normalized["reviewed_at"] = str(entry.get("reviewed_at") or "") or None
    return normalized


def new_misconception_entry(
    *,
    label: str,
    wrong: str,
    correct: str = "",
    message_id: str | None = None,
    detected_at: str | None = None,
    source: str = MISCONCEPTION_SOURCE_AI,
) -> dict:
    """新しい**候補**エントリを作る（``status`` は常に candidate, 原則1）。"""
    return normalize_misconception(
        {
            "id": uuid.uuid4().hex,
            "label": label,
            "wrong": wrong,
            "correct": correct,
            "status": MISCONCEPTION_STATUS_CANDIDATE,
            "source": source,
            "detected_at": detected_at or _now_iso(),
            "message_id": message_id,
        }
    )


def misconceptions_for_topic(data: PersonalGraphData, topic_id: str) -> list[dict]:
    """指定 topic_id の誤解一覧を正規形で返す（無ければ空。非 dict 要素は除外する）。"""
    entries = data.misconceptions_by_topic.get(topic_id) or []
    return [normalize_misconception(e) for e in entries if isinstance(e, dict)]


def normalized_misconceptions_by_topic(data: PersonalGraphData) -> dict[str, list[dict]]:
    """全 topic の誤解一覧を正規形で返す（``get_personal_layer`` の投影用）。"""
    return {
        topic_id: misconceptions_for_topic(data, topic_id)
        for topic_id in data.misconceptions_by_topic
    }


def find_misconception(
    data: PersonalGraphData, topic_id: str, entry_id: str,
) -> dict | None:
    """topic 内の1エントリを ID で探す（正規形。無ければ None）。"""
    for entry in misconceptions_for_topic(data, topic_id):
        if entry["id"] == entry_id:
            return entry
    return None


def append_misconception(data: PersonalGraphData, topic_id: str, entry: dict) -> PersonalGraphData:
    """topic_id の誤解一覧の先頭に entry を追加した新しいモデルを返す。

    純関数（``data`` 自体は変更しない）。呼び出し側は返り値を ``to_jsonb`` で JSONB 化して
    UPDATE すること。**件数上限は無い**（かつての「最新5件」上限は 2026-09-10 に撤廃した。
    6件目で古い行が黙って消えるのは原則3「情報を落とさない」に反する。表示件数を絞りたい
    読み出し側は、消すのではなく古い順に畳むこと）。
    """
    updated = data.model_copy(deep=True)
    current = list(updated.misconceptions_by_topic.get(topic_id) or [])
    updated.misconceptions_by_topic[topic_id] = [normalize_misconception(entry)] + current
    return updated


def set_misconception_status(
    data: PersonalGraphData,
    topic_id: str,
    entry_id: str,
    new_status: str,
    *,
    decision: str = "",
    reviewed_at: str | None = None,
) -> tuple[PersonalGraphData, str] | None:
    """1エントリの ``status`` を差し替えた新しいモデルと**旧 status** を返す。

    行は削除しない（却下も ``dismissed`` への遷移として保持する, P4）。旧形式の行を
    遷移させるときは、決定論導出した ID をその行へ焼き込む（以後 ID が内容に依存しなく
    なるので、本文を訂正しても参照が壊れない）。

    Returns:
        ``(updated, old_status)``。該当エントリが無ければ ``None``。

    Raises:
        ValueError: ``new_status`` が語彙外のとき。
    """
    if new_status not in MISCONCEPTION_STATUSES:
        raise ValueError(f"unknown misconception status: {new_status!r}")

    entries = data.misconceptions_by_topic.get(topic_id) or []
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict) or misconception_entry_id(raw) != entry_id:
            continue
        old_status = normalize_misconception(raw)["status"]
        updated = data.model_copy(deep=True)
        target = dict(updated.misconceptions_by_topic[topic_id][index])
        target["id"] = entry_id
        target["status"] = new_status
        if decision:
            target["decision"] = decision
        target["reviewed_at"] = reviewed_at or _now_iso()
        updated.misconceptions_by_topic[topic_id][index] = target
        return updated, old_status
    return None
