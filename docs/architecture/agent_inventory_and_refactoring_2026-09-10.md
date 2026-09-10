# AI エージェント全件棚卸しと共通化リファクタリング（2026-09-10）

[← アーキテクチャ概要](overview.md) ｜ 関連: [横断基盤の整理記録（2026-07）](consolidation_survey_2026-07.md) /
[対ユーザー支援エージェント調査（2026-07）](user_assistant_agents_survey_2026-07.md) /
[共通規約の正本](../features/assistant_common_infra_design.md)

> **状態:** 調査記録（完了）+ リファクタリング実施済み（§6 実装記録）。本調査以降の変更は未評価。

## 0. 目的と方法

リポジトリ内で LLM を呼ぶ「エージェント」（パイプライン agent・非同期 worker・チャット型・
単発ヘルパー）を **全件** 列挙し、次の3つに分類したうえでリファクタリングを実施した。

| 分類 | 意味 |
|---|---|
| **共通化（A）** | 本質的な制御構造が同一で、1つの共有ランナー／宣言に置き換えても観測可能な挙動が変わらないもの |
| **アダプター（B）** | 骨格は同じだが、系統固有の差分（引数形・部分検証・診断シンク・画像 kwarg 等）をフックとして注入する必要があるもの |
| **独自仕様（C）** | 骨格そのものが異なる、または既に別の共通基盤に乗っており再統合が退行になるもの。非LLM の決定論 builder もここ |

方法: Fable 5.1 が指揮、Opus 5 が①`src/episteme_graph/agents/`（22ディレクトリ）②backend
非同期 worker 7系統 + チャット型 11系統 ③backend 単発 LLM 呼び出し 34地点 を読み取り専用で実査し
（file:line 付き）、その結果を Fable が分類・設計し、Opus 4体が非重複ファイル所有で並列実装した。
ベースライン: backend 13,909 pass / src 1,828 pass。

## 1. 既に共通化されていたもの（前提）

- `backend/core/llm_worker/`（2026-07 Tier 2 提案6）: `BaseJSONLLMClient` / `run_with_repair`
  （**例外は continue**・`complete_json(str)` プロトコル）/ `CostGate` / `window_history`。
  worker 7系統 + `discuss_opening` / `landscape_placement` が利用。
- `src/episteme_graph/agents/llm_json_client.py::ProviderJSONLLMClient`（フェンス除去・
  `recover_truncated_json`・wall timeout・contextvars 継承）と `cartridge_loader.py`（提案9）。
- チャット型の共通規約（2026-07-20、`assistant_common_infra_design.md` §10）: CostGate(day-only)・
  `window_history`・`resolve_model`・degraded-200。

本調査はこれらの **外側に残っていた重複** を対象にした。

## 2. インベントリ（LLM を呼ぶ系統）

### 2.1 パイプライン agent（`src/episteme_graph/agents/`、22ディレクトリ）

| agent | LLM | run() の骨格の要点 | repair ループ | 分類 |
|---|---|---|---|---|
| paper_skeleton | ✔ | cartridge→input→messages→generate→`_parse_raw`→validate→repair | 独自コピー（基準形） | **A** |
| thesis_reconstruction | ✔ | 同上 + `canonicalize_claim_refs` 前処理 + traversal/exclusion 後処理（ループ外） | 独自コピー | **A** |
| narrative_annotator | ✔ | 同上 + graph 非改変の事後検証。parse は `(raw, llm_input)`、fallback は issues を持たない | 独自コピー | **A** |
| component_graph | ✔ | 同上 + nodes を閉包で持つ。merge/normalize は後処理 | 独自コピー | **A** |
| rhetorical_role | ✔ | block ごとに1コール。サブレコード parse + 部分 Result で検証 | 独自コピー | **B** |
| claim_qualification | ✔ | span ごとに1コール + 未解決参照の**追加1コール**（ループ外・テストで call_count 固定） | 独自コピー | **B** |
| dsl_linking | ✔ | `_has_no_nodes_error` の**事前短絡** + cleanup を parse に合成 | 独自コピー | **B** |
| equation_semantics | ✔ | 3段（候補→受理ゲート→LLM）+ vision probe（`image=` kwarg）+ `_vision_ocr` 刻印 | 独自コピー | **B**（vision 部は client 側） |
| component_assembly | ✔ | preflight→generate→canonicalize→parse→cleanup→enrich→validate→repair→refiner 等 | 独自コピー + **diagnostics シンク** | **B**（最も重い） |
| apparatus_semantics | ✔ vision | one-shot 経路 / `iterative.py` 状態機械（仮説→観察→照合→検証、上限 1/2/1） | one-shot: 独自コピー / iterative: 独自 | **B / C** |
| contextual_explanation | ✔ | バッチ + 要素単位の部分受理 `(results, calls)` | 独自（形が異なる） | **B（別入口）** |
| discuss_opening | ✔ | `core/llm_worker.run_with_repair` に委譲（`complete_json(str)`・例外 continue） | 共通基盤 | **C**（再統合しない） |
| landscape_placement | ✔ | discuss_opening の双子 | 共通基盤 | **C** |
| document_structure / evidence_registry / claim_object_builder / symbol_registry / derivation_chain / figure_table_semantics / course_mapping / blueprint / document_unit_boundary | ✗ | 決定論 builder | — | **C（非LLM）** |

共通ループの契約（11コピーで一致）: `_MAX_REPAIR_ATTEMPTS = 2`・初回1コール + 最大2回修復・
**LLM 例外はループを break**・warning のみは受理・fallback は agent 固有。
`core/llm_worker/repair.py` とは例外時の挙動とクライアント protocol が逆なので **統合しない**。

ループ以外の重複: `ValidationIssue` dataclass ×22（全て同一）・`_load_cartridge` ×12（同一）・
`[i for i in issues if i.severity == "error"]` ×約30・confidence clamp ×9（既定値が 0.0/0.5/0.75 で
異なるため統合対象外）・`equation_semantics/vision_probe.py`（参照ゼロ → §6 で削除）。

### 2.2 非同期 LLM worker（backend、7系統）

tension / structure_anchor / reconstruction / doubt.scope_candidates / doubt.assumption_mining /
doubt.falsification_conditions / deliberation.standardization。いずれも `core/llm_worker/` を利用済み
だが、系統ごとに次の glue が残っていた（合計 864 行・パッケージ総量の 12%）:

| glue | 状態 |
|---|---|
| `llm_client.py`（29行） | 7本とも同型。`resolve_model()` / `parse_json_response` の再エクスポートは**参照ゼロ（dead）** |
| `repair.py`（32〜43行） | `run_with_repair` を validate/on_repair_failed 閉包で包む同型 glue |
| `agent.py` | 5系統にあり reconstruction / assumption_mining に無い（非対称）。client 構築は lazy/eager が混在 |
| gate ブロック | `_cost_gate = CostGate()` + `_check_and_count_llm_call` が7通り。session+day（tension/anchor）/ day-only（他5）。`prune_stale_daily` は doubt×3 + standardization のみ・**reconstruction は欠落**（stale キーが溜まる） |
| thread 起動 | `args=`/`kwargs=`・`name=` 有無・try/except 有無・戻り値 bool/None がばらばら |
| ガードレール | `test_llm_worker_guardrails.py` の委譲検査は5系統固定で、falsification_conditions / standardization が**対象外** |

分類: glue（client / repair / gate / spawn）= **A**、`agent.py`（skip 述語）= **B**、
worker の SQL・冪等マーカー（5種類）・トリガ・`_confirm_gate`・per-document cap・L層書込禁止 = **C**。

### 2.3 チャット型・会話ターン（backend、11系統）

| 系統 | API | 骨格 | 分類 |
|---|---|---|---|
| W層 `dialogue.run_turn` | `generate_conversation_turn` | grounding を**最初の user** に注入→1 structured コール→degraded→hygiene | **A（chat_turn）** |
| `graph_dialogue.run_graph_turn` | 同 | `dialogue` の**フォーク**（`build_llm_messages` 同一アルゴリズム・`_MATH_DELIMITER_INSTRUCTION` 重複） | **A** |
| 教材図 `generator.run_figure_turn` | 同 | grounding を**現ターン**に注入 + SVG sanitize の1回 repair。route と二重 `window_history` | **A + B（repair）** |
| 教材図 `suggest.py` | structured | 手書き1回 repair（「行数が多い方を採る」タイブレーク） | **B** |
| Admin Copilot `intent.classify` | structured | 分類器（応答は KB テンプレ）。grounding は**最後**。拒否は heuristic 縮退 | **B** |
| 学習チャット `_learning_chat_core`（800行） | `generate_text` | 5 プロンプト様相 + RAG + 誤解検出 + 痕跡 — 分岐が機能そのもの | **C** |
| `_generate_graph_element_explanation` | `generate_text` | try/except 無し → LLM 失敗が **500**（本体は 200 縮退で非対称） | **B（是正）** |
| `check_topic_understanding` | `generate_text` | 関数内 `import json, re` + 独自 JSON 抽出 | **B（parser のみ）** |
| コースビルダー chat | `generate_text` | `---COURSE_DRAFT_JSON---` マーカープロトコル | **C**（フェンス除去のみ共通化） |
| atlas `interpret` / `propose` | structured | 独自履歴語彙 `teacher/agent`・DB 集計ゲート・429 に**数値を出す** | **B（数値是正）** |
| radar `run_compare` | structured | 明示操作のため**縮退させず 502**・verbatim 検査 | **C** |

### 2.4 単発 LLM ヘルパー（backend、34地点）

- JSON 抽出の実装が **13通り**（正本 `parse_json_response` の他に P1〜P13、`api/routes/theory_components.py` の
  `_parse_json_object` は byte-identical かつ呼び出しゼロ）。LaTeX バックスラッシュ修復は
  course_content_builder / topics の2箇所（同一）。切り詰め復元は `src` 側にのみ存在。
- 「structured → free-text JSON 降格」の対が2箇所（course_content_builder / lecture_studio.topics）。
- embedding の `usage_context` ラッパが3箇所同型（help_kb / atlas_vectors / discovery.ranking）。
- `consume_*_quota`（settings→gate→429 事実文）が4箇所。
- **デッドコード**: `core/graphs/student_graph.py`（LangGraph 系・route 未配線）、`core/chat.py`
  （CLAUDE.md でも削除候補）、`theory_components.extract_theory_components_from_chunk`、
  `services.generate_missing_link_suggestions`、`routes/theory_components.py` の未配線リトライ骨格。
- **帰属の穴**: `meta_analyzer` / `simulator` / lecture interrupt chat が `usage_context` 無し
  （`unattributed` に流れる）。
- 分類: フェンス除去・降格対・embed ラッパ・quota ラッパ = **A**、`lecture.py` の 429 対応3回リトライ
  （プロンプト非変更）と compare の非縮退 = **C**、その他パーサ移行 = **A〜B**。

## 3. 設計判断（共通化の単位）

| 新設 | 置き場 | 吸収するもの | 吸収しないもの |
|---|---|---|---|
| `agents/llm_step.py::run_repair_loop` | `src/` | 11 コピーの repair ループ（1+2・break・warning 受理） | parse / fallback / 前後処理（agent 固有） |
| `agents/validation.py::ValidationIssue` | `src/` | 22 コピー | — |
| `agents/cartridge_loader.load_cartridge_or_none` | `src/` | 12 コピーの `_load_cartridge` | — |
| `core/llm_worker/system.py::WorkerSystem / CostSpec` | backend | 7系統の client 生成・gate・run_with_repair 結線・thread 起動 | SQL・冪等マーカー・トリガ・専用ゲート |
| `core/llm_worker/chat_turn.py::structured_turn / build_turn_messages / spoken_variant` | backend | W層2者 + 教材図の会話ターン骨格・`stance_label` | grounding の中身・注釈生成・権限 |
| `core/llm_worker/repair.py::run_with_repair(call=)` | backend | `complete_json` 以外の呼び出し形（additive） | 既存挙動 |
| `core/llm_worker/single_shot.py::extract_json / json_call / structured_call` | backend | JSON 抽出 13通り・降格対2箇所 | `usage_context` / CostGate / モデル解決（所有者が別） |
| `core/llm_worker/embedding.py::embed_with_context` | backend | embed ラッパ3箇所 | — |
| `api/quota.py::consume_daily_quota` | backend api | quota ラッパ | 各経路の 429 文言 |

原則: **テストが patch する module 属性名・source-text ガードレールが grep する文字列は全て保持**し、
既存ファイルは薄いシムとして残す（`CartridgeContext` の再エクスポート方式と同じ）。
LLM 関数は共有ヘルパーが import して呼ぶのではなく、呼び出し側モジュールの属性を注入する
（`patch("api.routes.learning.generate_text")` 等の seam を壊さないため）。

## 4. 意図的に分けたまま残すもの（再統合の禁止）

1. `agents/llm_step.run_repair_loop`（例外 break・`generate(messages)`）と
   `core/llm_worker/repair.run_with_repair`（例外 continue・`complete_json(str)`）は**別物**。
   discuss_opening / landscape_placement を `llm_step` に寄せると完了済みの統合からの退行になる。
2. apparatus `iterative.py`、学習チャット本体、compare、course draft マーカー、`lecture.py` の
   429 リトライは独自仕様のまま。
3. モデル解決の3様式（scene 解決 / `get_llm_params(tier)` 明示 / 意図的明示）のうち、
   `api/routes/learning.py` の `get_llm_params(tier)["model"]` 明示渡しは M層の解決順序を迂回する
   **挙動上のギャップ**だが、本リファクタでは変更しない（挙動変更のため別件）。
4. admin の schema-proposals analyze / simulate への日次ゲート追加も別件（新しい 429 挙動）。

## 5. 分類の総括

| 系統群 | A（共通化） | B（アダプター） | C（独自） |
|---|---|---|---|
| パイプライン agent（22） | 4 | 7（+1 hybrid） | 11（LLM 2 + 非LLM 9） |
| 非同期 worker（7） | glue 4種 | agent.py（skip 述語） | SQL・冪等・トリガ |
| チャット型（11） | 3 | 5 | 3 |
| 単発ヘルパー（34地点） | パーサ・降格・embed・quota | 一部パーサ | lecture retry / compare |

## 6. 実装記録（2026-09-10、同日実施）

Fable 5.1 が §3 の設計を確定し、Opus 5 の4体（WS-A〜D）が非重複のファイル所有で並列実装した。
すべて **観測可能な挙動は不変**（下記「意図した挙動差」を除く）。テストが patch する module 属性・
source-text ガードレールが grep する文字列は全て保持し、既存ファイルは薄いシムとして残した。

### 6.1 WS-A: パイプライン agent（`src/`）
- 新設 `agents/llm_step.py`（`run_repair_loop` / `has_errors` / `error_issues` / `attach_issues` /
  `MAX_REPAIR_ATTEMPTS`）。契約（初回1コール + 最大2修復・例外で break・warning 受理・fallback は
  呼び出し側）を docstring と 13 テストで固定。`core/llm_worker/repair.run_with_repair` と統合しない
  理由を docstring に明記。
- 10 repairer（A: paper_skeleton / thesis_reconstruction / narrative_annotator / component_graph、
  B: rhetorical_role / claim_qualification / dsl_linking / equation_semantics / apparatus one-shot /
  component_assembly）が `run_repair_loop` に委譲。dsl_linking の事前短絡は runner の前に残置。
  component_assembly の diagnostics キーは `on_attempt_*` フックで従前どおり（移行前に snapshot
  テストを追加してから移行）。
- `agents/validation.py::ValidationIssue`: 22 コピー → 17 が再エクスポート、component_assembly は
  サブクラス（`target_type`/`target_id` 追加）、document_structure / document_unit_boundary /
  discuss_opening / landscape_placement の4件はフィールド構成が異なるためローカル維持。
- `cartridge_loader.load_cartridge_or_none`: 13 コピーが委譲（`backend/tests/test_material_domain_entry.py`
  が各 `_load_cartridge` 本体の `if not cartridge_id: return None` を grep するため、その2行は残置）。
- `equation_semantics/vision_probe.py` 削除（参照ゼロ）。
- **保留**: contextual_explanation のバッチ・部分受理ループは別アルゴリズム（1呼び出し元）のため
  `run_item_repair_loop` を作らず、ガードレールの唯一の免除として固定。
- ガードレール `test_repair_loop_guardrails.py`: repair.py が独自 `for attempt in range(` を持たない
  （免除: discuss_opening / landscape_placement / apparatus iterative / contextual_explanation）。
- 行数: repair.py 合計は +60（閉包と説明コメント）、パッケージ全体はほぼ横ばい。**利得は意味的**
  （制御フロー 11→1・ValidationIssue 22→1・_load_cartridge 13→1）。src 1,828 → 1,859 pass。

### 6.2 WS-B: 非同期 worker（backend）
- 新設 `core/llm_worker/system.py`（`CostSpec` / `WorkerSystem`: `.gate` / `.client()` / `.run()` /
  `.check_and_count(settings=, gate=)` / `.spawn(thread_factory=)`）。settings と gate は呼び出し側が
  渡す（`worker.get_settings` / `worker._cost_gate` の monkeypatch seam を保持）。`thread_factory` は
  worker モジュール自身の `threading.Thread`（`worker.threading.Thread` の patch を保持）。
- 7系統に `core/<系統>/system.py`（`SYSTEM = WorkerSystem(...)`）。llm_client / repair / agent は
  シム（dead な `resolve_model()` / `parse_json_response` 再エクスポートは削除）。
- 挙動是正: reconstruction に `prune_stale_daily=True`（日付キーの無限成長）。7 launcher すべて
  名前付き daemon thread + try/except + `bool` 戻り（standardization の `None` 戻りも揃えた）。
  tension / anchor は日次キーが `(user_id, date)` のため prune を **有効化しない**（他ユーザーの
  カウンタを消すため）。
- ガードレール `test_llm_worker_guardrails.py` を 5→7 系統に拡張 + `WorkerSystem` 1つ/系統・
  `SYSTEM.spawn` 経由のみ・`CostGate()` 再インスタンス化禁止。
- 行数: 既存 glue −51、新規 +381（うち共有 216）。**目標の −400 は未達**（シムの残量は
  ガードレール・seam が名前で固定している分そのもの）。利得は「方針の単一供給源」（prune 意味論・
  gate 結線・log_label・thread 命名・起動時例外処理）。

### 6.3 WS-C: 会話ターン（backend）
- 新設 `core/llm_worker/chat_turn.py`（`MATH_DELIMITER_INSTRUCTION` / `SPOKEN_CONTRACT` /
  `spoken_variant` / `build_turn_messages(inject=first_user|current_user|none)` / `TurnResult` /
  `structured_turn(call=, model=callable 可)`）。旧 `build_llm_messages` 2本との出力一致は
  移行前実装のコピーとの比較テストで固定。`spoken_variant` は `model_json_schema()` 一致を検査。
- `dialogue.run_turn` / `graph_dialogue.run_graph_turn` / `teaching_figures.generator.run_figure_turn`
  が委譲。`stance_label` は `TurnResult` に載り route は `result.stance_label or AI_READING_LABEL`。
- `core/llm_worker/repair.py::run_with_repair(call=)` を additive 追加。
- 意図した挙動差: ①`api/routes/atlas.py` の 429 文言から数値を除去（I2）②教材図 studio の二重
  `window_history` を解消（route 側を撤去。`head_keep=1` の generator 側のみ）。
- **アダプター維持**: `teaching_figures/suggest.py`（行数が多い方を採るタイブレーク）と generator の
  SVG repair（message list 拡張・sanitize 検証・2回目拒否は degraded=False）は `run_with_repair`
  の契約と合わないため独自のまま（LLM 呼び出し自体は `structured_turn` 経由に統一）。

### 6.4 WS-D: 単発ヘルパー・デッドコード（backend）
- 新設 `core/llm_worker/single_shot.py`（`extract_json` / `strip_code_fence` / `json_call` /
  `structured_call` / `LLMSingleShotError`）、`core/llm_worker/embedding.py::embed_with_context`、
  `api/quota.py::consume_daily_quota`。LLM 関数は注入（`_UNSET` センチネルで「渡されたものだけ転送」—
  `model=None` を明示転送する既存テストと `model` 非転送を要求する既存テストの両方を満たす）。
- 移行: P1 / P3 / P4（降格対ごと）/ P5（raise 維持）/ P6 / P7 / P8 / P9 / P11 / P12 / P15（マーカー
  protocol は残置）+ embed ラッパ3箇所 + quota ラッパ（_shared / learning / admin / teaching_figures）。
- 削除: `core/chat.py`、`core/graphs/`（student_graph / state・LangGraph 依存 `langgraph` も
  requirements から除去）、`extract_theory_components_from_chunk`、`generate_missing_link_suggestions`、
  `routes/theory_components.py` の未配線リトライ骨格 + 重複 `_parse_json_object`。対応テストは
  削除対象部分のみ除去。
- ギャップ是正: `_generate_graph_element_explanation` の LLM 失敗を 500 → degraded 200（本体と同型）。
  `usage_context` 追加: `admin:schema_analysis` / `admin:schema_simulate` / `learning:lecture_interrupt`
  （`llm_policy` の scene 対応も追加。既定 tier は不変）。
- ガードレール `test_llm_single_shot_guardrails.py`（自前フェンス除去の禁止・`strict=False` の
  二重化禁止・注入規約・削除物の再出現禁止）。
- 行数: 本番 −388（既存 −828 / 新規 +440）。

### 6.5 統合（Fable）
- `core/llm_worker/__init__.py` に新4モジュールを公開。教材図 route の quota ラッパを `api/quota.py`
  へ委譲。`langgraph` 依存を削除。CLAUDE.md（`core/chat.py` 行撤去・`llm_worker` 節更新・
  横断基盤節に新モジュールと接続規律）と docs（core-engine / overview / rag-chat / 調査記録の
  解消注記）を更新。

### 6.6 意図的に残した follow-up（本リファクタの範囲外＝挙動変更）
1. `api/routes/learning.py` の `get_llm_params(tier)["model"]` 明示渡し（M層解決順序の迂回）。
2. lecture interrupt chat への日次 quota（`learning.py` ↔ `routes/lecture.py` の import 循環のため
   gate の置き場を決める必要がある）と admin schema analyze / simulate の quota。
3. `chat_sessions` / `chat_messages` テーブル（DB 側デッドコード）。
4. contextual_explanation のバッチ repair ループの共通化（1呼び出し元のため見送り）。
