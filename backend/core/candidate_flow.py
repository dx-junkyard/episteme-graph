"""candidate → 人間確定 ワークフローの共通プリミティブ（提案 §2-1）。

「非LLM prefilter → 非同期 LLM 候補 → 人間 confirm / dismiss → 状態遷移で保持・
監査記帳」という同型パイプラインが、少なくとも8系統（tension / structure_anchor /
D層 scope_candidates / assumption_nodes / W層 element_annotations /
C層 explanations / ランドスケープ placements / カテゴリギャップ decisions）で
個別に再実装されている。LLM 呼び出し側は ``core/llm_worker/`` に共通化済みで、
本モジュールは残りの**確定側の制御フロー**だけを引き受ける。

:mod:`core.revision_store` と同じ流儀:

- 共有するのは **制御フロー**（遷移の可否判定 → apply → 監査）だけ。
- status 語彙・粒度・トリガ・SQL・テーブル・DTO キー名はドメイン側に残す。
  :class:`CandidateVocabulary` は「その系統の4語彙は何か」を宣言するだけで、
  語彙そのものを決め打ちしない（``accepted`` は系統によって ``confirmed`` /
  ``accepted`` / ``committed`` / ``teacher_approved`` などと異なる）。
- **DB へは一切触らない**（sqlalchemy を import しない純 Python）。書き込みは
  ドメインが注入する callable が行う。FastAPI / LLM も import しない。

**不変条項**（各層の設計書から継承する。ここで構造的に守る）:

- **P4 情報を落とさない**: 行削除アクションを提供しない。却下は ``dismissed``
  への状態遷移で保持し、``restore`` で候補へ戻せる。本モジュールに delete /
  purge に相当する API は無く、今後も追加しない。
- **KN-3 確定は人間**: ``confirm`` / ``dismiss`` / ``restore`` は ``actor_id``
  を必須とする（空文字・None は :class:`CandidateTransitionError`）。LLM /
  worker が人間の確定状態へ遷移させる経路を作らない。
- **監査必須**: 人間の3アクション（confirm / dismiss / restore）は必ず
  ``record_audit`` を伴う（:class:`CandidateFlow` が apply と監査を一体で呼ぶ）。
  監査 callable の例外は握らない。再生成による supersede だけは例外で、既定では
  記帳しない（既存系統は候補入れ替えを detect 側で記帳する慣行のため。
  ``audit_supersede=True`` で1行ずつ記帳に切り替え可能）。
- **再生成は候補のみ supersede（LS3 セマンティクス）**:
  :func:`select_supersedable` は ``candidate`` 状態の行だけを返す。人間が確定した
  ``accepted`` / ``dismissed`` は再解析で置換・復活させられない。

**非スコープ**: 既存8系統の巻き取り（各層のコードは変更しない）/ SQL 生成 /
LLM ワーカー基盤（``core/llm_worker/``）/ 段階ラベル変換（提案 §2-2）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# アクション語彙
# ---------------------------------------------------------------------------

#: 人間が候補に対して行える3アクション。ここに削除は無い（P4）。
ACTION_CONFIRM = "confirm"
ACTION_DISMISS = "dismiss"
ACTION_RESTORE = "restore"
ACTIONS: tuple[str, ...] = (ACTION_CONFIRM, ACTION_DISMISS, ACTION_RESTORE)

#: 再生成（再解析）による候補の置換。人間のアクションではないので ACTIONS に入れない。
ACTION_SUPERSEDE = "supersede"


class CandidateFlowConfigError(ValueError):
    """語彙・構成の誤り（:class:`CandidateVocabulary` / :class:`CandidateFlow` の
    構築時、および ``superseded`` 未定義での supersede 要求時に送出する）。"""


class CandidateTransitionError(ValueError):
    """許されない状態遷移、または必須引数（actor_id / 却下理由）の欠落。"""


# ---------------------------------------------------------------------------
# 1. 状態語彙
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateVocabulary:
    """1系統の状態語彙の宣言。

    Args:
        candidate: AI / prefilter が書ける唯一の状態。
        accepted: 人間が確定した状態（``confirmed`` / ``accepted`` /
            ``committed`` / ``teacher_approved`` 等、系統ごとに異なる）。
        dismissed: 人間が却下した状態（行削除の代わり, P4）。
        superseded: 再生成で置換された履歴状態。この概念を持たない系統では
            None（その場合 :func:`select_supersedable` は
            :class:`CandidateFlowConfigError`）。

    4語彙は空文字不可・互いに重複不可（構築時に検証する）。
    """

    candidate: str
    accepted: str
    dismissed: str
    superseded: str | None = None

    def __post_init__(self) -> None:
        named = [
            ("candidate", self.candidate),
            ("accepted", self.accepted),
            ("dismissed", self.dismissed),
        ]
        if self.superseded is not None:
            named.append(("superseded", self.superseded))

        for field_name, value in named:
            if not isinstance(value, str) or not value.strip():
                raise CandidateFlowConfigError(
                    f"{field_name} must be a non-empty status string (got {value!r})"
                )
            if value != value.strip():
                raise CandidateFlowConfigError(
                    f"{field_name} must not have surrounding whitespace (got {value!r})"
                )

        values = [value for _, value in named]
        if len(set(values)) != len(values):
            raise CandidateFlowConfigError(
                f"status vocabulary must be distinct (got {values!r})"
            )

    @property
    def statuses(self) -> tuple[str, ...]:
        """この系統が扱う全状態（``superseded`` 未定義なら3語彙）。"""
        if self.superseded is None:
            return (self.candidate, self.accepted, self.dismissed)
        return (self.candidate, self.accepted, self.dismissed, self.superseded)

    @property
    def human_decided(self) -> tuple[str, ...]:
        """人間が確定させた状態（再生成で触ってはいけない集合, LS3）。"""
        return (self.accepted, self.dismissed)

    def is_candidate(self, status: str) -> bool:
        """未確定の候補状態か。"""
        return status == self.candidate


# ---------------------------------------------------------------------------
# 2. 遷移解決
# ---------------------------------------------------------------------------


def resolve_transition(
    current_status: str,
    action: str,
    *,
    vocab: CandidateVocabulary,
    actor_id: str | None,
    reason: str = "",
    require_dismiss_reason: bool = True,
) -> str:
    """現在状態 + アクションから新しい状態を解決する（DB には触らない）。

    許される遷移はこれだけである:

    - ``confirm``: ``candidate`` → ``accepted``
    - ``dismiss``: ``candidate`` → ``dismissed``
    - ``restore``: ``dismissed`` → ``candidate``

    ほかは全て :class:`CandidateTransitionError`（未知のアクション、語彙外の
    現在状態、``accepted`` からの再確定、``superseded`` の復活、
    ``candidate`` の restore などを含む）。

    Args:
        actor_id: 遷移させた人間の識別子。空文字・空白のみ・None は拒否する
            （KN-3: 確定は人間。機械が確定状態へ遷移させる経路を作らない）。
        reason: 却下理由。``require_dismiss_reason`` が真（既定）のとき
            ``dismiss`` では非空を要求する。理由必須を緩めている系統
            （例: 学習者自身の dismiss）は ``False`` を渡す。

    Returns:
        新しい状態文字列。
    """
    if action not in ACTIONS:
        raise CandidateTransitionError(
            f"unknown action: {action!r} (must be one of {ACTIONS!r})"
        )
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise CandidateTransitionError(
            f"actor_id is required for action {action!r} (human confirmation only)"
        )
    if current_status not in vocab.statuses:
        raise CandidateTransitionError(
            f"unknown current status: {current_status!r} "
            f"(must be one of {vocab.statuses!r})"
        )

    if action == ACTION_RESTORE:
        if current_status != vocab.dismissed:
            raise CandidateTransitionError(
                f"restore is only allowed from {vocab.dismissed!r} "
                f"(current: {current_status!r})"
            )
        return vocab.candidate

    if current_status != vocab.candidate:
        raise CandidateTransitionError(
            f"{action} is only allowed from {vocab.candidate!r} "
            f"(current: {current_status!r})"
        )

    if action == ACTION_CONFIRM:
        return vocab.accepted

    if require_dismiss_reason and not str(reason or "").strip():
        raise CandidateTransitionError("dismiss requires a reason")
    return vocab.dismissed


# ---------------------------------------------------------------------------
# 3. 再生成時の supersede 対象抽出
# ---------------------------------------------------------------------------


def _field_of(row: Any, key: str) -> str:
    """行から1フィールドを取り出す（Mapping なら添字、それ以外は属性）。"""
    if isinstance(row, Mapping):
        value = row.get(key)
    else:
        value = getattr(row, key, None)
    return str(value) if value is not None else ""


def select_supersedable(
    rows: Iterable[Any],
    *,
    vocab: CandidateVocabulary,
    status_key: str = "status",
    status_of: Callable[[Any], str] | None = None,
) -> list[Any]:
    """再解析で置換してよい行（``candidate`` のみ）を返す。

    人間が確定した ``accepted`` / ``dismissed`` は**絶対に返さない**
    （LS3: AI は人間の判断を上書き・復活させられない）。既に ``superseded``
    の履歴行、および語彙外の未知の状態も返さない（fail-closed: 意味の分からない
    行を機械的に置換しない）。

    Args:
        status_of: 行から状態を取り出す関数。未指定なら Mapping は
            ``row[status_key]``、それ以外は ``getattr(row, status_key)``。

    Raises:
        CandidateFlowConfigError: ``vocab.superseded`` が未定義のとき
            （supersede の概念を持たない系統で呼ぶのは構成の誤り）。
    """
    if vocab.superseded is None:
        raise CandidateFlowConfigError(
            "vocabulary has no 'superseded' status; supersede is not available"
        )

    getter = status_of if status_of is not None else (lambda r: _field_of(r, status_key))
    return [row for row in rows if getter(row) == vocab.candidate]


# ---------------------------------------------------------------------------
# 4. 検証 → apply → 監査 の一本化
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateFlow:
    """1系統の候補確定フロー（検証 → apply → 監査）。

    Args:
        vocab: この系統の状態語彙。
        audit_entity_type: ``theory_review_events.entity_type``。値は
            ``core/schema.py`` の ``AUDIT_ENTITY_*`` 定数を使うこと（本モジュールは
            カタログを参照しない — core/schema への依存を作らないため）。空文字は
            構築時に :class:`CandidateFlowConfigError`。
        apply_status: 実際の書き込み。次のキーワード引数で呼ばれる:
            ``entity_id`` / ``old_status`` / ``new_status`` / ``actor_id`` /
            ``reason`` / ``metadata``。戻り値は結果 dict の ``applied`` に載る
            （None を返してもよい）。
        record_audit: 監査記帳。次のキーワード引数で呼ばれる:
            ``entity_type`` / ``entity_id`` / ``action`` / ``old_status`` /
            ``new_status`` / ``actor_id`` / ``reason`` / ``metadata``。
            例外は握らない（監査必須）。
        require_dismiss_reason: ``dismiss`` に理由を要求するか（既定 True）。
        audit_supersede: 再生成による supersede も1行ずつ監査記帳するか
            （既定 False。多くの系統は「候補の入れ替え」を detect 側で記帳する）。

    ``apply_status`` の後に ``record_audit`` を呼ぶ（書き込めていない遷移を
    監査に載せない）。両者のトランザクション境界は呼び出し側の責務。
    """

    vocab: CandidateVocabulary
    audit_entity_type: str
    apply_status: Callable[..., Any]
    record_audit: Callable[..., Any]
    require_dismiss_reason: bool = True
    audit_supersede: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.audit_entity_type, str) or not self.audit_entity_type.strip():
            raise CandidateFlowConfigError("audit_entity_type must be a non-empty string")
        for name in ("apply_status", "record_audit"):
            if not callable(getattr(self, name)):
                raise CandidateFlowConfigError(f"{name} must be callable")

    # -- 人間のアクション ---------------------------------------------------

    def confirm(
        self,
        entity_id: str,
        *,
        current_status: str,
        actor_id: str,
        reason: str = "",
        metadata: dict | None = None,
    ) -> dict:
        """候補を確定させる（``candidate`` → ``accepted``）。"""
        return self._transition(
            entity_id,
            ACTION_CONFIRM,
            current_status=current_status,
            actor_id=actor_id,
            reason=reason,
            metadata=metadata,
        )

    def dismiss(
        self,
        entity_id: str,
        *,
        current_status: str,
        actor_id: str,
        reason: str = "",
        metadata: dict | None = None,
    ) -> dict:
        """候補を却下する（``candidate`` → ``dismissed``。行は削除しない, P4）。"""
        return self._transition(
            entity_id,
            ACTION_DISMISS,
            current_status=current_status,
            actor_id=actor_id,
            reason=reason,
            metadata=metadata,
        )

    def restore(
        self,
        entity_id: str,
        *,
        current_status: str,
        actor_id: str,
        reason: str = "",
        metadata: dict | None = None,
    ) -> dict:
        """却下を取り消して候補へ戻す（``dismissed`` → ``candidate``）。"""
        return self._transition(
            entity_id,
            ACTION_RESTORE,
            current_status=current_status,
            actor_id=actor_id,
            reason=reason,
            metadata=metadata,
        )

    # -- 再生成 -------------------------------------------------------------

    def supersede_candidates(
        self,
        rows: Iterable[Any],
        *,
        actor_id: str | None = None,
        reason: str = "",
        metadata: dict | None = None,
        status_key: str = "status",
        id_key: str = "id",
        status_of: Callable[[Any], str] | None = None,
        id_of: Callable[[Any], str] | None = None,
    ) -> dict:
        """再解析で候補のみを ``superseded`` へ倒す（人間確定行は触らない, LS3）。

        対象が1件も無ければ ``apply_status`` / ``record_audit`` を**一度も呼ばない**
        （空集合で SQL を発行しない慣行）。``actor_id`` は任意（worker 実行のため）。

        Returns:
            ``{"action": "supersede", "new_status": ..., "entity_ids": [...],
            "count": int, "applied": [...]}``。
        """
        targets = select_supersedable(
            rows, vocab=self.vocab, status_key=status_key, status_of=status_of
        )
        new_status = self.vocab.superseded
        result: dict = {
            "action": ACTION_SUPERSEDE,
            "new_status": new_status,
            "entity_ids": [],
            "count": 0,
            "applied": [],
        }
        if not targets:
            return result

        id_getter = id_of if id_of is not None else (lambda r: _field_of(r, id_key))
        payload = dict(metadata or {})
        for row in targets:
            entity_id = id_getter(row)
            applied = self.apply_status(
                entity_id=entity_id,
                old_status=self.vocab.candidate,
                new_status=new_status,
                actor_id=actor_id,
                reason=reason,
                metadata=payload,
            )
            result["entity_ids"].append(entity_id)
            result["applied"].append(applied)
            if self.audit_supersede:
                self.record_audit(
                    entity_type=self.audit_entity_type,
                    entity_id=entity_id,
                    action=ACTION_SUPERSEDE,
                    old_status=self.vocab.candidate,
                    new_status=new_status,
                    actor_id=actor_id,
                    reason=reason,
                    metadata=payload,
                )

        result["count"] = len(result["entity_ids"])
        return result

    # -- 内部 ---------------------------------------------------------------

    def _transition(
        self,
        entity_id: str,
        action: str,
        *,
        current_status: str,
        actor_id: str,
        reason: str,
        metadata: dict | None,
    ) -> dict:
        new_status = resolve_transition(
            current_status,
            action,
            vocab=self.vocab,
            actor_id=actor_id,
            reason=reason,
            require_dismiss_reason=self.require_dismiss_reason,
        )
        payload = dict(metadata or {})
        applied = self.apply_status(
            entity_id=entity_id,
            old_status=current_status,
            new_status=new_status,
            actor_id=actor_id,
            reason=reason,
            metadata=payload,
        )
        self.record_audit(
            entity_type=self.audit_entity_type,
            entity_id=entity_id,
            action=action,
            old_status=current_status,
            new_status=new_status,
            actor_id=actor_id,
            reason=reason,
            metadata=payload,
        )
        return {
            "entity_id": entity_id,
            "action": action,
            "old_status": current_status,
            "new_status": new_status,
            "applied": applied,
        }


__all__: Sequence[str] = (
    "ACTIONS",
    "ACTION_CONFIRM",
    "ACTION_DISMISS",
    "ACTION_RESTORE",
    "ACTION_SUPERSEDE",
    "CandidateFlow",
    "CandidateFlowConfigError",
    "CandidateTransitionError",
    "CandidateVocabulary",
    "resolve_transition",
    "select_supersedable",
)
