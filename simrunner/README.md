# sim-runner（検証シミュレーション層の隔離実行コンテナ）

検証シミュレーション層（SB層）が LLM に生成させた理論プログラムと、部品から合成した実験機の
コードを**api-server の外で**実行するための小さなサーバです。正本は
`docs/features/simulation_bridge_design.md` §5.5（不変条項 SB3）。

- `POST /run` `{source, entrypoint, inputs, params, seed, timeout_s}` → `{ok, outputs, error, stderr_tail}`。
  `GET /health` は `{"ok": true}`。
- コードは子プロセス `python -I -S -B` に stdin で渡します。環境変数は渡さず、ディスクにも書きません。
  子プロセスにはメモリ（512MB）・CPU 時間・開けるファイル数・書けるファイルサイズ（0）の上限を掛け、
  import は `math` / `numpy` / `cmath` だけ、組み込み関数も許可リストに絞ります。
- api-server 側の AST 検査（`backend/core/simulation_bridge/ast_guard.py`）が前段で、ここは最後の防波堤です。

## compose での配線

`docker-compose.yml` のサービス `sim-runner` として内部ネットワーク `episteme` にだけ参加します
（ports なし・`read_only`・`cap_drop: [ALL]`・`no-new-privileges`・`mem_limit`・`pids_limit`）。
api-server は `SIM_RUNNER_URL`（既定 `http://sim-runner:8080`）で到達します。`backend/Dockerfile` は
このディレクトリを COPY しません。

## ローカルで動かす

```bash
python simrunner/server.py   # 0.0.0.0:8080 で待ち受け（SIM_RUNNER_PORT で変更可）
```

`.env` に `SIM_RUNNER_URL=http://localhost:8080` を置くと、ローカル実行の api-server から使えます。
ローカル実行ではコンテナのネットワーク隔離が無い点に注意してください（開発用途のみ）。
