"""uxsim の設定（環境変数と ``uxsim/sandbox/.env.uxsim``）。

優先順位は「プロセスの環境変数 > .env.uxsim > 既定値」。dotenv には依存しない。
本番・開発共用 DB の URL を既定値に持たない（PE1）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

UXSIM_ROOT = Path(__file__).resolve().parent
REPO_ROOT = UXSIM_ROOT.parent
DEFAULT_ENV_FILE = UXSIM_ROOT / "sandbox" / ".env.uxsim"

PROVIDERS = ("openai", "anthropic", "replay", "scripted", "mailbox", "claude_cli")


def parse_env_file(path: Path) -> dict[str, str]:
    """``KEY=VALUE`` 形式の簡易パーサ。``#`` 行・空行・``export`` 接頭辞・引用符を扱う。"""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    """uxsim の実行設定。"""

    base_url: str = "http://localhost:3000"
    admin_username: str = "Administrator"
    admin_password: str = ""
    persona_password: str = "uxsim-persona-pass"
    persona_llm_provider: str = "anthropic"
    persona_llm_model: str = ""
    persona_llm_api_key: str = ""
    runs_dir: Path = field(default_factory=lambda: UXSIM_ROOT / "runs")
    sandbox_database_url: str = ""
    http_timeout_s: float = 130.0

    @property
    def has_sandbox_db(self) -> bool:
        return bool(self.sandbox_database_url)


def get_settings(env: Optional[Mapping[str, str]] = None, env_file: Optional[Path] = None) -> Settings:
    """環境変数（と .env.uxsim）から ``Settings`` を組み立てる。"""
    merged: dict[str, str] = dict(parse_env_file(env_file or DEFAULT_ENV_FILE))
    merged.update(dict(os.environ if env is None else env))

    def get(key: str, default: str = "") -> str:
        return merged.get(key, default) or default

    provider = get("UXSIM_PERSONA_LLM_PROVIDER", "anthropic").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"UXSIM_PERSONA_LLM_PROVIDER は {PROVIDERS} のいずれか: {provider!r}")
    runs = Path(get("UXSIM_RUNS_DIR", str(UXSIM_ROOT / "runs")))
    if not runs.is_absolute():
        runs = (REPO_ROOT / runs) if str(runs).startswith("uxsim") else (UXSIM_ROOT / runs)
    return Settings(
        base_url=get("UXSIM_BASE_URL", "http://localhost:3000").rstrip("/"),
        admin_username=get("UXSIM_ADMIN_USERNAME", "Administrator"),
        admin_password=get("UXSIM_ADMIN_PASSWORD"),
        persona_password=get("UXSIM_PERSONA_PASSWORD", "uxsim-persona-pass"),
        persona_llm_provider=provider,
        persona_llm_model=get("UXSIM_PERSONA_LLM_MODEL"),
        persona_llm_api_key=get("UXSIM_PERSONA_LLM_API_KEY"),
        runs_dir=runs,
        sandbox_database_url=get("UXSIM_SANDBOX_DATABASE_URL"),
        http_timeout_s=float(get("UXSIM_HTTP_TIMEOUT_S", "130")),
    )
