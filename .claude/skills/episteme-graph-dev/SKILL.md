---
name: episteme-graph-dev
description: >
  Episteme GraphのバックエンドAPI、データベース操作、ルーティングの実装や修正を行う際に使用します。
  ユーザーから「APIを追加して」「DBのスキーマを変更して」「コードをリファクタリングして」
  「エンドポイントを修正して」「テストを書いて」などの開発タスクを依頼された場合に自動的に発動してください。
---
# Episteme Graph — 開発支援スキル

## 概要

大学院生の学習プロセスを支援する知識グラフ管理システムの開発を支援するスキル。
PDF教材からの知識抽出、RAGベースの対話型学習、コース管理の開発ルールを定義する。

## アーキテクチャ

### FastAPI バックエンド（backend/）

| モジュール | 役割 | 主要ファイル |
|---|---|---|
| `backend/api/main.py` | FastAPI アプリ本体 (lifespan, CORS, ルーター統合) | エントリポイント |
| `backend/api/routes/auth.py` | 認証ルーター | 登録・ログイン・ユーザー情報 |
| `backend/api/routes/learning.py` | 学習ルーター | コース管理・RAGチャット（intent_mode: on_path/explore/casual/**discuss**（論文と話す。予約疑似トピック `_discussion`・`discuss_scope` 2段スコープ・無断フォールバック禁止 DM1）・進捗・tension 確定 API・structure_anchor 確定 API（`/api/learning/anchors/...`）・音声会話（`/voice/transcribe`=Whisper STT, `/voice/speak`=TTS）・discuss 開幕画面（`GET .../discuss/opening`、非LLM読み取り専用）。RAG の全域検索は `search_chunks_with_metadata(..., allowed_document_ids=...)`（必須キーワード引数）で可視性を強制 — 可視集合の正本は `services.list_visible_document_ids` / コース sources 解決は `services.list_course_source_document_ids`。チャンク直読み `GET .../source-chunk/{chunk_id}`（`get_chunk_passage(..., allowed_document_ids=)` 必須kw引数）と claim-refs（コース sources ∪ 本人可視の複合判定）も同じ可視集合で fail-closed |
| `backend/core/discuss/` | discuss モード（論文と話す）コア | `opening.py` = 開幕画面投影（thesis_reconstruction artifact + theory_component_graphs main 層 + open-assumptions。非LLM・読み取り専用・confidence 生数値非漏洩）/ `observation.py` = 観測基盤（Phase 3 実測ゲート用の集計・参考目安判定・ダンプ構築・HMAC 仮名化。正本は `docs/features/discuss_observation_design.md` DO1〜DO6: **本文非含有・仮名化・学習者に数値非表示・削除APIなし・目安は自動ゲートにしない・計測失敗でUXを止めない**）。正本は `docs/features/discussion_mode_design.md`（DM1〜DM8）。**FastAPI を import しない** |
| `backend/api/routes/discuss_observation.py` | discuss 観測基盤ルーター（migration 060 `discuss_metric_events`） | `admin_router`（SYSTEM_ADMIN のみ）: `GET /api/admin/discuss/observation-status`（蓄積状況 + 分析開始の参考目安）/ `GET /api/admin/discuss/observation-dump?format=tar.gz\|zip`（本文非含有・仮名化ダンプ。取得は `theory_review_events` `entity_type='discuss_observation'` に監査）。`learning_router`: `POST /api/learning/discuss/metric-events`（UI イベント取込。イベント語彙13 + payload キー・値のホワイトリスト（許容 enum 外の値はキーごと黙って捨てる）、fire-and-forget 前提）。main.py にフラット登録（Tier 3-17c） |
| `backend/api/routes/admin.py` | 管理ルーター | 教材アップロード・コースビルダー・ユーザー管理。教材一覧/詳細の status は `core/status/projector.py` の導出 + legacy 語彙マッピングを使う（独自 JOIN で status を再合成しない、Tier 3-16）。admin 系子ルーター13本は admin.router に include せず main.py が `prefix="/api/admin"` でフラット登録する（Tier 3-17c） |
| `backend/api/routes/lecture_studio/` | 原稿スタジオルーター（パッケージ） | Tier 3-17a で `_shared.py`（境界横断ヘルパー）/ `scripts.py`（原稿・音声）/ `pipeline.py`（Agent Pipeline 実行）/ `topics.py`（コース構造・トピック編集）に3分割。`__init__.py` が router と互換シンボルを再エクスポート（import 面は旧 `lecture_studio.py` と同じ） |
| `backend/api/routes/atlas.py` | 分野の地図（骨格・報告・導線） | 骨格の生成/レビュー/凍結（教員）、修正報告（`POST /api/atlas/report`）、見晴らしの導線の内部計測と初回自動表示フラグ（`/api/learning/atlas/cues/...`、migration 026 `atlas_cue_events`）、骨格エディタ AIアシスト編集（`POST .../atlas/skeleton/assist/interpret|propose`。意図解釈→教員確定→JSON Patch 提案の2段階、draft は書き換えない。コスト上限 `ATLAS_ASSIST_MAX_CALLS_PER_DAY`、監査 `entity_type='atlas_assist'`） |
| `backend/api/routes/atlas_view.py` | 分野の地図（閲覧） | `GET /api/atlas`（骨格+`atlas_overlay_cache`+個人層合成）・`GET /api/atlas/node/{id}`。状態判定はサーバ側 `core/atlas_state.py` のみ |
| `backend/api/routes/doubt.py` | D層（Doubt Layer）ルーター | 認識的地位台帳（記帳/閲覧, `/api/admin/doubt/ledger/...`）・暗黙前提の確定フロー・疑義/検証提案・反実仮想セッション・前提の地図・naive-signals（k-匿名）・KPI（SYSTEM_ADMIN）。学習者向け読み取りは `learning_router`（`/api/learning/courses/{id}/ledger/...`・`open-assumptions`）。全書き込みを `theory_review_events` に監査。**load_score・confidence の生数値をレスポンスに含めない**（段階ラベルのみ） |
| `backend/api/routes/admin_assistant.py` | 横断ユーティリティ層（Admin Copilot, migration 034）ルーター | 管理画面 統合 AI アシスタント。`POST /api/admin/assistant/chat`（intent 分類 → guidance / locate / action / clarify）・`POST .../actions`（代行実行）・`POST .../actions/{id}/revert`（取り消し）・`GET .../actions`（戻す履歴）。admin.router 配下（prefix `/assistant`）。capability registry で role×screen を fail-closed 判定、代行・取り消しを `theory_review_events` `entity_type='assistant_action'` に監査。**A/B/C/D 層のコードは変更せず既存 API を呼ぶ側**（P7） |
| `backend/core/admin_assistant/` | Admin Copilot コア（横断ユーティリティ層） | `capabilities.py`（capability registry＝単一の真実源。role/screen/reversible/confirm/locate_steps）/ `knowledge.py`（`docs/admin_operations/*.md` の操作KB ローダ + role/screen フィルタ）/ `intent.py`（LLM intent 分類＋応答。失敗時は非LLMヒューリスティックへ縮退）/ `actions/`（tool 実装 = capture_before/apply/revert。apply は既存 API を呼ぶだけ）/ `action_store.py`（`assistant_actions` I/O）/ `schema.py`（Capability / Intent 等の dataclass・Pydantic）。**FastAPI を import しない**（core の testability） |
| `backend/core/admin_assistant/next_steps.py` | G層（ガイダンス層, migration 039）Next Steps エンジン | 「次にやること」To-Do をサーバ状態（教材・解析 run・コース・binding・公開・音声キャッシュ）から**毎回決定論的に導出**（G1 完了フラグなし / G2 非LLM同期）。ルールカタログ v1 = 6件、capability registry 参照でロール fail-closed（G3）、上限10件+`truncated`。dismiss は `assistant_step_dismissals` の `revoked` 遷移で保持（G5）。API は `GET /api/admin/assistant/next-steps`（`{steps, hidden, truncated, assistant_cue_pending}`）+ dismiss/restore（監査 `entity_type='next_step'`）。**FastAPI / LLM を import しない** |
| `backend/api/routes/reconstruction.py` | 再構成ループ（R層, migration 036）ルーター | 学習者向け（`learning_router`・本人のみ・受講ゲート `get_accessible_course_data`）: 出題取得（`GET /api/learning/courses/{id}/topics/{tid}/reconstruction/next`、伏せフィールド非返却）・提出→DIFF→リビール（`POST .../{item_id}/submit`）・自己確認（`.../{recon_id}/self-check`）・記号葉降下（`.../descend`）・再挑戦（`.../{item_id}/revise`）。教員向け（`admin_router`・`_require_teacher`）: review キュー・item status 遷移（削除 API なし）・手動オーサリング・つまづきサマリー（`GET /api/admin/documents/{id}/claims/stumble-summary`）。実行時 DIFF は非LLM同期、判定は仮説・権威は出典リビール、点数非表示。監査 `theory_review_events`（`reconstruction_item`/`reconstruction_response`）。**A層は読むだけ** |
| `backend/core/reconstruction/` | 再構成ループ コア（R層） | `schema.py`（語彙・伏せフィールド・承認語彙の正本）/ `item_builder.py`（predict 可否・restate 縮退・learner 伏せ・記号降下プローブ。非LLM）/ `diff.py`（構造照合 + REFLECT 事実文。非LLM同期）/ `prompt.py`・`input_builder.py`・`llm_client.py`・`validator.py`・`repair.py`（item オーサリング。LLM は選択肢・expected のみ、2回修復失敗で item 非生成）/ `worker.py`（オーサリング worker。トリガー: claim 承認フック / 手動バッチ、冪等性=非 retired item の有無）/ `health.py`（疑わしさランクをアプリ側計算）/ `stumble.py`（claim 単位つまづき集約・k=3 匿名）。**FastAPI を import しない** |
| `backend/api/routes/library.py` | ナレッジライブラリ（L層, migration 042）ルーター | 実パス `/api/admin/library/...`・`_require_teacher`。エントリ CRUD（draft 編集は `expected_revision` 楽観ロック・衝突 409）・凍結版発行・retire/restore（行削除 API なし）・domain サマリ・類似検索（`POST /entries/similar`）。全書き込みを `theory_review_events` `entity_type='library_entry'` に監査。昇格は人間の操作のみ（LLM 直接書込経路なし） |
| `backend/core/library/` | ナレッジライブラリ コア（L層） | `schema.py`（entry_type=`apparatus`/`theory_component`・status 語彙）/ `store.py`（draft 正本 CRUD + revision 楽観ロック + 凍結版 append + retire/restore。DELETE 文を書かない）/ `search.py`（**凍結版のみ**の pgvector 検索 + 類似提示。draft/retired を読まない）/ `seed.py`（`backend/cartridges/<id>/library/*.json` の冪等シード取込）。atlas_skeletons パターン踏襲。**FastAPI を import しない** |
| `backend/core/document_pipeline/figure_images.py` | 図画像抽出ステージ（migration 041/051、非LLM・常時実行） | PyMuPDF 埋め込み画像 + caption 領域レンダリング fallback → MinIO `figure-images` + `document_figures`（upsert）。図単位失敗は `status='failed'` で非致命。図領域内テキストスパンを `_extract_inner_labels` でグルーピングし `document_figures.inner_labels JSONB` に保存（migration 051、装置図のパーツ列挙グラウンディング用） |
| `backend/core/document_pipeline/figure_context.py` | 図ごとの文脈収集（非LLM・純関数） | `collect_figure_context(structure, figure_row, inner_labels=...) -> FigureContext(nearby_text, abbreviations, source_counts)`。caption 直近 → `Fig. N.M` 参照メンション ±1 → セクション本文の優先度収集 + 略語辞書（initials 検査・inner_labels で絞り込み）。上限 `APPARATUS_CONTEXT_MAX_ITEMS`/`_CHARS`。apparatus_semantics ステージの入力配線に使用 |
| `backend/api/routes/deliberation.py` | W層（要素検討ワークスペース）ルーター | 実パス `/api/admin/deliberation/...`・全経路 `_require_teacher`。要素 overview（面①内訳+面②位置づけ）・同一性リンク（candidate→confirm/reject）・対話セッション/候補注釈（commit/dismiss）・標準化判定バッチ起動・**要素インベントリ**（`GET /documents/{document_id}/elements` — 4要素型の統一カード一覧。`resolve_document_access` で material_id/UUID を正規化 + 閲覧 fail-closed。正本は `docs/features/element_inventory_design.md`） |
| `backend/core/deliberation/` | W層 コア | `schema.py`（ElementRef・語彙の正本）/ `refs.py`（要素解決）/ `decomposition.py`・`positioning.py`（面①②、非LLM）/ `dialogue.py`（対話 1応答=1コール）/ `annotations.py`（候補注釈・コミットルーティング）/ `identity_links.py` / `standardization/`（llm_worker 6系統目）/ `inventory.py`（**要素インベントリ**: document 単位の component/claim/equation/figure 統一カード一覧 + 検討状況集約。非LLM・読み取り専用・confidence 生数値を落とす・型別上限500+`truncated_types`）/ `context_lens.py`（**要素中心コンテキストレンズ #498**: 選択要素を中心に上位(Why)/下位(How)を各1階層だけ決定論的に投影し overview の `context` キーに載せる。非LLM・読み取り専用・レーン上限20+`notes`）。**FastAPI を import しない** |
| `backend/api/routes/atlas_gaps.py` | カテゴリギャップ候補（migration 066）ルーター | `_require_teacher`・main.py フラット登録。`GET /api/admin/cartridges/{id}/atlas/gap-candidates`（**読み時導出**キュー: active 信号の cluster 化 → distinct document ≥ 2 → 現行凍結版で解消済み除外 → dismissed 抑止）・`POST .../gap-candidates/decide`（accept / dismiss（理由必須 422）/ restore — 行削除しない）・`POST .../gap-candidates/incorporate-preview`（決定論 JSON Patch プレビュー。**draft を書かない**）・`POST .../gap-candidates/mark-incorporated`。骨格複製は `POST .../atlas/skeleton/draft/from-frozen`（routes/atlas.py 側・決定論・LLM 不要）。監査 `entity_type='category_gap'`。学習者 DTO に gap 語彙を出さない |
| `backend/core/atlas_gaps/` | カテゴリギャップ候補 コア | `schema.py`（語彙・`normalize_label`・`build_cluster_key`（**版非依存** `gap\|{domain_key}\|{parent_region_id}\|{normalized_label}`）・`MIN_DOCUMENTS_FOR_CANDIDATE=2` の正本）/ `store.py`（signals の LS3 同型 supersede・空入力 SQL 非発行・decisions upsert。**DELETE FROM を書かない**）/ `patching.py`（決定論 JSON Patch 生成・**op=add のみ**・満杯領域は専用例外）。**FastAPI を import しない**。gap 系コードから `atlas_skeletons` への INSERT/UPDATE 不在をガードレールで証明（LS7）。正本は `docs/features/category_gap_candidates_design.md` |
| `backend/api/routes/llm_usage.py` | U層（LLM 使用量推計, migration 043）ルーター | `GET /api/admin/llm-usage/metrics`（SYSTEM_ADMIN のみ。reported/estimated 分離集計 + dropped_events + cost_usd、価格表未設定なら null）・`GET /api/admin/llm-usage/estimate/documents/{id}`（TEACHER・`_ensure_document_viewable`・トークンレンジのみ返す。点推定・金額なし）。削除 API は作らない（append-only） |
| `backend/core/llm_usage/` | U層 コア（LLM 使用量計測） | `schema.py`（UsageEvent・operation/usage_source/feature 語彙の正本）/ `context.py`（`usage_context()` contextvars 帰属）/ `estimator.py`（決定論的トークン推計。tiktoken optional・CJK×1.0+その他/4 ±40%）/ `recorder.py`（bounded buffer + flusher thread。`record()` は例外を漏らさない）/ `observe.py`（core/llm.py から呼ばれる薄い観測関数。reported usage 抽出 → 無ければ推計）/ `pricing.py`（外部 JSON 価格表。ハードコード禁止）/ `metrics.py`（集計）。**FastAPI を import しない** |
| `backend/core/config.py` | 設定一元管理 | pydantic-settings による環境変数管理 |
| `backend/core/llm.py` | LLM 抽象化レイヤー | Reasoning モデル自動対応。`generate_structured_with_images()`（vision structured output、v1 は OpenAI 経路のみ） |
| `backend/core/models.py` | SQLAlchemy ORM モデル | 全テーブル定義 |
| `backend/core/schema.py` | Pydantic スキーマ | ドメインモデル (PaperStructure 等) |
| `backend/core/extractor.py` | GROBID 変換 + diff/merge | PDF→TEI XML（orchestrator の下請け）と PaperStructure diff/merge のみ（旧抽出パイプラインは 2026-07 削除済み） |
| `backend/core/embedder.py` | ベクトル検索 | PostgreSQL pgvector |
| `backend/core/chat.py` | tier 付き chunk 検索 | 実 RAG チャットは `routes/learning.py::learning_chat` |
| `backend/core/postgres.py` | PostgreSQL セッション管理 | SQLAlchemy セッション |
| `backend/core/storage.py` | MinIO ストレージ | S3互換ファイル管理 |
| `backend/core/migrations.py` | マイグレーションランナー | `backend/db/*.sql`（init.sql + 番号順ファイル群）が唯一の正本。トラッキングテーブルを持たず、毎起動・番号順に全ファイルを冪等再実行する（pg_advisory_lock で多重起動排他・ファイル単位トランザクション）。`main.py` にはエンドポイント以外の DDL を書かない |
| `backend/core/llm_worker/` | 非同期 LLM worker 共通基盤 | BaseJSONLLMClient / run_with_repair / CostGate。tension・structure_anchor・reconstruction・doubt×2 の5系統が利用。新 worker はコピペせずここに接続 |
| `backend/core/privacy.py` | k-匿名ゲートの正本 | K_ANONYMITY=3・件数レンジ導出（3-5/6-10/11+）。k=3 のリテラル再定義禁止 |
| `backend/core/course_data.py` | `learning_courses.data` の正本スキーマ | CourseData 系 Pydantic モデル（全て `extra="allow"`）+ アクセサ（`course_topics`/`iter_all_topics`/`course_sources`/`course_source_material_ids`/`course_cartridge_id`/`course_title`/`lecture_studio_settings`/`find_course_topic`/`course_atlas_binding_facts`）。**course_data への素の dict アクセスを新規に書かない**（Tier 3-18） |
| `backend/core/revision_store.py` | draft/freeze/楽観ロック共通プリミティブ | `RevisionConflictError`（共通基底）/ `update_with_revision_lock` / `idempotent_seed_import`。atlas_store・library/store が委譲。第3の draft/freeze 利用者はコピペせずここに接続（Tier 3-20） |
| `backend/core/document_pipeline/orchestrator.py` | 解析パイプライン orchestrator | 26ステージは `_stage_<name>(ctx)` 関数 + `_PIPELINE_STEPS` リスト + `PipelineContext` で構成（Tier 3-19）。新ステージはリスト登録で追加し、インライン展開に戻さない |
| `backend/core/notification_recipients.py` | 通知宛先解決の共通 JOIN | status 系 / V層 versioning が利用（宛先集合の方針は各層に残す） |
| `backend/core/tension/` | TensionMiningAgent (B層) | 会話からの違和感候補検出（prefilter=同期非LLM / agent=非同期LLM / validator・repair / worker）。候補は `interest_traces` kind='tension' status='candidate' に保存し、学習者本人の confirm/dismiss API（`/api/learning/tension/...`）で確定。教員へは k-匿名化集約のみ |
| `backend/core/structure_anchor/` | StructureAnchorAgent (B層) | 学習チャットの問いを「構造のどこに・どう引っかかったか」へ帰属（agent=非同期LLM / validator・repair / worker、tension と同型の独立モジュール）。候補は `interest_traces.payload.structure_anchor` に `attribution_source='llm_candidate'` で保存し（行 status は変更しない）、学習者本人の confirm/dismiss API（`/api/learning/anchors/...`）で確定。明示アンカー（テキスト選択・要素タップ）は同期・非LLMで `learner_selected` 記録。教員へは k-匿名化集約のみ |
| `backend/core/doubt/` | D層（Doubt Layer, migration 029〜033） | 認識的地位台帳。`schema.py`（語彙の正本）/ `ledger_builder.py`（A層成果物からの非LLMバックフィル。directly_verified は人間専用）/ `naive_signal.py`（k=3 匿名集計）/ `dependency.py`+`load_calculator.py`（下流到達可能性・閉路対応）/ `counterfactual.py`（3区分伝播。再構築は計算しない）/ `scope_candidates/`・`assumption_mining/`（tension と同型の非同期LLM worker。出力は常に candidate、確定は教員API）/ `open_assumptions.py` / `metrics.py`。A層コードは読むだけで変更しない |
| `frontend/public/js/app.js` | 学習 UI | ES6+ SPA。D3-6: 出典タブ・コンポーネント説明への台帳事実行の併記（fail-closed）・未検証合意リスト（プル型） |
| `frontend/public/js/reconstruction.js` | 再構成ループ 学習 UI（R層・ES6+・`window.Reconstruction`） | トピック学習ビュー下部「再構成に挑戦」カード。ELICIT→CAPTURE→REVEAL→SELF-CHECK(必須)→REVISE/DESCEND。自動では開かない（P7）。app.js の `selectTopic` が `setContext(courseId, topicId)` を配線 |
| `frontend/public/js/admin.js` | 管理 UI（原稿スタジオ以外） | ES5互換 Vanilla JS。原稿スタジオは admin-lecture-studio.js に分離済み（Tier 3-17b） |
| `frontend/public/js/admin-lecture-studio.js` | 原稿スタジオ UI（ES5・`window.LectureStudio`） | admin.js から分離した `ls` 接頭辞の関数群（約6,300行）。公開 API は `init({apiFetch, apiFetchRaw, escHtml, onTabActivate, state})` / `openExportModal` / `getScreenContext`。R層: 右ペインの `根拠リンク ⇄ つまづき` トグル（`lsState.rightPaneMode`、別タブにしない）+ review キューのインライン確認・item retire/confirm。読み込み順は doubt-atlas.js より後・admin.js より前 |
| `frontend/public/js/doubt-atlas.js` | D層 管理 UI（ES5） | 前提の地図タブ・台帳セクション（ノード詳細ペインへ追記）・前提候補レビュー・反実仮想モード。**Field Atlas（atlas-*.js）とは別機能** — 識別子/CSS は doubt- プレフィックス |
| `frontend/public/js/admin-assistant.js` | Admin Copilot 管理 UI（ES5・`window.AdminAssistant`） | 全タブ横断の常設フローティングパネル。チャット（`/api/admin/assistant/chat`）・道案内の点灯（`runLocatePlan` + `.admin-assistant-spotlight`）・戻す（`ActionStack` L1 ローカル/L2 サーバ revert）。各画面は `registerScreenContext`/`registerUiAnchors`/`registerUndoHandler` で状態・点灯先・Undo を注入。識別子/CSS は `admin-assistant-` プレフィックス（Field Atlas / Doubt Atlas と非衝突） |
| `frontend/public/js/admin-next-steps.js` | G層 管理 UI（ES5・`window.AdminNextSteps`） | ヘッダー `📋 次にやること` バッジ + severity 別パネル。`[案内する]` は `AdminAssistant.runLocatePlan(step.locate_plan)` を呼ぶだけ（G8 spotlight 二重実装しない）。自動で開かない・ポーリングしない（G4）。再取得はタブ切替・教材アップロード/コース登録/公開/binding 保存の成功後のみ。初回ログイン cue（🤖 pulse）は `assistant_cue_pending` 一度きり・fail-closed。識別子/CSS は `admin-next-steps-` プレフィックス |

### PDF解析Agentパイプライン（src/episteme_graph/）

ドキュメントアップロード後の処理をコース作成と切り離して実行するagent群。
FastAPIバックエンドとは**独立したPythonパッケージ**として実装する。

| モジュール | 役割 |
|---|---|
| `src/episteme_graph/agents/document_structure/` | DocumentStructureAgent (#216) |
| `src/episteme_graph/agents/paper_skeleton/` | PaperSkeletonAgent (#217) |
| `src/episteme_graph/agents/rhetorical_role/` | RhetoricalRoleAgent (#218) |
| `src/episteme_graph/agents/claim_qualification/` | ClaimQualificationAgent (#219) |
| `src/episteme_graph/agents/equation_semantics/` | EquationSemanticsAgent (#220) |
| `src/episteme_graph/agents/thesis_reconstruction/` | ThesisReconstructionAgent (#221) |
| `src/episteme_graph/agents/dsl_linking/` | DSLLinkingAgent (#222) |
| `src/episteme_graph/agents/component_assembly/` | ComponentAssemblyAgent (#223) |
| `backend/cartridges/<cartridge_id>/` | ドメインカートリッジ定義（全Agent共有） |

**重要**: agentの実装は `src/episteme_graph/` に置き、`backend/` には置かない。
ただしカートリッジファイル（`backend/cartridges/`）は両方から参照される。

## 技術スタックの現状

| レイヤー | 技術 |
|---|---|
| API フレームワーク | FastAPI (ルーター分割済み) |
| ORM | SQLAlchemy 2.x + pgvector |
| RDB + ベクトル | PostgreSQL 16 + pgvector (cosine, 次元数は `LLM_EMBEDDING_DIM` で設定) |
| ストレージ | MinIO (S3互換) |
| LLM | **マルチプロバイダ対応** (OpenAI / Gemini)。`LLM_PROVIDER` 環境変数で切替。詳細は下記参照。 |
| 認証 | JWT (HS256) + bcrypt |
| 設定管理 | pydantic-settings (core/config.py) |
| フロントエンド | Vanilla JS SPA + nginx |

### LLM マルチプロバイダ構成

`LLM_PROVIDER` 環境変数で LLM バックエンドを切り替える。

| `LLM_PROVIDER` 値 | バックエンド | 必要な認証 |
|---|---|---|
| `openai` (デフォルト) | OpenAI API | `LLM_API_KEY` または `OPENAI_API_KEY` |
| `gemini` | Google AI Studio (google-generativeai) | `LLM_API_KEY` または `GEMINI_API_KEY` |
| `google` | Vertex AI (google-cloud-aiplatform) | GCP ADC または `GOOGLE_APPLICATION_CREDENTIALS` |
| `gemini-vertex` | Vertex AI + generativeai SDK **(廃止予定)** | GCP ADC |

**重要**: `google` / `gemini-vertex` (Vertex AI) は非推奨。新規実装では `openai` または `gemini` を使用すること。

## 開発時の必須ルール

### 1. 設定の Config 化 — `os.environ` の直書き禁止

すべての環境変数は `core/config.py` の `Settings` クラスに集約されている。
他のモジュールで `os.environ` や `os.getenv()` を直接使用してはならない。

```python
# NG
import os
api_key = os.environ["OPENAI_API_KEY"]

# OK
from core.config import get_settings
settings = get_settings()
api_key = settings.openai_api_key
```

### 2. ルーターの分割 — `main.py` への直書き禁止

新しいエンドポイントは `backend/api/routes/` 配下の適切なルーターに追加する。
`main.py` にエンドポイントを直接定義してはならない。

- 認証系 → `routes/auth.py`
- 学習系 → `routes/learning.py`
- 管理系 → `routes/admin.py`（原稿スタジオ系は `routes/lecture_studio/` パッケージの該当モジュール）
- 新ドメインが必要な場合 → 新しいルーターファイルを作成

**ルーター登録はフラット構成**（Tier 3-17c）: 新しい admin 系ルーターは `main.py` に
`app.include_router(<router>, prefix="/api/admin")` で直接登録する。`admin.router` に
子ルーターを `include_router` でぶら下げる二段ネストを復活させないこと。

**`learning_courses.data`（course_data）へのアクセス**は `core/course_data.py` のアクセサを使う
（素の `course_data.get("topics")` 等を新規に書かない。Tier 3-18）。

### 3. LLM 抽象化レイヤーの利用 — ベンダ SDK の直接利用禁止

LLM の呼び出しは必ず `core/llm.py` の公開 API を経由する。
`extractor` / `chat` / `batch` 等のサービス層で以下のインポートを直接行ってはならない:

```python
# NG — ベンダ SDK をサービス層で直接インポートしない
import openai
from openai import OpenAI
import google.generativeai
from google.generativeai import GenerativeModel

# OK — 必ず core/llm.py の公開 API を使う
from core.llm import generate_text, generate_embeddings, generate_text_with_structured_output
```

`core/llm.py` は以下を内蔵:
- OpenAI Reasoning モデル (o1, o3, gpt-5.x) 向け自動変換:
  - `system` ロール → `developer` ロールへの変換
  - `temperature` / `max_tokens` の自動除去
- Gemini 向け自動変換:
  - `system` ロールを `system_instruction` として抽出し `GenerativeModel` に渡す
  - `user` / `assistant` ロールは `contents` として渡す

### 3-a. Google LLM を使う際の禁止事項

Google の LLM バックエンドを利用する場合、以下のルールを厳守すること:

| 区分 | 内容 |
|---|---|
| **禁止** | `google-cloud-aiplatform` (`vertexai` SDK) を新規サービスコードで使用すること |
| **禁止** | `LLM_PROVIDER=google` または `LLM_PROVIDER=gemini-vertex` を推奨設定として提案すること |
| **推奨** | `LLM_PROVIDER=gemini` + `google-generativeai` (Google AI Studio 版) を使用すること |

> **理由**: `google-cloud-aiplatform` (Vertex AI) は将来的な廃止を見越して意図的に非推奨としている。
> 新規実装・AIへの提案ではかならず `google-generativeai` (Google AI Studio) を使うこと。

### 4. データベース操作

- ORM モデルは `core/models.py` で定義 — テーブル追加・変更時はここを更新
- Pydantic スキーマは `core/schema.py` — ドメインモデルの定義
- API 固有のリクエスト/レスポンスモデルは各ルーターファイル内に定義
- PostgreSQL セッションは `core/postgres.py` の `get_session()` を使用し、必ず `try/finally` で `session.close()`
- **マイグレーションの正本は `backend/db/*.sql` のみ**（アーキテクチャ整理 Tier 3-13 で
  `main.py::_run_migrations()` のインライン DDL は撤去済み）。`backend/core/migrations.py` の
  ランナーが毎起動・番号順に全ファイルを冪等再実行する。
  - **新しいスキーマ変更は必ず新番号の SQL ファイルを追加する**（既存ファイルの編集は
    typo・冪等性修正など「最終状態の是正」に限り、過去に適用済みの意味を変えない）
  - **すべてのファイルは冪等に書く**（`CREATE TABLE IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` /
    `DO $$ ... $$` の存在確認ガード等）。毎起動再実行されるため非冪等な DDL は次回起動で
    失敗するか既存データを壊す
  - **`main.py` に DDL を書き戻さない**（正本を2箇所に増やさない）
  - 複数の旧テーブルを1枚に統合するマイグレーション（例: 044, 045）は、置換された旧ファイルを
    削除・空にはせず**コメントのみのスタブ化**に留める（毎起動再実行方式で「作って即壊す」
    往復を防ぐため）
  - ガードレールは `backend/tests/test_migrations_runner.py`（冪等性 lint・番号連続性・
    main.py への DDL 再侵入禁止）

### 5. フロントエンド

- `admin.js` は Vanilla JS (ES5互換) — `var`, `function` キーワードを使用
- `app.js` は ES6+ (const/let, async/await)
- フレームワーク不使用

### 6. 認証

- JWT トークンの署名鍵は `settings.jwt_secret` から取得
- 全 API エンドポイントで認証デコレータを使用
- RBAC: STUDENT / TEACHER / SYSTEM_ADMIN

### 7. アクセス制御と認可ロジックの実装ルール (新規追加)
資料（Document, LearningCourse 等）を取得・操作するエンドポイントを実装する際は、単に認証済みのユーザーであるかだけでなく、**開示範囲（Visibility）とグループ所属を検証する認可ロジック**を必ず組み込むこと。
- 自分が作成した Private なリソースか？
- 自分が所属している Group に許可されたリソースか？
- Public なリソースか？
これらの判定を行う共通の依存関数（Dependency）を `api/dependencies.py` に実装し、ルーターで再利用すること。

### 8. 多言語対応とTTS実装ルール (新規追加)
教材データからテキストを抽出、またはLLMで音読用スクリプト（`spoken_text`）を生成する際は、言語コード（例: `ja-JP`, `en-US`）を判定しメタデータとして保持する。TTS呼び出し時（`generate_tts_audio` 等）は、この言語メタデータを引数として受け取り、プロバイダの設定を動的に切り替えること（言語指定のハードコード禁止）。


## 実装前のドキュメント更新（条件付き必須）

以下に該当する変更を行う場合、**実装を開始する前に** 対応する CLAUDE.md または SKILL.md を更新すること。
AI アシスタントは各セッション開始時にこれらのファイルを設計書として読むため、実装前に更新することで
設計意図と実装が一致し、手戻りを防ぐ。

### 更新が必須のケース

| 変更の種類 | 更新対象 |
|---|---|
| 新しい Agent / エンドポイント / テーブルの追加 | 該当スキルの SKILL.md（アーキテクチャ表・ファイル構成表） |
| アーキテクチャや設計原則の変更 | CLAUDE.md および該当 SKILL.md |
| 新しい必須ルール・禁止事項の追加 | 該当 SKILL.md の「開発時の必須ルール」セクション |
| 環境変数・設定項目の追加 | `episteme-graph-knowledge/SKILL.md` の環境変数仕様表 |

### 更新が任意のケース

- 既存ロジックの小さなバグ修正
- リファクタリング（外部インターフェース変更なし）
- テストの追加・修正のみ

### 更新手順

1. 実装予定の変更内容を把握する
2. 影響する SKILL.md のセクション（アーキテクチャ表・ルール・パイプライン構成等）を特定する
3. 実装内容を反映した形でドキュメントを更新する
4. 更新後に実装を開始する

## CI テストパターンの更新（必須）

**機能の追加・更新を行った場合、必ず対応するテストパターンも追加・更新すること。**

実装完了後、`episteme-graph-ci-tests` スキルの手順に従い、以下を実行する:

1. 変更対象に対応するテストファイルが `backend/tests/` に存在するか確認
2. 不足しているテストパターンを追加（正常系・エッジケース・異常系）
3. 既存テストが変更後のコードと整合するか確認し、必要に応じて更新
4. `cd backend && python -m pytest tests/ -v` でテストが通ることを確認

テストの配置規則・コード規約・設計原則の詳細は `episteme-graph-ci-tests` スキルを参照。

## 動的参照ガイド

コード生成時は、以下のファイルを直接読み込んで最新の定義を確認すること:

- **ORM モデル (テーブル定義)**: `backend/core/models.py` を読んで確認
- **Pydantic スキーマ**: `backend/core/schema.py` を読んで確認
- **設定項目**: `backend/core/config.py` の `Settings` クラスを読んで確認
- **LLM API**: `backend/core/llm.py` の公開関数を読んで確認
- **既存ルーター**: `backend/api/routes/` 配下のファイルを読んで確認
- **テスト**: `backend/tests/` 配下を読んで既存テストパターンを確認
