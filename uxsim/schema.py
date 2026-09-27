"""uxsim の共通データ形（transcript / finding）。

runner（api / browser）・ペルソナ側 LLM・審判・レポートの全部がこの形を読む。
製品コード（backend/）を import しない。pydantic は製品の requirements にあるため使ってよい。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Friction = Literal["none", "confused", "misread", "blocked", "gave_up"]
SeverityLabel = Literal["blocked", "confused", "inconsistent", "principle"]
Oracle = Literal["A", "B", "C", "D", "E"]
FindingStatus = Literal["open", "filed", "duplicate", "not_reproduced", "wont_fix"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ThinkAloud(BaseModel):
    """ペルソナが 1 ステップごとに残す発話。点数は持たない。"""

    intent: str = ""
    expectation: str = ""
    reaction: str = ""
    friction: Friction = "none"
    gave_up_reason: str = ""


class HttpTrace(BaseModel):
    method: str
    path: str
    status: Optional[int] = None
    elapsed_ms: Optional[int] = None
    error: str = ""
    response_excerpt: str = ""  # 先頭 2000 字程度。数値の検査に使うため生のまま


class TranscriptStep(BaseModel):
    seq: int
    at: str = Field(default_factory=now_iso)
    persona_id: str
    session: int = 1
    scenario_id: str = ""
    action_id: str  # 行為レジストリの id。未登録なら "unsupported:<id>"
    args: dict[str, Any] = Field(default_factory=dict)
    think: ThinkAloud = Field(default_factory=ThinkAloud)
    screen: str = ""  # learning | admin
    affordance: str = ""  # data-ui-anchor か manual の {#anchor}
    http: list[HttpTrace] = Field(default_factory=list)
    observation: str = ""  # ペルソナに見せた画面の投影（DTO を整形した文）
    console_errors: list[str] = Field(default_factory=list)  # browser runner のみ
    screenshot: str = ""
    replay_divergence: bool = False


class Evidence(BaseModel):
    transcript_steps: list[int] = Field(default_factory=list)
    screenshots: list[str] = Field(default_factory=list)
    server_rows: dict[str, Any] = Field(default_factory=dict)
    quote: str = ""


class Finding(BaseModel):
    finding_id: str
    fingerprint: str
    oracle: Oracle
    severity_label: SeverityLabel
    reproducibility: Literal["reproduced_in_replay", "observed_once"] = "observed_once"
    persona_id: str = ""
    scenario_id: str = ""
    screen: str = ""
    affordance: str = ""
    hypothesis: str
    evidence: Evidence = Field(default_factory=Evidence)
    suspected_layer: list[str] = Field(default_factory=list)  # docs/issue_knowledge/layers.md の語彙
    status: FindingStatus = "open"
    status_ref: str = ""  # filed → IK-NNNN / duplicate → f-...
    run_id: str = ""
    campaign_id: str = ""


class RunMeta(BaseModel):
    run_id: str
    campaign_id: str
    domain: str
    snapshot: str
    git_commit: str = ""
    started_at: str = Field(default_factory=now_iso)
    finished_at: str = ""
    base_url: str = ""
    flags: dict[str, str] = Field(default_factory=dict)
    budget: dict[str, int] = Field(default_factory=dict)
    spent: dict[str, int] = Field(default_factory=dict)  # product_llm_calls / persona_llm_calls / http_429
    replay_of: str = ""
    notes: list[str] = Field(default_factory=list)  # 事実文（snapshot の版ずれ・未実施の検査など）
