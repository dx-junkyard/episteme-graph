"""compose / .env.example の環境変数配線ガードレール（2026-09-03 新設）。

背景（このテストが守る事故）:
    ``backend/core/config.py`` の ``Settings`` は ``env_file=".env"`` を宣言するが、
    ``backend/Dockerfile`` は ``.env`` をイメージへ COPY しない（シークレットを
    イメージに焼かない方針）。したがって **compose が ``env_file:`` で ``.env`` を
    渡さない限り、コンテナ内には読むべき ``.env`` が存在しない**。
    2026-09-03 以前はまさにその状態で、``docker-compose.yml`` の ``environment:``
    に列挙された約30キー以外（機能別のコール上限・worker の on/off 等）は
    ``.env`` に書いても Docker 実行時には一切効いていなかった。

守る不変条項:
    1. ``docker-compose.yml`` の ``api-server`` が ``.env`` を ``env_file`` で読む。
    2. ``.env`` が無い環境でも compose が失敗しない（``required: false``）。
    3. ``backend/Dockerfile`` は ``.env`` を COPY しない（1 の前提であり、
       シークレットをイメージに残さないための条件でもある）。
    4. ``.env.example`` が「設定可能な env 名」を網羅する — ``Settings`` の
       主別名と、コードが ``os.getenv`` で直接読む名前の両方。
       新しい設定を足したら ``.env.example`` にも書く（正本はコード側だが、
       運用者が存在を知る唯一の入口が ``.env.example`` のため）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
COMPOSE = ROOT / "docker-compose.yml"
ENV_EXAMPLE = ROOT / ".env.example"
DOCKERFILE = BACKEND / "Dockerfile"

# os.getenv を直接読むソースの走査範囲（core / api / agents）。
_SCAN_ROOTS = (BACKEND / "core", BACKEND / "api", ROOT / "src")

# ``.env.example`` への記載を免除する env 名。
# - 後方互換の別名（``AliasChoices`` の2番目以降）はテスト側で primary のみ見るため
#   ここには挙げない。
# - 下記は「アプリ設定ではないもの」だけを列挙する。
_EXEMPT_ENV_NAMES = frozenset(
    {
        # Google SDK が自動参照する標準変数（llm.py が os.environ へ書き込む側）。
        # 設定としては GOOGLE_APPLICATION_CREDENTIALS の項が .env.example にある。
    }
)


def _load_compose() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML が無い環境ではスキップ")
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1〜3: compose / Dockerfile の配線
# ---------------------------------------------------------------------------


def test_api_server_declares_env_file() -> None:
    """api-server が .env を env_file で読む（列挙漏れの env を届ける唯一の経路）。"""
    compose = _load_compose()
    api = compose["services"]["api-server"]
    assert "env_file" in api, (
        "docker-compose.yml の api-server に env_file がありません。"
        " Dockerfile は .env を COPY しないため、これが無いと Settings(env_file='.env')"
        " はコンテナ内で読むファイルを持たず、environment: に列挙した変数しか効きません。"
    )
    entries = api["env_file"]
    assert isinstance(entries, list), "env_file はリスト形式で書いてください"
    paths = [e if isinstance(e, str) else e.get("path") for e in entries]
    assert ".env" in paths, f"env_file に .env がありません: {paths}"


def test_api_server_env_file_is_optional() -> None:
    """.env が無い環境（CI 等）で compose が失敗しないこと。"""
    compose = _load_compose()
    entries = compose["services"]["api-server"]["env_file"]
    for entry in entries:
        if isinstance(entry, dict) and entry.get("path") == ".env":
            assert entry.get("required") is False, (
                "env_file の .env は required: false にしてください"
                "（.env が無い環境で docker compose up が失敗するため）"
            )
            return
    pytest.fail(
        ".env は `- path: .env` / `required: false` の長形式で書いてください"
        "（短形式 `- .env` は .env 不在時に compose が失敗します）"
    )


def test_dockerfile_does_not_copy_dotenv() -> None:
    """イメージに .env を焼かない（シークレット非同梱・env_file が唯一の注入経路）。"""
    text = DOCKERFILE.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.upper().startswith("COPY"):
            continue
        assert ".env" not in stripped, (
            f"backend/Dockerfile が .env を COPY しています: {stripped!r}。"
            " シークレットをイメージに残さないため、注入は compose の env_file で行います。"
        )


def test_compose_environment_entries_are_documented_choices() -> None:
    """environment: に残す行は「env_file では足りない理由」があるものだけ。

    2026-09-03 の整理で、`VAR: ${VAR:-<コード既定値と同じ>}` の重複行は削除し、
    ① コンテナ固有の既定値 ② 複数変数の合成 ③ Settings が知らない旧 env 名の
    いずれかに該当する行だけを残した。ここでは「勝手に重複行が復活していないか」を
    件数ではなく明示的な許可リストで固定する。
    """
    compose = _load_compose()
    env = compose["services"]["api-server"].get("environment") or {}
    allowed = {
        "LLM_FAST_MODEL",  # ③ OPENAI_FAST_MODEL は Settings の別名に無い
        "LLM_STANDARD_MODEL",  # ③ 同上（+ tier 既定が Settings と異なる）
        "LLM_DEEP_MODEL",  # ③ 同上
        "DATABASE_URL",  # ② DB_* から合成
        "GOOGLE_APPLICATION_CREDENTIALS",  # ① コンテナ内 /app/.gcp/... が既定
        "GROBID_URL",  # ① コンテナ間はサービス名 grobid:8070
    }
    unexpected = sorted(set(env) - allowed)
    assert not unexpected, (
        "api-server.environment に新しい行が増えています: "
        f"{unexpected}。env_file: .env でそのまま届く変数は environment: に書かないでください"
        "（compose 側が既定値を二重に持つと、Settings の既定値変更が黙って無効になります）。"
        " コンテナ固有の既定値・変数合成・旧 env 名の吸収が必要な場合のみ追加し、"
        " このテストの allowed に理由コメント付きで登録してください。"
    )


# ---------------------------------------------------------------------------
# 4: .env.example の網羅
# ---------------------------------------------------------------------------


def _settings_primary_env_names() -> set[str]:
    sys.path.insert(0, str(BACKEND))
    from core.config import Settings  # noqa: PLC0415

    names: set[str] = set()
    for field_name, field in Settings.model_fields.items():
        alias = field.validation_alias
        if alias is None:
            names.add(field_name.upper())
            continue
        choices = getattr(alias, "choices", None)
        if choices:
            # 先頭が正式名。2番目以降は後方互換の別名なので .env.example に必須としない。
            names.add(str(choices[0]))
        else:
            names.add(str(alias))
    return names


def _direct_getenv_env_names() -> set[str]:
    """os.getenv / os.environ[...] が読む env 名を AST で集める（定数名も解決）。"""
    found: set[str] = set()
    for root in _SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 破損ファイル
                continue
            consts: dict[str, str] = {}
            for node in tree.body:
                target = None
                if isinstance(node, ast.Assign) and len(node.targets) == 1:
                    target = node.targets[0]
                elif isinstance(node, ast.AnnAssign):
                    target = node.target
                if (
                    isinstance(target, ast.Name)
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    consts[target.id] = node.value.value
            for node in ast.walk(tree):
                arg = None
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    src = ast.unparse(node.func)
                    if node.args and (
                        src.endswith("os.getenv")
                        or src == "getenv"
                        or "environ.get" in src
                    ):
                        arg = node.args[0]
                elif isinstance(node, ast.Subscript):
                    src = ast.unparse(node.value)
                    if src.endswith("os.environ") or src == "environ":
                        arg = node.slice
                if arg is None:
                    continue
                value = None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    value = arg.value
                elif isinstance(arg, ast.Name):
                    value = consts.get(arg.id)
                if value and value.isupper() and re.fullmatch(r"[A-Z][A-Z0-9_]+", value):
                    found.add(value)
    return found


def _env_example_names() -> set[str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # 有効行・コメントアウト行の両方を「記載あり」とみなす
    # （既定値のままで良い設定はコメントアウトで示すのが本ファイルの流儀）。
    return set(re.findall(r"^\s*#?\s*([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))


def test_env_example_covers_settings_fields() -> None:
    documented = _env_example_names()
    missing = sorted(_settings_primary_env_names() - documented - _EXEMPT_ENV_NAMES)
    assert not missing, (
        "Settings のフィールドに対応する env が .env.example にありません: "
        f"{missing}。用途と既定値をコメントで添えて追記してください"
        "（既定値のままで良いものはコメントアウト行で構いません）。"
    )


def test_env_example_covers_direct_getenv_names() -> None:
    documented = _env_example_names()
    missing = sorted(_direct_getenv_env_names() - documented - _EXEMPT_ENV_NAMES)
    assert not missing, (
        "os.getenv で直接読んでいる env が .env.example にありません: "
        f"{missing}。Settings に載せるか、.env.example に用途と既定値を追記してください。"
    )


def test_env_example_holds_no_real_secret_values() -> None:
    """サンプル値に実キーらしき文字列を書かない（.env.example はコミットされる）。"""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    # OpenAI 実キーの形（sk- + 十分な長さの英数字）。プレースホルダは許容する。
    for match in re.finditer(r"sk-[A-Za-z0-9_\-]{16,}", text):
        token = match.group(0)
        assert "your" in token.lower() or "xxx" in token.lower(), (
            f".env.example に実キーらしき値があります: {token[:12]}…"
        )
