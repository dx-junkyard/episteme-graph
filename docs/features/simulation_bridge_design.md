# 検証シミュレーション層（Simulation Bridge, SB層 — 理論⇔シミュレーション⇔実験の翻訳層）

> **状態: 実装済み（v1・2026-10-02 起票・同日実装）**。migration 088。オーナー判断 3 件（§9）は
> 2026-10-02 の「実装を進めてほしい」で推奨案を採用。実装記録は §12（docker 実機・ブラウザは未確認）。

**正本**: 本ドキュメント。
**関連**: [理論モジュール層](theory_module_layer_design.md)（TM1〜TM15 — シミュレータの単位になる
「少数の入力式と前提を受けて少数の結果式を返す部分系」）/
[賭け金の台帳](stakes_ledger_design.md)（SL1〜SL10 — 観測系 claim の同定・反証条件・閉世界語彙）/
[D層 疑義層](doubt_layer_issues.md)（台帳 `epistemic_ledger`・候補→確定の作法）/
[画像パイプライン + ナレッジライブラリ](image_pipeline_knowledge_library_design.md)（L層 —
装置部品の格納庫）/ [知識オブジェクト層](knowledge_objects_design.md)（KO2 stable_key）/
[制度指標カタログ](indicator_governance_design.md)（IG1〜IG5 — 教員向けに差の数値を出す面の統治）/
[可視性6軸](disclosure_axes_design.md)（DA2 — 新しい AI 通過点の宣言）/
[論文の再現性レビュー 2026-09-19](../architecture/knowledge_reproduction_review_2026-09-19.md)
（E-1 PDF 由来の式の信頼連鎖 — 本層は TeX 経路の教材を前提にする）/
[vision.md §2.8](../vision.md)（検証可能性は検査器に相対的 — 本層が担うのは「分析妥当性」の候補のみ）。

**オーナー判断（本書の前提・確定済み）**

| # | 判断 | 採用 | 根拠 |
|---|---|---|---|
| — | この層は開発用の仕組みか製品機能か | **製品機能**（episteme-graph 本体）。教員・研究者が自分の教材（論文）に対して使い、結果は D層の台帳に候補として戻る | オーナー 2026-10-02。uxsim（ペルソナ通し受講テスト）から借りるのは「評価者と是正者を分けて所見を課題として記帳する」運転の型だけ |
| O-1 | 生成コードの実行場所 | **別コンテナ `sim-runner`**（ネットワーク無し・読み取り専用・CPU / メモリ / 時間上限）。API サーバのプロセスで `exec` しない | 後戻りしにくい構造。uxsim の砂場と同じく製品プロセスから隔離する |
| O-2 | 差の数値を出すか（原則 4 改訂） | **教員・研究者向けの計器として制度指標カタログに登録したうえで数値を出す**。学習者には出さない | 計器の定義・用途を公開すれば数値を出してよい（vision §6.1）。学習者向け面は本層に無い |
| O-3 | 実験機の部品をどこに置くか | **L層 `library_entries` に同居**（`entry_type` を 3 つ足す） | 教員共同財・凍結版・retire・装置候補からの昇格導線をそのまま再利用できる |

---

## 1. 目的 — 理論と実験の間に「翻訳」を置く

論文の構造化成果は、式の操作（導出チェーン・理論モジュール）・観測系の主張（`MEASURES`・
diagnostic stage）・装置の部品（図からの apparatus 候補）・検証の記帳（台帳の
`verification_scopes` / 反証条件）までを別々に持っている。しかし「この理論モジュールを数値で
走らせ、この装置を通したら、論文が言う観測量が本当に見えるのか」を確かめる経路が無い。

本層は 3 つの部品を**互いに独立に**置き、1 つの運転でつなぐ。

| 部品 | 入力 | 出力 | 誰が作るか |
|---|---|---|---|
| 理論シミュレータ（`simulation_programs`） | 理論モジュール 1 つ（入力式・前提・出力式・記号） | 記号 → 数値の関数（Python） | LLM が候補を生成、教員が確定 |
| 実験機シミュレータ（`instrument_wirings`） | L層の部品（伝達関数・ノイズ源・信号処理の工夫）の配線 | 理論の出力 → 模擬観測 | 教員が組む。改訂案は LLM が部品の組み替えとして提案（候補） |
| 比較器（`core/simulation_bridge/comparator.py`） | 理論の出力・模擬観測・（任意）論文の記載値 | 段階ラベル + 事実文 + 教員向けの数値 | 決定論・非LLM |

運転の型は uxsim と同じ 3 役の分離（runner = 実行 / 審判 = 比較器 / 是正者 = 配線の改訂提案）
だが、是正者ができるのは**部品の組み替えだけ**で、新しいコードを書かない（§2 SB5）。

「より良い検証方法を見つける」は、同じ理論シミュレータに複数の配線を掛け、期待観測量を
最もよく分離できる配線を人に提示することに対応する（v1 は run の並置まで。自動探索は非スコープ）。

## 2. 不変条項（SB1〜SB10）

| # | 条項 | 内容 | 継承元 |
|---|---|---|---|
| SB1 | **シミュレーションの一致は検証ではない** | 結果は vision §2.8 の「分析妥当性」の候補にすぎない。台帳には `simulation_candidates` として入り、教員が確定しても `indirectly_supported` 止まり。`directly_verified` にしない。言えるのは「このモデルと配線のもとでは」だけ（閉世界） | SL1 / SL2 / §2.8 |
| SB2 | **3 部品の独立** | 理論プログラムは観測・ノイズ・装置を知らない（入力式 → 出力式の純関数）。実験機の配線は理論を知らない（信号 → 模擬観測）。比較器だけが両方を読む。1 つの行に混ぜない | 新規 |
| SB3 | **コードは候補・実行は隔離** | LLM が生成したコードは `review_status='candidate'`。実行前にサーバ側で AST 許可リスト検査（import は `math` / `numpy` / `cmath` のみ・属性アクセスの禁止語・`open` / `exec` / `eval` / `__import__` 不可）。実行は `sim-runner` コンテナ（HTTP・ネットワーク無し・時間 / メモリ上限）。api-server プロセス内で `exec` しない | O-1 / 原則 11 |
| SB4 | **比較器は決定論** | 比較・段階ラベル・事実文は非LLM。LLM は理論プログラムの生成と配線の改訂提案だけ | 原則 9 |
| SB5 | **是正者の操作は部品の組み替えに限る** | 改訂提案は L層**凍結版**の部品 ID の追加 / 除去 / パラメータ変更だけ。目録に無い部品・自由記述のコードは validator が落とす（捏造ガード）。提案は候補で、教員が確定して初めて run できる | 原則 1 / FG8 |
| SB6 | **数値の宛先** | 学習者向け DTO・画面は作らない。教員向けの差の数値は制度指標カタログに `per_item_no_person` で登録して出す。confidence は出さない | O-2 / IG1〜IG4 |
| SB7 | **情報を落とさない** | `simulation_runs` は append-only。programs / wirings は status 遷移（candidate → confirmed / dismissed、再生成は superseded）。DELETE API なし。失敗した run も行として残す | 原則 3 |
| SB8 | **A層非改変・読む側** | `src/episteme_graph/agents/` を変更しない。理論モジュール層・台帳・L層は読むか、設計済みの追加列 / 追加 entry_type だけで受ける | 原則 13 |
| SB9 | **LLM 回数と実行の上限** | 生成・改訂提案はそれぞれ同期 1 コール + 日次 CostGate。run は同期 1 回で `SIMULATION_RUN_TIMEOUT_SECONDS`。ループは人が 1 回ずつ押す（自動反復なし） | 原則 9 / 原則 12 |
| SB10 | **式の出所の正直さ** | 復元由来（`must_not_treat_as_source_extracted`）の式しか無いモジュールもコード化は拒まないが、プログラムの `provenance.reconstructed_inputs=true` と事実文を必ず持ち、比較結果の候補にも同じ印を写す | E-1 / R-1 / 原則 8 |

## 3. 全体像

```
理論モジュール（読み時導出 + knowledge_theory_modules）
   │  入力式・前提・出力式・記号（knowledge_equations / knowledge_symbols）
   ▼
[LLM 1 コール] 理論プログラム候補 ── AST 検査 ── 教員確定 ──┐
                                                            │
L層 部品（instrument_model / noise_source / signal_processing）│
   │  教員が配線（instrument_wirings）                      │
   ▼                                                        ▼
                     sim-runner（隔離実行）: 理論 → 出力 → 配線 → 模擬観測
                                                            │
                                   比較器（決定論）: 段階ラベル + 事実文 + 数値
                                                            │
             ┌──────────────────────────────┬───────────────┴──────────────┐
             ▼                              ▼                              ▼
   台帳へ候補として記帳            [LLM 1 コール] 配線の改訂提案        run の並置（複数配線）
   (epistemic_ledger.simulation_candidates)  (部品の組み替えのみ・候補)
```

## 4. データモデル（migration 088）

### 4.1 `knowledge_entry_types` の追加（L層・同居）

| entry_type | label | `body` の規約（`core/simulation_bridge/schema.py::PART_BODY_KEYS`） |
|---|---|---|
| `instrument_model` | 装置モデル | `transfer`（Python 式。`x` と `params` だけを参照）/ `params`（`{name: {default, unit, min, max}}`）/ `inputs` / `outputs`（記号名） |
| `noise_source` | ノイズ源 | `noise_kind`（`gaussian` / `poisson` / `one_over_f` / `drift` / `offset`）/ `params` / `depends_on`（装置依存の説明） |
| `signal_processing` | 信号処理の工夫 | `transfer`（例: 平均化・差動・ロックイン）/ `params` / `removes`（効くノイズ種別） |

`body` は L層のまま自由 dict。規約の検査は本層の `validate_part_body()` が配線時に行う（L層の
store には手を入れない）。同梱の初期部品は `backend/library_common/instrument_parts.json`
（`domain_key='unassigned'`・人間が書いた JSON。`seed.py` が走査先を 1 つ足すだけ）。

### 4.2 `simulation_programs`（理論シミュレータ）

| 列 | 意味 |
|---|---|
| `id` UUID / `document_id` UUID FK CASCADE | |
| `module_stable_key` TEXT / `module_key` TEXT | 理論モジュール（保存行の stable_key と読み時キー）。保存行が無いときは `module_key` だけ |
| `language` TEXT（`'python'` 固定） / `source_code` TEXT | 生成コード。関数 `simulate(inputs: dict, params: dict) -> dict` を 1 つ定義する |
| `io_contract` JSONB | `{inputs: [{symbol, kind, unit, default}], outputs: [{symbol, kind, unit}], parameters: [...]}` |
| `provenance` JSONB | `{model, equation_ids, symbol_ids, reconstructed_inputs: bool, facts: [..]}`（confidence は持たない） |
| `review_status` TEXT CHECK（`candidate` / `confirmed` / `dismissed` / `superseded`）/ `review_note` / `decided_by` / `decided_at` | 候補 → 確定は `candidate_flow` 経由 |
| `created_by` / `created_at` / `updated_at` / `superseded_at` / `superseded_by_id` | 再生成は旧行を superseded にして新行を挿入 |

### 4.3 `instrument_wirings`（実験機シミュレータ）

| 列 | 意味 |
|---|---|
| `id` / `document_id` UUID FK CASCADE（nullable: 論文に紐づかない汎用配線を許す） / `name` | |
| `parts` JSONB | `[{slot, entry_id, version_no, params}]`（`entry_id` は L層の行・`version_no` は凍結版） |
| `edges` JSONB | `[{from_slot, to_slot}]`（信号の流れ。無向ではない） |
| `origin` TEXT CHECK（`teacher` / `llm_candidate`）/ `parent_wiring_id` UUID / `revision_note` | 改訂提案は親を指す新行 |
| `review_status` TEXT CHECK（`candidate` / `confirmed` / `dismissed`）/ `review_note` / `decided_by` / `decided_at` | 教員が組んだ配線は `confirmed` で始まる |
| `created_by` / `created_at` / `updated_at` | |

### 4.4 `simulation_runs`（append-only）

| 列 | 意味 |
|---|---|
| `id` / `document_id` / `program_id` FK / `wiring_id` FK nullable | |
| `observation_claim_ids` JSONB | 比較対象の観測系 claim（`observation_targets` から教員が選ぶ） |
| `inputs` JSONB | `{values, params, reference: {value, unit, source_note}?, seed}` |
| `result` JSONB | `{theory_outputs, observed, comparison: {level, facts, metrics}}` |
| `runner` JSONB | `{status, error, ast_check, exit_code}`（内部） |
| `status` TEXT CHECK（`succeeded` / `failed` / `rejected`） | `rejected` = AST 検査で実行前に止めた |
| `ledger_candidate_id` TEXT | 台帳へ記帳したときの候補 ID（任意） |
| `created_by` / `created_at` | |

### 4.5 `epistemic_ledger.simulation_candidates` JSONB

反証条件候補（`falsification_candidates`）と同じ「配列に追記・confirm / dismiss は配列内の
status 遷移」。1 要素:

```
{candidate_id, run_id, program_id, wiring_id, statement, level, facts: [...],
 reconstructed_inputs: bool, status: "candidate" | "confirmed" | "dismissed",
 detector_version: "simulation-bridge/v1", created_at, created_by,
 confirmed_by?, confirmed_at?, dismissed_by?, dismissed_at?}
```

確定時に `verification_scopes` へ **`system` 軸に配線名・`condition` 軸に入力条件**を持つ
スコープを 1 件追加し、`verification_status` が `untested` / `unknown` なら
`indirectly_supported` へ上げる（それ以上には上げない = SB1）。`evidence_lines` には書かない。

## 5. 各部品の規則

### 5.1 理論プログラムの生成（LLM・候補）

- 入力: 理論モジュール DTO（`inputs` / `outputs` / `assumptions` / `members[].step_refs`）+
  保存行の `member_steps[].input_equation_ids / output_equation_ids` → `knowledge_equations_live`
  の `latex` / `plain_text` / `agent_payload.confidence_policy` + 関係する `knowledge_symbols_live`
  （`canonical_symbol` / `kind` / `unit` / `domain_constraints` / `definition_evidence_texts`）。
  すべて untrusted 区画（`UNTRUSTED_SOURCE_NOTICE`）で提示する。
- 出力（structured）: `source_code` / `io_contract` / `facts`（何を近似したか・何を省いたか）。
- 検証（決定論）: ① `ast_guard.check(source)`（§2 SB3）② `simulate` 関数が 1 つ定義されている
  ③ `io_contract.outputs` の記号が理論モジュールの `outputs` の記号（`equation_lhs_symbol`）と
  重なる（重ならなければ warning・候補のまま）。修復は同一コール内 1 回まで。
- `provenance.reconstructed_inputs` は入力式の 1 つでも `must_not_treat_as_source_extracted`
  なら true。
- feature `simulation:codegen` / scene は `simulation`（新設・text）/ env
  `SIMULATION_CODEGEN_MAX_CALLS_PER_DAY`（既定 20）/ `SIMULATION_LLM_MODEL`（空 = fast）。

### 5.2 実験機の合成（決定論）

- 配線から**実験機プログラム**を決定論で合成する（`instrument.compose(wiring, parts) -> source`）。
  部品の `transfer` 式は `ast_guard` の式モード（式 1 つ・`x` / `params` / `numpy as np` /
  `math` だけ）で検査してから埋め込む。ノイズ源は `noise_kind` ごとのサーバ側テンプレート
  （`numpy.random.default_rng(seed)`）で、部品側はパラメータしか持たない。
- 合成結果も `simulate_instrument(signal, params, seed) -> dict` の 1 関数で、理論プログラムと
  同じ sandbox で走る（別プロセス・別呼び出し。SB2）。
- 部品は凍結版 `library_entry_versions.content` から読む（draft を読まない = L層の規律）。
  `review_status='candidate'` の entry は凍結できないので自然に除外される。

### 5.3 比較器（決定論）

| 観点 | 計算 | 段階ラベル（`label_vocab.SIMULATION_AGREEMENT_SCALE`） |
|---|---|---|
| 一致（理論出力 vs 模擬観測の中央値） | 相対差 |δ| = |obs − theory| / max(|theory|, ε) | 0.05 / 0.20 で「一致 / おおむね一致 / 乖離」 |
| 分離（効果量 vs ばらつき） | theory の出力の大きさ / 模擬観測の標準偏差 | 3 / 1 で「分離できる / 境界 / 埋もれる」 |
| 論文の記載値との差（任意） | reference があるときだけ同じ相対差 | 同上 |

数値は `result.comparison.metrics` に残し、教員向け DTO には `metrics` をそのまま出す（O-2・
カタログ登録 `simulation.agreement`）。学習者向けの経路は作らない。事実文は閉世界で書く
（「このモデルと配線『◯』のもとでは…」）。

### 5.4 配線の改訂提案（LLM・候補・部品の組み替えのみ）

- 入力: 比較結果の事実文 + 現在の配線 + **凍結版部品の目録**（名前・要約・`removes` / `params`
  の名前）。
- 出力（structured）: `changes: [{op: add | remove | set_param, slot, entry_id?, param?, value?, reason}]`。
- validator: `entry_id` は目録に実在・`param` は部品の `params` に実在・`value` は `min` / `max`
  の内・`op` 語彙外は drop。全 drop なら提案なし（事実文）。通った変更で新しい
  `instrument_wirings` 行（`origin='llm_candidate'`・`parent_wiring_id`・`review_status='candidate'`）
  を作る。教員が確定するまで run に使えない。
- feature `simulation:revision` / env `SIMULATION_REVISION_MAX_CALLS_PER_DAY`（既定 20）。

### 5.5 sandbox（`sim-runner`）

- `simrunner/`（リポジトリ直下。`backend/Dockerfile` は COPY しない）: `python:3.11-slim` +
  `numpy`。stdlib `http.server` で `POST /run {source, entrypoint, inputs, params, seed, timeout_s}`
  → 子プロセス `python -I -S`（`RLIMIT_AS` / `RLIMIT_CPU`・timeout）で実行し、`{outputs, error}` を
  返す。環境変数も引数も渡さず、コードは stdin。
- compose: サービス `sim-runner`（内部ネットワークのみ・ports 無し・`read_only: true`・
  `cap_drop: [ALL]`・`mem_limit`）。api-server は `SIM_RUNNER_URL`（既定 `http://sim-runner:8080`）で
  到達。未設定・不達は run を `failed` にして事実文「実行環境に到達できません」（LLM は呼ばない）。
- サーバ側 `ast_guard` は sandbox の前段で、sandbox は最後の防波堤（二重）。テストでは
  `SandboxClient` を注入して差し替える（HTTP を出さない）。

## 6. API（`routes/simulation_bridge.py`・`/api/admin/simulation/...`・全て `_require_teacher`）

| メソッド | パス | ゲート | 内容 |
|---|---|---|---|
| GET | `/documents/{id}/overview` | viewable | 理論モジュール（コード化できるもの）・programs・wirings・runs・観測系 claim を 1 画面分 |
| POST | `/documents/{id}/programs/generate` | editable + CostGate | body `{module_key}` → LLM 1 コール → candidate 行 |
| POST | `/programs/{id}/decide` | editable | `{action: confirm \| dismiss, note?}`（`candidate_flow`） |
| GET | `/parts` | teacher | 凍結版の部品目録（3 entry_type） |
| GET / POST | `/documents/{id}/wirings` | viewable / editable | 一覧 / 教員が組む（`confirmed` で作成・`validate_wiring`） |
| POST | `/wirings/{id}/decide` | editable | 改訂候補の confirm / dismiss |
| POST | `/documents/{id}/runs` | editable | `{program_id, wiring_id?, observation_claim_ids, values, params, reference?, seed?}` → sandbox 同期実行 → 比較 → 行 |
| GET | `/documents/{id}/runs` / `/runs/{id}` | viewable | |
| POST | `/runs/{id}/ledger-candidate` | editable | 台帳へ候補を 1 件追記（`target_type='claim'`・観測系 claim ごと） |
| POST | `/runs/{id}/propose-revision` | editable + CostGate | §5.4 |
| POST | `/ledger/{tt}/{tid}/simulation-candidates/{cid}/confirm \| dismiss` | `_require_editable_ledger_target` | 反証条件候補と同型（`routes/doubt.py` に置く） |

エラーは日本語事実文・数値なし。監査は新 entity `AUDIT_ENTITY_SIMULATION`（program / wiring /
run / 台帳候補の遷移）。DELETE ルート無し。

## 7. UI（第 2 波）

教材管理の各行 ⋯ メニュー「検証シミュレーション…」→ フルモーダル `admin-simulation-bridge.js`
（ES5・`window.SimulationBridge`・DI 注入）。4 区画: ①理論モジュール → [コードを生成] → コード
と入出力契約 → [確定 / 見送り] ②配線（部品目録から組む・改訂候補は点線枠 + 「AI の提案（未確認）」）
③実行（入力値・観測系 claim・論文の記載値（任意）・seed）→ 比較の段階ラベル + 事実文 + 数値表
④[台帳に候補として記帳] / [改訂案を求める]。アンカー `materials.row-simulation` +
`simulation.{generate,program-decide,wiring-new,run,ledger-candidate,propose-revision}` +
マニュアル `docs/manual/teacher/27-admin-simulation-bridge.md`。

## 8. 環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `SIM_RUNNER_URL` | `http://sim-runner:8080` | sandbox の到達先。空なら run は常に失敗事実文 |
| `SIMULATION_RUN_TIMEOUT_SECONDS` | 20 | 1 run の上限 |
| `SIMULATION_LLM_MODEL` | 空（fast） | 生成・改訂提案のモデル |
| `SIMULATION_CODEGEN_MAX_CALLS_PER_DAY` | 20 | |
| `SIMULATION_REVISION_MAX_CALLS_PER_DAY` | 20 | |

## 9. オーナー判断

§冒頭の表（O-1〜O-3）。いずれも推奨案で確定。

## 10. 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い（採る部品・見送る派生形） |
|---|---|---|
| 生成コードがそのまま走る / 一致が検証に読める | `ai-decides-instead-of-human` | SB1 / SB3 — status 遷移を `candidate_flow` に通し、台帳は `indirectly_supported` 止まり、文言は閉世界 |
| 改訂提案が目録に無い部品を挙げる | `fallback-fabricates-missing-link` | SB5 validator（entry_id / param / 範囲）で drop |
| 生成コードがファイル・ネットワークに触る | `untrusted-input-reaches-instruction` | AST 許可リスト + ネットワーク無しコンテナの二重。式・記号は untrusted 区画で提示 |
| PDF 経路の復元式でコードが「それらしく」動く | `upstream-loss-masked-downstream` | SB10 の印を program → run → 台帳候補まで写す |
| 他人の教材のモジュールでコード生成 | `permission-and-affordance-asymmetric` | editable ゲート。viewable は閲覧のみ |
| 再生成が確定済みプログラムを消す | `reexecution-overwrites-human-decision` | 再生成は superseded 遷移で旧行を残す（SB7） |
| 新しい教員向け GET がカタログ・anchor の網羅から漏れる | `guardrail-does-not-cover-new-path` | indicator catalog `_NON_INDICATOR_ROUTES` / anchors / `_AUDIT_CALLER_FILES` へ同時登録 |

当たらない族: 該当なし（運用層の型 `external-budget-exceeded` は CostGate 日次上限で既に塞ぐ）

## 11. 開発チェックリスト §5 対応

- 機能解説: CLAUDE.md に節追加（同時）。設計書の状態ヘッダ（本書冒頭）。
- migration は実番号 088（採番前に `ls backend/db/` で確認済み）。
- 登録: `docs/architecture/layer_registry.md`（層・版の語彙 §4: programs / wirings は revision =
  `prov:wasRevisionOf`（superseded / parent_wiring_id））/ `AUDIT_ENTITY_SIMULATION` /
  `KNOWN_FEATURES` 2 件 / llm_policy scene / config / indicator catalog `simulation.agreement` /
  disclosure_axes の AI 通過点（`teacher_reviews` 相当の data_kind に `simulation:codegen` /
  `simulation:revision`）/ main.py ルーター登録。

## 12. 実装記録

### 12.1 v1（2026-10-02・Fable 指揮 + Opus 5 体）

第 1 波（UI なし・並列 3 体）→ 第 1.5 波（API）‖ 第 2 波（UI・アンカー・マニュアル）の順。全 backend 18,703 /
src 2,264 pass（2026-10-02 時点）。docker 実機・ブラウザは未確認（`sim-runner` の起動と `admin.html` の再ビルドが要る）。

| 部品 | 実装 | 設計からの差分 |
|---|---|---|
| 語彙・契約 | `core/simulation_bridge/schema.py` | — |
| DB | `backend/db/088_simulation_bridge.sql`（3 表 + `epistemic_ledger.simulation_candidates` + entry_type 3 件）| `created_by` / `decided_by` は TEXT・users FK なし。再生成は**新行を先に挿入してから**旧行を superseded（`superseded_by_id` の FK のため） |
| 部品シード | `backend/library_common/instrument_parts.json`（11 件・`domain_key='unassigned'`）/ `library/seed.py` が走査先を 1 つ追加 / Dockerfile COPY | — |
| store | `store.py`（`candidate_flow` 経由・DELETE なし・台帳候補に `wiring_name` / `condition` を控えとして追加）| 監査は commit 後にバッファから記帳 |
| AST 検査 | `ast_guard.py`（プログラム / 式の 2 モード）| `with` / `class` / async / `global` も拒否 |
| sandbox | `sandbox.py`（urllib・transport 注入）/ `simrunner/`（stdlib http.server・`python -I -S` 子プロセス・rlimit・制限 builtins）| compose は `sim-internal`（`internal: true`）だけに参加（`episteme` は外向き通信ができるため）。`.env.example` の上書き一覧は実態の 4 変数に是正 |
| コード生成 | `codegen.py`（untrusted 区画 + `structured_call`・修復 1 回）| 式本文ゼロは LLM を呼ばず 422 |
| 実験機 | `instrument.py`（一本の信号経路のみ・ノイズはサーバ側テンプレート・`_repeats` の集団平均）| 分岐は v1 非対応の事実文 |
| 比較器 | `comparator.py` + `label_vocab.SIMULATION_*`（`GradedScale` は「高いほど良い」専用なので相対差は符号反転で掛ける）| 分離は 1 記号でも std が無ければ未測定 |
| 改訂提案 | `revision.py`（最大 3 変更・目録外 drop・適用後に `validate_wiring` 再検査）| — |
| API | `routes/simulation_bridge.py`（13 本）+ `routes/doubt.py` の候補確定 2 本 | **`TeacherDisplayRoute` は使わない** — 記号をキーにした写像から `k` / `count` 等が消えるため、本層専用 `SimulationDisplayRoute`（`FORBIDDEN_DTO_KEYS` のみ落とす + 制御文字除去）を掛ける |
| 登録 | `AUDIT_ENTITY_SIMULATION` / `KNOWN_FEATURES` 2 件 / scene `simulation` / config 5 項目 / 可視性 6 軸の AI 通過点 2 件 / 制度指標 `simulation-agreement` / capability `materials.simulation_bridge` | 指標 id はカタログ規約の kebab-case（設計書本文の `simulation.agreement` 表記は id ではなく呼称） |
| UI | `admin-simulation-bridge.js`（4 区画）+ アンカー 9 件 + `docs/manual/teacher/27-admin-simulation-bridge.md` | 段階ラベルはサーバ解決の `result.labels` を素通し |

**検証で分かったこと**: 既存の `tests/core/test_library_store.py` 2 件が同梱シードの初版凍結を経由して embedding API を
実際に呼んでいた（CI のダミーキーで 401・課金なし）。`LIBRARY_COMMON_DIR` を tmp に差し替える fixture で塞いだ
（P-0011）。

**次にやること**: ①`docker compose ... up -d --build api-server sim-runner` で `sim-runner` の到達と `GET /overview` の
`sandbox_configured` を確認 ②TeX 経路の教材（2609.15375v1 TeX 取り込み）で理論モジュール 1 つのコード生成 → 配線
「線形増幅器 + ガウス雑音 + 平均化」→ 実行 → 台帳候補まで通す ③ペルソナ通し受講の教員場面に本画面を 1 場面足す。

## 13. 非スコープ（v1）

学習者向け表示 / 自動反復（是正者を人の操作なしに回す）/ 複数配線の自動探索・最適化 /
Python 以外の言語 / 外部データ（実測値ファイル）の取り込み / 装置候補（apparatus_semantics）から
`instrument_model` への自動昇格 / 台帳 `evidence_lines` への記帳 / 理論モジュール以外（単一式・
component）の単位でのコード生成。
