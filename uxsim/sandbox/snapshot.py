"""砂場の snapshot（pg_dump + MinIO データの tar）を作る・戻す。

使い方（リポジトリルートで）:
  python uxsim/sandbox/snapshot.py make    <name> [--note "..."]
  python uxsim/sandbox/snapshot.py restore <name>
  python uxsim/sandbox/snapshot.py list

実体は uxsim/snapshots/<name>/{db.dump, minio.tgz}（git 管理外）。manifest.yaml だけ commit する。
docker compose のプロジェクト名は episteme-uxsim 固定（PE1）。製品コードを import しない。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAP_DIR = ROOT / "uxsim" / "snapshots"
PROJECT = "episteme-uxsim"
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "docker-compose.yml", "-f", "uxsim/sandbox/docker-compose.uxsim.yml"]
MINIO_VOLUME = f"{PROJECT}_uxsim_minio_data"


def _env(key: str, default: str = "") -> str:
    # compose と同じく .env を読む（DB_USER / DB_NAME）。シークレットは表示しない。
    val = os.environ.get(key)
    if val:
        return val
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return default


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), file=sys.stderr)
    return subprocess.run(cmd, cwd=ROOT, check=True, **kw)


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def _migration_head() -> str:
    files = sorted(p.name for p in (ROOT / "backend" / "db").glob("[0-9][0-9][0-9]_*.sql"))
    return files[-1][:3] if files else ""


def make(name: str, note: str) -> None:
    out = SNAP_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    user, db = _env("DB_USER", "episteme"), _env("DB_NAME", "episteme")
    with open(out / "db.dump", "wb") as fh:
        _run(COMPOSE + ["exec", "-T", "postgres", "pg_dump", "-U", user, "-Fc", db], stdout=fh)
    _run(["docker", "run", "--rm", "-v", f"{MINIO_VOLUME}:/data:ro", "-v", f"{out}:/backup", "alpine",
          "tar", "czf", "/backup/minio.tgz", "-C", "/data", "."])
    manifest = out / "manifest.yaml"
    existing = manifest.read_text() if manifest.exists() else ""
    stamp = (
        f"# --- snapshot record (appended by snapshot.py) ---\n"
        f"snapshot_record:\n  name: {name}\n  created_at: {dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}\n"
        f"  git_commit: {_git_commit()}\n  migration_head: '{_migration_head()}'\n  note: {json.dumps(note, ensure_ascii=False)}\n"
    )
    manifest.write_text(existing + ("\n" if existing and not existing.endswith("\n") else "") + stamp)
    print(f"snapshot written: {out}")


def restore(name: str) -> None:
    src = SNAP_DIR / name
    if not (src / "db.dump").exists():
        sys.exit(f"no db.dump under {src}")
    user, db = _env("DB_USER", "episteme"), _env("DB_NAME", "episteme")
    # api-server を止めてから DB を入れ替える（接続が残ると DROP できない）
    _run(COMPOSE + ["stop", "api-server"])
    # psql -c は複数文を 1 トランザクションで流すため DROP DATABASE が通らない → 2 回に分ける
    _run(COMPOSE + ["exec", "-T", "postgres", "psql", "-U", user, "-d", "postgres", "-c", f"DROP DATABASE IF EXISTS {db} WITH (FORCE);"])
    _run(COMPOSE + ["exec", "-T", "postgres", "psql", "-U", user, "-d", "postgres", "-c", f"CREATE DATABASE {db} OWNER {user};"])
    with open(src / "db.dump", "rb") as fh:
        _run(COMPOSE + ["exec", "-T", "postgres", "pg_restore", "-U", user, "-d", db, "--no-owner"], stdin=fh)
    _run(COMPOSE + ["stop", "minio"])
    _run(["docker", "run", "--rm", "-v", f"{MINIO_VOLUME}:/data", "-v", f"{src}:/backup:ro", "alpine",
          "sh", "-c", "rm -rf /data/* /data/.minio.sys && tar xzf /backup/minio.tgz -C /data"])
    _run(COMPOSE + ["up", "-d", "minio", "api-server"])
    print(f"restored: {name}（api-server 起動時に migration ランナーが追随する）")


def list_() -> None:
    for p in sorted(SNAP_DIR.glob("*/manifest.yaml")):
        has = (p.parent / "db.dump").exists()
        print(f"{p.parent.name}\t{'実体あり' if has else '実体なし（manifest のみ）'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make"); m.add_argument("name"); m.add_argument("--note", default="")
    r = sub.add_parser("restore"); r.add_argument("name")
    sub.add_parser("list")
    a = ap.parse_args()
    {"make": lambda: make(a.name, a.note), "restore": lambda: restore(a.name), "list": list_}[a.cmd]()
