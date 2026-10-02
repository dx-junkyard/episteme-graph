"""検証シミュレーション層（Simulation Bridge, SB層）— 語彙・定数・事実文・契約の正本。

設計: ``docs/features/simulation_bridge_design.md``（SB1〜SB10・§4 データモデル・§5 規則）。

このモジュールは FastAPI / sqlalchemy / LLM / numpy を import しない（純データ + 純関数）。
各部品モジュールの**契約**（関数名・引数・戻り値の形）をここに宣言し、実装は以下に分ける:

- ``ast_guard.py``   : ``check_program(source) -> AstCheckResult`` / ``check_expression(expr) -> AstCheckResult``
                        LLM 生成コードと部品の ``transfer`` 式の許可リスト検査（SB3）。
- ``sandbox.py``     : ``SandboxClient(base_url, timeout_s, transport=None).run(source, *, entrypoint,
                        inputs, params, seed) -> SandboxResult``。HTTP は ``transport`` 注入で差し替え可。
- ``codegen.py``     : ``generate_program(module_dto, *, equations, symbols, llm=..., model=None)
                        -> ProgramDraft``（LLM 1 コール + 同一コール内修復 1 回・ast_guard 通過を要求）。
- ``instrument.py``  : ``validate_part_body(entry_type, body) -> list[str]`` /
                        ``validate_wiring(parts, edges, catalog) -> list[str]`` /
                        ``compose_instrument(parts, edges, catalog) -> str``（決定論・配線 → Python ソース）。
- ``comparator.py``  : ``compare(theory_outputs, observed, *, reference=None) -> Comparison``（決定論）。
- ``revision.py``    : ``propose_revision(wiring, comparison, catalog, *, llm=..., model=None)
                        -> RevisionProposal``（LLM 1 コール・validator が目録外を drop）。
- ``store.py``       : programs / wirings / runs の CRUD（状態遷移のみ・DELETE 無し）と台帳候補の追記・確定。

**数値の扱い（SB6）**: ``Comparison.metrics`` は教員向け DTO にそのまま出す（カタログ登録済み計器
``simulation.agreement``）。confidence は本層のどの DTO にも載せない。学習者向け経路は無い。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 版・識別子
# ---------------------------------------------------------------------------

#: 台帳候補の ``detector_version``（反証条件候補の ``doubt-layer/v1`` と同じ役割）。
DETECTOR_VERSION = "simulation-bridge/v1"

#: U層 feature キー（``core/llm_usage/schema.py::KNOWN_FEATURES`` に登録する 2 件）。
FEATURE_CODEGEN = "simulation:codegen"
FEATURE_REVISION = "simulation:revision"

#: M層 scene キー（``core/llm_policy.py``。``simulation:*`` はこの scene に束ねる）。
SCENE_SIMULATION = "simulation"

#: 監査 entity_type（``core/schema.py::AUDIT_ENTITY_SIMULATION`` と同値。循環 import を避けて文字列を複製しない —
#: 実際の記帳では ``core.schema.AUDIT_ENTITY_SIMULATION`` を import すること）。
AUDIT_ACTIONS = (
    "program_generated",
    "program_confirmed",
    "program_dismissed",
    "program_superseded",
    "wiring_created",
    "wiring_proposed",
    "wiring_confirmed",
    "wiring_dismissed",
    "run_recorded",
    "ledger_candidate_added",
    "ledger_candidate_confirmed",
    "ledger_candidate_dismissed",
)

# ---------------------------------------------------------------------------
# 語彙（DB CHECK と一致させる。migration 088 が正本の DDL）
# ---------------------------------------------------------------------------

PROGRAM_LANGUAGE = "python"
PROGRAM_ENTRYPOINT = "simulate"
INSTRUMENT_ENTRYPOINT = "simulate_instrument"

PROGRAM_STATUS_CANDIDATE = "candidate"
PROGRAM_STATUS_CONFIRMED = "confirmed"
PROGRAM_STATUS_DISMISSED = "dismissed"
PROGRAM_STATUS_SUPERSEDED = "superseded"
PROGRAM_STATUSES: tuple[str, ...] = (
    PROGRAM_STATUS_CANDIDATE,
    PROGRAM_STATUS_CONFIRMED,
    PROGRAM_STATUS_DISMISSED,
    PROGRAM_STATUS_SUPERSEDED,
)

WIRING_ORIGIN_TEACHER = "teacher"
WIRING_ORIGIN_LLM = "llm_candidate"
WIRING_ORIGINS: tuple[str, ...] = (WIRING_ORIGIN_TEACHER, WIRING_ORIGIN_LLM)

WIRING_STATUS_CANDIDATE = "candidate"
WIRING_STATUS_CONFIRMED = "confirmed"
WIRING_STATUS_DISMISSED = "dismissed"
WIRING_STATUSES: tuple[str, ...] = (WIRING_STATUS_CANDIDATE, WIRING_STATUS_CONFIRMED, WIRING_STATUS_DISMISSED)

RUN_STATUS_SUCCEEDED = "succeeded"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_REJECTED = "rejected"  # AST 検査で実行前に止めた
RUN_STATUSES: tuple[str, ...] = (RUN_STATUS_SUCCEEDED, RUN_STATUS_FAILED, RUN_STATUS_REJECTED)

LEDGER_CANDIDATE_STATUS_CANDIDATE = "candidate"
LEDGER_CANDIDATE_STATUS_CONFIRMED = "confirmed"
LEDGER_CANDIDATE_STATUS_DISMISSED = "dismissed"

#: 台帳の ``verification_status`` をこの値より上に上げない（SB1）。
LEDGER_MAX_STATUS = "indirectly_supported"
#: 確定時に上げてよい元の status（これ以外は触らない）。
LEDGER_RAISABLE_FROM: tuple[str, ...] = ("untested", "unknown")

# ---------------------------------------------------------------------------
# L層 部品（entry_type は core/schema.py::LIBRARY_ENTRY_TYPES に追加する 3 件）
# ---------------------------------------------------------------------------

PART_TYPE_INSTRUMENT = "instrument_model"
PART_TYPE_NOISE = "noise_source"
PART_TYPE_PROCESSING = "signal_processing"
PART_TYPES: tuple[str, ...] = (PART_TYPE_INSTRUMENT, PART_TYPE_NOISE, PART_TYPE_PROCESSING)

#: 日本語ラベル（``core/library/schema.py::ENTRY_TYPE_LABELS`` と migration 088 のシードに同じ値を書く）。
PART_TYPE_LABELS: dict[str, str] = {
    PART_TYPE_INSTRUMENT: "装置モデル",
    PART_TYPE_NOISE: "ノイズ源",
    PART_TYPE_PROCESSING: "信号処理の工夫",
}

NOISE_KIND_GAUSSIAN = "gaussian"
NOISE_KIND_POISSON = "poisson"
NOISE_KIND_ONE_OVER_F = "one_over_f"
NOISE_KIND_DRIFT = "drift"
NOISE_KIND_OFFSET = "offset"
NOISE_KINDS: tuple[str, ...] = (
    NOISE_KIND_GAUSSIAN,
    NOISE_KIND_POISSON,
    NOISE_KIND_ONE_OVER_F,
    NOISE_KIND_DRIFT,
    NOISE_KIND_OFFSET,
)

#: ``library_entries.body`` の規約（§4.1）。L層 store は検査しない。配線時に ``instrument.validate_part_body`` が見る。
PART_BODY_KEYS: dict[str, tuple[str, ...]] = {
    PART_TYPE_INSTRUMENT: ("transfer", "params", "inputs", "outputs"),
    PART_TYPE_NOISE: ("noise_kind", "params", "depends_on"),
    PART_TYPE_PROCESSING: ("transfer", "params", "removes"),
}
#: 無いと配線できない必須キー。
PART_BODY_REQUIRED: dict[str, tuple[str, ...]] = {
    PART_TYPE_INSTRUMENT: ("transfer",),
    PART_TYPE_NOISE: ("noise_kind",),
    PART_TYPE_PROCESSING: ("transfer",),
}

#: 部品 ``params`` の 1 項目のキー（``{name: {default, unit, min, max}}``）。
PART_PARAM_KEYS: tuple[str, ...] = ("default", "unit", "min", "max")

# ---------------------------------------------------------------------------
# AST 許可リスト（SB3・ast_guard の正本定数）
# ---------------------------------------------------------------------------

#: 生成コード・部品式で import してよいモジュール。
ALLOWED_IMPORTS: frozenset[str] = frozenset({"math", "numpy", "cmath"})
#: 使ってはならない名前（呼び出し・属性・識別子のいずれでも）。
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "input",
        "breakpoint",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "memoryview",
        "exit",
        "quit",
    }
)
#: 属性アクセスで禁止する接頭辞 / 名前（``__class__`` 系のサンドボックス脱出・os 系）。
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset(
    {"__class__", "__subclasses__", "__globals__", "__builtins__", "__code__", "__mro__", "__bases__", "__dict__", "__loader__", "__spec__", "system", "popen", "fromfile", "tofile", "load", "save", "savetxt", "loadtxt", "genfromtxt"}
)
#: 部品の ``transfer`` 式が参照してよい名前（式モード）。
EXPRESSION_ALLOWED_NAMES: frozenset[str] = frozenset({"x", "params", "np", "math"})
#: 生成コードの行数上限（超えたら候補のまま rejected）。
PROGRAM_MAX_LINES = 400

# ---------------------------------------------------------------------------
# 比較器（SB4）— 閾値は label_vocab の GradedScale が正本。ここは観点の名前だけ。
# ---------------------------------------------------------------------------

METRIC_RELATIVE_DIFFERENCE = "relative_difference"
METRIC_SEPARATION_RATIO = "separation_ratio"
METRIC_REFERENCE_DIFFERENCE = "reference_relative_difference"

AGREEMENT_LEVEL_MATCH = "match"
AGREEMENT_LEVEL_ROUGH = "rough"
AGREEMENT_LEVEL_DIVERGE = "diverge"
AGREEMENT_LEVEL_UNMEASURED = "unmeasured"
AGREEMENT_LEVELS: tuple[str, ...] = (
    AGREEMENT_LEVEL_MATCH,
    AGREEMENT_LEVEL_ROUGH,
    AGREEMENT_LEVEL_DIVERGE,
    AGREEMENT_LEVEL_UNMEASURED,
)

SEPARATION_LEVEL_CLEAR = "clear"
SEPARATION_LEVEL_BORDER = "border"
SEPARATION_LEVEL_BURIED = "buried"
SEPARATION_LEVEL_UNMEASURED = "unmeasured"
SEPARATION_LEVELS: tuple[str, ...] = (
    SEPARATION_LEVEL_CLEAR,
    SEPARATION_LEVEL_BORDER,
    SEPARATION_LEVEL_BURIED,
    SEPARATION_LEVEL_UNMEASURED,
)

# ---------------------------------------------------------------------------
# 事実文（閉世界・数値なし・SB1）
# ---------------------------------------------------------------------------

FACT_CLOSED_WORLD_PREFIX = "このモデルと配線のもとでは、"
FACT_NOT_VERIFICATION = "シミュレーションの一致は検証ではなく、分析の妥当性の候補にとどまります。"
FACT_RECONSTRUCTED_INPUTS = "入力の式に PDF から復元された式が含まれています。元の式と一致するかは確認されていません。"
FACT_SANDBOX_UNREACHABLE = "実行環境に到達できません。管理者が sim-runner を起動しているか確認してください。"
FACT_SANDBOX_NOT_CONFIGURED = "実行環境が構成されていません（SIM_RUNNER_URL が空です）。"
FACT_AST_REJECTED = "生成されたコードに許可されていない操作が含まれていたため、実行せずに止めました。"
FACT_NO_MODULE_EQUATIONS = "この理論モジュールには式の本文が引けないため、コードを生成できません。"
FACT_NO_PROPOSAL = "目録にある部品の組み替えでは改訂案を作れませんでした。"
FACT_NO_PARTS_CATALOG = "凍結された部品がまだありません。ナレッジライブラリに部品を登録して凍結してください。"
FACT_NO_WIRING = "配線なしで実行したため、模擬観測は理論の出力そのものです。"
FACT_REFERENCE_MISSING = "論文の記載値は入力されていません。"

#: 一致の段階ラベル（表示は label_vocab の表が正本。ここは level キーの意味の説明用）。
AGREEMENT_FACTS: dict[str, str] = {
    AGREEMENT_LEVEL_MATCH: "理論の出力と模擬観測は一致しています。",
    AGREEMENT_LEVEL_ROUGH: "理論の出力と模擬観測はおおむね一致しています。",
    AGREEMENT_LEVEL_DIVERGE: "理論の出力と模擬観測は乖離しています。",
    AGREEMENT_LEVEL_UNMEASURED: "一致の度合いは測れませんでした。",
}
SEPARATION_FACTS: dict[str, str] = {
    SEPARATION_LEVEL_CLEAR: "期待される効果は模擬観測のばらつきから分離できます。",
    SEPARATION_LEVEL_BORDER: "期待される効果は模擬観測のばらつきと同程度です。",
    SEPARATION_LEVEL_BURIED: "期待される効果は模擬観測のばらつきに埋もれています。",
    SEPARATION_LEVEL_UNMEASURED: "分離の度合いは測れませんでした。",
}

# ---------------------------------------------------------------------------
# DTO に載せてはならないキー（ガードレールが再帰走査）
# ---------------------------------------------------------------------------

FORBIDDEN_DTO_KEYS: frozenset[str] = frozenset({"confidence", "score", "weight", "structure_fingerprint", "candidate_score"})

# ---------------------------------------------------------------------------
# 契約のデータ型（各モジュールが返す形。純 dataclass）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AstCheckResult:
    ok: bool
    reasons: tuple[str, ...] = ()
    entrypoint_found: bool = False


@dataclass
class SandboxResult:
    status: str  # RUN_STATUS_*
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    facts: list[str] = field(default_factory=list)


@dataclass
class ProgramDraft:
    source_code: str
    io_contract: dict[str, Any]
    provenance: dict[str, Any]
    facts: list[str] = field(default_factory=list)
    ast_check: AstCheckResult | None = None

    @property
    def accepted(self) -> bool:
        return bool(self.ast_check and self.ast_check.ok and self.ast_check.entrypoint_found)


@dataclass
class Comparison:
    agreement_level: str
    separation_level: str
    facts: list[str]
    metrics: dict[str, float | None]
    reference_level: str = AGREEMENT_LEVEL_UNMEASURED

    def to_dict(self) -> dict[str, Any]:
        return {
            "agreement_level": self.agreement_level,
            "separation_level": self.separation_level,
            "reference_level": self.reference_level,
            "facts": list(self.facts),
            "metrics": dict(self.metrics),
        }


@dataclass
class RevisionChange:
    op: str  # add | remove | set_param
    slot: str
    entry_id: str = ""
    param: str = ""
    value: Any = None
    reason: str = ""


@dataclass
class RevisionProposal:
    changes: list[RevisionChange]
    dropped: list[str]  # 目録外などで落とした理由（事実文）
    facts: list[str]
    parts: list[dict] = field(default_factory=list)  # 適用後の parts（新 wiring 行の材料）
    edges: list[dict] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes


REVISION_OPS: tuple[str, ...] = ("add", "remove", "set_param")


def is_valid_program_status(value: str) -> bool:
    return value in PROGRAM_STATUSES


def is_part_type(value: str) -> bool:
    return value in PART_TYPES


def closed_world(sentence: str, *, wiring_name: str = "") -> str:
    """事実文を閉世界の前置き付きにする（SB1）。配線名があれば名指しする。"""
    text = (sentence or "").strip()
    if not text:
        return ""
    if wiring_name:
        return f"このモデルと配線『{wiring_name}』のもとでは、{text}"
    return FACT_CLOSED_WORLD_PREFIX + text


__all__ = [name for name in globals() if not name.startswith("_")]
