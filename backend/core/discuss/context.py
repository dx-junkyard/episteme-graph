"""コース無し論文議論（document 直付け discuss）の会話コンテキスト・センチネル。

正本設計書: ``docs/features/corpus_roaming_design.md`` §5.1（Phase B）。
親層: ``docs/features/discussion_mode_design.md``（DM1〜DM8）。

``learning_chat_history`` / ``interest_traces`` はいずれも
``(user_id, course_id TEXT, topic_id TEXT)`` キーで **course_id に FK が無い**。
document 直付けの会話は新テーブル・新会話機構を発明せず、予約センチネル
``course_id = "_doc:{document_id}"`` + ``topic_id = "_discussion"``（既存の疑似トピック）
で既存の会話機構にそのまま載せる（migration 0）。

**このモジュールがセンチネル文字列の唯一の正本である。**
他所で ``"_doc:" + document_id`` のような組み立て・判定を書かないこと
（``test_document_discuss_guardrails.py`` が repo 全体を grep して固定する）。

- FastAPI を import しない（開発ルール2 / core/ 共通ルール）。
- LLM を呼ばない・DB を触らない（純粋な文字列の正規化のみ）。
"""

from __future__ import annotations

__all__ = [
    "DOCUMENT_CONTEXT_PREFIX",
    "document_context_id",
    "is_document_context",
    "parse_document_context",
]

# センチネルの接頭辞。実在コース id は UUID 由来（`learning_courses.id` は TEXT だが
# 生成は uuid4）のため、この接頭辞と衝突しない。
DOCUMENT_CONTEXT_PREFIX = "_doc:"


def document_context_id(document_id: str) -> str:
    """``document_id`` に対応する会話コンテキスト id（センチネル course_id）を返す。

    空・空白のみの ``document_id`` は ``ValueError``（呼び出し側の解決漏れを
    センチネルとして黙って通さない — fail-closed）。
    """
    doc_id = str(document_id or "").strip()
    if not doc_id:
        raise ValueError("document_id is required to build a document discuss context id")
    return f"{DOCUMENT_CONTEXT_PREFIX}{doc_id}"


def parse_document_context(course_id: str | None) -> str | None:
    """センチネル course_id から document_id を取り出す。センチネルでなければ ``None``。

    コース解決（``get_course_data`` / ``get_accessible_course_data``）へ流れ込ませない
    ための判定にも使う（設計書 §5.1）。
    """
    value = str(course_id or "")
    if not value.startswith(DOCUMENT_CONTEXT_PREFIX):
        return None
    doc_id = value[len(DOCUMENT_CONTEXT_PREFIX):].strip()
    return doc_id or None


def is_document_context(course_id: str | None) -> bool:
    """``course_id`` が document 直付けのセンチネルか。"""
    return parse_document_context(course_id) is not None
