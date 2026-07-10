"""Admin Copilot（横断ユーティリティ層）のドメインモデル。

正本: docs/features/admin_assistant_design.md §4 / §6。

設計原則:
  - このモジュールは **FastAPI を import しない**（core のテスタビリティ確保、開発ルール2）。
  - `Capability` は画面横断で唯一の真実源（capabilities.py が集約する）。
  - `reversible=False` の capability は必ず `confirm=True`（P2。破壊的操作は確認ゲート）。
    不変条項は capabilities.py 構築時に構造的に検証する（validate_registry）。
  - `locate_steps` の `anchor_id` は**論理 ID**。バックエンドは DOM セレクタを持たない
    （P8。実 DOM 解決はフロントの registerUiAnchors に委ねる＝ domain/DOM 非依存）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# ロール（RBAC）
# ---------------------------------------------------------------------------

ROLE_STUDENT = "STUDENT"
ROLE_TEACHER = "TEACHER"
ROLE_SYSTEM_ADMIN = "SYSTEM_ADMIN"

# ロール階層: SYSTEM_ADMIN >= TEACHER >= STUDENT。
# required_role を満たすのは「同格以上」のロール。
_ROLE_RANK = {
    ROLE_STUDENT: 0,
    ROLE_TEACHER: 1,
    ROLE_SYSTEM_ADMIN: 2,
}


def role_rank(role: str) -> int:
    return _ROLE_RANK.get((role or "").upper(), -1)


def role_satisfies(required_role: str, actual_role: str) -> bool:
    """actual_role が required_role 以上か（P1 の fail-closed 判定に使う）。"""
    req = role_rank(required_role)
    act = role_rank(actual_role)
    if req < 0 or act < 0:
        return False
    return act >= req


# ---------------------------------------------------------------------------
# intent / kind 語彙
# ---------------------------------------------------------------------------

INTENT_GUIDANCE = "guidance"   # 説明（読み取り専用・DB非変更）
INTENT_LOCATE = "locate"       # 道案内（画面遷移＋点灯・DB非変更）
INTENT_ACTION = "action"       # 操作代行（変更を伴う）
INTENT_CLARIFY = "clarify"     # 聞き返し
INTENTS = (INTENT_GUIDANCE, INTENT_LOCATE, INTENT_ACTION, INTENT_CLARIFY)

KIND_GUIDANCE_ONLY = "guidance_only"
KIND_ACTION = "action"


# ---------------------------------------------------------------------------
# Capability（capabilities.py の登録要素）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LocateStep:
    """道案内（§5.3）の順序付きステップ。

    anchor_id は論理 ID。`{course_id}` 等の差し込みは実行時コンテキストで置換する。
    precondition が未達なら runLocatePlan はそのステップで停止し、画面の選択完了を待つ。
    """

    screen: str
    anchor_id: str
    hint: str
    precondition: str = ""

    def resolved(self, ctx: dict) -> "LocateStep":
        """`{key}` プレースホルダを ctx で差し込んだコピーを返す。"""
        return LocateStep(
            screen=_interpolate(self.screen, ctx),
            anchor_id=_interpolate(self.anchor_id, ctx),
            hint=self.hint,
            precondition=self.precondition,
        )

    def to_dict(self) -> dict:
        d = {"screen": self.screen, "anchor_id": self.anchor_id, "hint": self.hint}
        if self.precondition:
            d["precondition"] = self.precondition
        return d


@dataclass(frozen=True)
class Capability:
    """操作の宣言的カタログ要素（画面横断で単一の真実源）。"""

    id: str                        # 例 "lecture_studio.rewrite_chunk_script"
    screen: str                    # admin.js の data-tab に一致するタブ名
    title: str                     # 人間向けの操作名
    required_role: str             # ROLE_TEACHER | ROLE_SYSTEM_ADMIN
    kind: str                      # KIND_GUIDANCE_ONLY | KIND_ACTION
    howto_doc: str = ""            # 操作KB のアンカー（docs/admin_operations/... の見出し）
    description: str = ""          # 一行の説明
    scope: str = "any"             # "own_course" 等（既存 helper に委譲）
    reversible: bool = True        # action のとき取り消し可能か
    confirm: bool = False          # reversible=False は必ず True（P2）
    target_type: str = ""          # action の対象種別 'course'|'material'|'chunk'|...
    api: Optional[dict] = None     # action のとき参考情報（method/path）。実行は actions/ が担う
    revert: Optional[dict] = None  # reversible=True のときの取り消し方法メモ
    locate_steps: tuple = ()       # 道案内の点灯手順（LocateStep のタプル）

    # --- 派生・シリアライズ ---

    def is_action(self) -> bool:
        return self.kind == KIND_ACTION

    def locate_steps_as_dicts(self, ctx: Optional[dict] = None) -> list:
        ctx = ctx or {}
        return [s.resolved(ctx).to_dict() for s in self.locate_steps]

    def summary_dict(self) -> dict:
        """LLM プロンプトや guidance 応答に載せる安全なメタ（内部 api は含めない）。"""
        return {
            "id": self.id,
            "screen": self.screen,
            "title": self.title,
            "required_role": self.required_role,
            "kind": self.kind,
            "reversible": self.reversible,
            "confirm": self.confirm,
            "description": self.description,
        }


def _interpolate(template: str, ctx: dict) -> str:
    """`{key}` を ctx[key] で置換（欠損はそのまま残す＝捏造しない, P4）。"""
    if not template or "{" not in template:
        return template
    out = template
    for key, value in (ctx or {}).items():
        if value is None:
            continue
        out = out.replace("{" + str(key) + "}", str(value))
    return out


# ---------------------------------------------------------------------------
# LLM structured output（intent 分類）
# ---------------------------------------------------------------------------


class LLMIntentResponse(BaseModel):
    """intent.py の 1 LLM コールが返す構造化出力。

    LLM は capability を **提示された許可リストの中からのみ** 選ぶ。未知 / 権限外の
    capability_id を返しても、サーバ側 registry で再検証して fail-closed に落とす（P1）。
    """

    intent: str = Field(default=INTENT_CLARIFY, description="guidance | locate | action | clarify")
    capability_id: str = Field(default="", description="解決した capability id（無ければ空）")
    answer: str = Field(default="", description="ユーザーへの自然言語応答")
    needs_clarification: bool = Field(default=False)


@dataclass
class IntentResult:
    """route が受け取る intent 分類結果（LLM でもヒューリスティックでも同型）。"""

    intent: str
    answer: str
    capability_id: str = ""
    citations: list = field(default_factory=list)
    source: str = "llm"  # "llm" | "heuristic"
