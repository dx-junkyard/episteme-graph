"""砂場の api-server をネイティブ起動する（docker が使えない環境の代替経路）。

前提: オーナーの開発スタックの Postgres(5432) / MinIO(9000) / GROBID(8070) が localhost に公開されている。
専用 DB `episteme_uxsim` を使う（開発 DB には触れない）。MinIO は共有（バケット名が固定のため — 記録に残す）。
.env は shell の source ではなく自前で読む（`KEY= value` のような行があるため）。
"""
from __future__ import annotations

import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(path: pathlib.Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    port = os.environ.get("UXSIM_API_PORT", "8011")
    env = dict(os.environ)
    env.update(load(ROOT / ".env"))
    env.update(load(ROOT / "uxsim" / "sandbox" / ".env.uxsim"))
    env["DATABASE_URL"] = f"postgresql://{env['DB_USER']}:{env['DB_PASSWORD']}@localhost:5432/episteme_uxsim"
    env["MINIO_ENDPOINT"] = "localhost:9000"
    env["GROBID_URL"] = "http://localhost:8070"
    env["CORS_ORIGINS"] = f"http://localhost:{port}"
    env["PYTHONPATH"] = f"{ROOT / 'backend'}:{ROOT / 'src'}:{ROOT / 'backend' / 'api'}"
    os.chdir(ROOT / "backend" / "api")
    py = str(ROOT / "backend" / ".venv" / "bin" / "python")
    os.execve(py, [py, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", port, "--log-level", "info"], env)


if __name__ == "__main__":
    sys.exit(main())
