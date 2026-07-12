# アーキテクチャ整理調査レポート（2026-07-12）

12機能領域の現状把握と、シンプル化・整理の提案。
調査方法: 6並列のコード実査（読み取り専用）。数値（LOC・箇所数・重複度）はすべて実測。

---

## 第1部: 現状把握 — 12機能とコードの対応

ユーザー提示の12機能を実装に対応付けた結果。★は調査で判明した重要な訂正・補足。

### 1. 教材処理のパイプライン
- **実体**: `backend/core/document_pipeline/orchestrator.py`（2,364行、26ステージ）が
  `src/episteme_graph/agents/`（実働17エージェント、最大は component_assembly 9,460行）を順に実行。
  成果物は `theory_claims` / `theory_components` / `theory_component_graphs` / `chunks`(pgvector) /
  MinIO(figure-images) + `document_analysis_runs.stage_outputs`（全ステージ生データも JSONB に二重保存）。
- ★ **旧パイプラインはデッドコードとして残存**（実行系統は分離済みだが削除されていない）:
  - `core/extractor.py` の主機能（`extract_paper_structure` / `merge_paper_structures` /
    `extract_abstraction_pattern`）→ 呼び出し元はテストのみ。低レベルの GROBID ユーティリティ
    2関数だけが orchestrator から再利用されている。
  - `core/graphs/teacher_graph.py`（LangGraph 版パイプライン）→ 呼び出し元ゼロ。
  - `core/batch.py`（Neo4j 保存）→ 呼び出し元ゼロ。**新パイプラインは Neo4j に書いていない**。
    `core/db.py` の Neo4j ドライバはこの死んだ経路専用 → **Neo4j 自体が実質未使用の可能性**（要確認）。
  - `core/chat.py::generate_chat_response`（MinIO `extracted-structures` から PaperStructure を読む
    設計）→ routes から呼ばれておらず、そもそも同バケットへの書き込み元がコードに存在しない。
    実際の RAG チャットは `routes/learning.py::learning_chat` の別実装。
  - `agents/graph_narrative/` は空ディレクトリ（`__pycache__` のみ）。
- ★ **cartridge_loader.py が12エージェントにほぼ完全コピペ**、`CartridgeContext` dataclass も
  10エージェントの schema.py で個別再定義。さらにカートリッジ読み込みは
  `core/cartridges.py::DomainCartridge` 系と agent 側 `CartridgeLoader` 系の**2系統に分裂**
  （パス解決のみ共有）。実在カートリッジは particle_physics の1つのみ。
- `llm_client.py` は10+エージェントで8行のサブクラス化のみ（基底 `ProviderJSONLLMClient` は共有済み）。
- agents/ 配下に「LLM 9点セット型」と「builder 単体型（非LLM）」の2流儀が混在。

### 2. 講義内容作成
- **実体**: 4段パイプライン =
  コースビルダー（`routes/admin.py` 内 約760行 + `course_builder_sessions`）
  → `core/course_content_builder.py`（1,301行、成果物→topics 投影 + student_material 草稿生成）
  → 原稿スタジオ（`routes/lecture_studio.py` 3,403行 = **バックエンド最大ファイル**）
  → レクチャー配信（`routes/lecture.py` 975行 + `core/lecture.py` 738行 + `core/tts.py`）。
- ★ **`learning_courses.data` JSONB は実質スキーマレス**: Pydantic 検証は API 境界のみ。
  `course_data.get("topics", [])` 等の素の dict アクセスが**15ファイル・40箇所以上**に散在。
  フィールドごとに保護の強さがバラバラ（`lecture_language` はヘルパー経由、
  `cartridge_id` / `topics` / `sources` は素のまま）。
- ★ **スライド分割ロジックが Python/JS に並行実装**: 設計書は「プレビューと配信で分割ロジックを
  共有すること」を必須要件と明記するが、実態は `core/lecture.py::split_slides`（配信）と
  admin.js `lsSplitSlides`（プレビュー、L9937-9987）の**手動同期の並行実装**。JS 側コメント自身が
  「backend と同じ意味論をクライアント側で再現する」と明言。
- ★ **音声準備完了の判定が二重実装かつ粒度不一致**: `core/status/projector.py` は chunk 単位、
  `routes/lecture.py::get_topic_audio_status` は slide 単位 + language 一致まで見る。
  一部スライドのみ音声があるケースで G層 To-Do と UI ボタン活性が食い違い得る。
- コースビルダーの LLM 出力（course_draft）はバックエンドでスキーマ検証されていない
  （正規表現ベースのマーカー抽出 → JS 側で組み立て）。

### 3. 権限管理（RBAC）
- **実体**: `api/dependencies.py`（116行）の JWT + `_require_teacher`（**190箇所/14ファイル**で参照）+
  `_require_system_admin`。判定の正本は `api/services.py` の9関数（約300行、view→edit→owner の
  入れ子委譲は正しく一元化）。core/ からの認可参照ゼロ = 層分離は良好。
- ★ **正本の周りに再ラップ層が3ファイル・8関数並存**: `theory_components.py`
  （`_ensure_document_viewable` 等、chunk ごとの3段フォールバックで N+1 クエリ + 404握りつぶし）、
  `versioning.py`（`_require_owner/_require_viewer`、命名・戻り値設計が非対称）、
  `groups.py`（services を使わない完全独立実装）。
- ★ **role 語彙が3系統並存**: JWT role（STUDENT/TEACHER/SYSTEM_ADMIN）/ group_members.role
  （admin/member）/ permission（viewer/editor）。"admin" が2系統に登場し grep 調査で誤認しやすい。

### 4. 共有の仕組み
- **実体**: ①レガシー単一グループ共有（visibility/group_id 列, migration 009）
  ②`course_group_permissions`（010）③`document_group_permissions`（035 — **コメント自身が
  「010の完全な移植」と明記する双子テーブル**）④C層 承認・引用（021）⑤V層 版管理+削除猶予（037、
  `object_type + TEXT id` のポリモーフィック設計）。
- ★ **V層は CLAUDE.md に一切記載がない**（実装 1,712行が主要文書から不可視）。
- ★ **通知宛先解決（所有者+editor）が2箇所で独立実装**: `core/status/notification_rules.py` と
  `core/versioning/notifications.py`。docstring で互いに「同型」と明記しつつ共通化されていない。

### 5. 教材管理における状態管理
- **実体**: 正本は `documents.status` + `document_analysis_runs`（pending/running/completed/failed +
  current_stage + stage_outputs + options）。migration 038 で `core/status/projector.py`
  （MaterialStatus 導出）+ `status_events`（watermark 方式 watcher、60秒周期）+
  `user_notifications` が実装済み。導出と保存の境界は設計どおり明確。
- ★ **projector が UI に接続されていない**: admin.js の教材管理タブは `/api/admin/materials` が
  documents×runs を**独自 JOIN で合成**した status を表示。`/api/admin/status/*` API は
  **フロントから一度も呼ばれていない**（grep ゼロ件）。projector は実質 G層専用の内部部品。
  → 同じ状態合成ロジックが2箇所（admin.py 内 SQL と projector.py）に並存。

### 6. ライブラリ（L層）
- **実体**: `core/library/`（1,484行）+ `routes/library.py` + migration 042。
  draft/revision(楽観ロック)/freeze パターンは `core/atlas_store.py` の**意図的コピー**
  （docstring に「踏襲する」と明記）。凍結版のみ retrieval 対象、retire のみ（削除なし）等の
  ガードレールは設計どおり。
- ★ draft/freeze/楽観ロック実装が atlas_store（4,103行の atlas 群の一部）と library で
  **2セット並存**。第3の利用者が現れたら再コピーされる構造。

### 7. 学習コースにおける状態管理
- **実体**: `learning_courses` の is_template / is_published / visibility 列 +
  CourseStatus 投影（projector、チェックポイント集合 = registered / script_status / audio_status /
  atlas_bound / published / shared）。enroll は `learning_states` 行を作るだけ
  （★ `cloned_from` によるクローン方式は migration 011 で**廃止済み** — CLAUDE.md の A2 記述は旧仕様）。
- ★ 「コースの何が完了しているか」の判定が `next_steps.py` / `projector.py` / `atlas.py` の
  3ファイルにそれぞれ独自の `course_data.get(...)` で書かれている。

### 8. ユーザー支援エージェント
- **実体**: Admin Copilot（`core/admin_assistant/` 1,640行、capability registry 16件 +
  KB 6ファイル + intent 分類）+ G層 next_steps（同居、6ルール、`assistant_step_dismissals`）。
  G層→Copilot の一方向依存で、registry を単一の真実源とする設計は守られている。**この領域は健全**。
- 注意点は routes が `admin_assistant.py` に Copilot と G層同居していることくらい（小規模）。

### 9. 分野マップ（Field Atlas）
- **実体**: `core/atlas*.py` 7ファイル 4,103行（スキーマ/生成/配置/パス/ストア/状態/報告で
  1ファイル=1関心事）+ routes 1,848行 + フロント atlas-*.js **9ファイル**（CLAUDE.md 記載は6）。
  fail-closed（404→非表示、フィクスチャ退避なし）も設計どおり実装。
- ★ **調査全体で最も設計品質が高い領域**。重複なし。学習シグナル系（下記11）の対極。

### 10. コース実施状況管理
- ★ **実質存在しない**。教員向けの受講者数・完了率ダッシュボードは無い。
  存在するのは k-匿名集約（関心ダッシュボード / anchor-insights / stumble-summary）のみで、
  これは「学習者を監視しない」（P3）という**意図的な設計思想の帰結**。
  → 「機能が欠けている」のではなく「作らないと決めた」領域。今後作るなら P3 との整合を先に設計すべき。
- ★ 付随バグ疑い: `services.calculate_progress` の `mastered_concepts` カウントは
  `status=="mastered"` の**書き込み箇所がコードベースに存在しない**死にロジック（常に0になり得る）。

### 11. 学習状況管理
- **実体**: `interest_traces`（kind = question / tension / detour / misconception / raw、
  status 遷移で削除しない）を軸に、Stage0 prefilter（同期・非LLM）→ 非同期 LLM worker →
  digest → 本人 confirm/dismiss → k-匿名集計、という共通パターン。
  習得判定は `learning_chat_history` の**行の有無**だけの粗いヒューリスティック
  （トピック完了の専用状態は存在しない）。誤解記録は `learning_states.personal_graph`。
- ★ **同型 LLM worker が5系統**（tension / structure_anchor / reconstruction /
  doubt.scope_candidates / doubt.assumption_mining、計 5,372行）:
  - `llm_client.py`: **90-95%同一**（差分はクラス名と設定キーのみ）
  - `repair.py`: **85-90%同一**（2回再試行→repair_failed の同一ループ）
  - `worker.py`: 60-70%が同型骨格。`_check_and_count_llm_call` という**同名関数が5回別々に定義**。
    冪等性フラグの持ち場（列/JSONB キー）だけがモジュールごとに違い、抽象化されていない。
  - digest/confirm/dismiss API も SQL の形・監査記録まで揃った双子関数群（services.py 2340-2700 付近）。
- ★ **k-匿名（k=3・n<3 非表示）が4箇所に独立実装**（services.py ×2 / reconstruction/health.py /
  doubt/schema.py）。値はたまたま全部3で一致。

### 12. 通知機能
- **実体**: migration 038 の `user_notifications`（status watcher 由来6種）+ V層の
  `share_notifications`（037）の**2テーブル**を `routes/notifications.py` が読み取りで併合。
  フロントの🔔は統合 API に差し替え済み。
- ★ 設計書記載の `course.published` / `course.atlas_bound` イベントは**未実装**（watcher は
  document_analysis_runs と background_tasks の2テーブルしか見ていない）。
- ★ 設計書の migration 番号が実体と入れ替わったまま（status_notification 設計書は「039想定」
  →実体038、guidance 設計書は「038」→実体039）。

### ユーザーの12分類に無かった機能（見落とし補完）
- **学習対話そのもの（RAG チャット + casual/音声モード + 出所分類 + 書き直し削除）** —
  `routes/learning.py`（2,240行）。B層の中核なのに12分類に対応項目がない。
- **D層（疑義・認識的地位台帳、migration 029-033）** — `core/doubt/` + `routes/doubt.py`（1,621行）。
- **U層（LLM 使用量計測、migration 043）** — `core/llm_usage/`（1,427行）。
  ★ **バグ疑い（要修正）: migration 043 の `llm_usage_events` テーブルが
  `main.py::_run_migrations()` に存在しない**（他の 038-042 はインライン DDL があるのに 043 だけ漏れ）。
  遅延 CREATE も無いため、**U層の計測が本番で機能しない可能性が高い**。
- **E層（exposition_layer_design.md）** — 設計書 339行が存在するが
  **実装コードはゼロ**（grep ヒットなし）。設計倒れとして明示すべき。
- **エクスポート層** — `routes/export.py`（3,144行）+ `export_artifacts.py`（1,875行）。
- **revision_runs（migration 019、解析 run の accept/reject ワークフロー）** —
  「revision」という語が ①draft 楽観ロックの版番号 ②解析 run の受理履歴 ③V層の共有版、
  の**3つの異なる概念**に使われている。

---

## 第2部: 横断的な発見（定量サマリ）

### 規模
| 対象 | 実測 |
|---|---|
| backend/api/routes/ | 22ファイル・25,686行（上位: theory_components 3,497 / lecture_studio 3,403 / export 3,144 / admin 3,097 / learning 2,240 / export_artifacts 1,875 / doubt 1,621） |
| backend/api/main.py | 1,794行、うち**約1,600行（89%）が `_run_migrations()` 単一関数**。エンドポイントは /healthz の1本のみ |
| frontend/public/js/ | 16ファイル・22,776行、**admin.js 13,050行（57%）+ app.js 4,499行（計77%）**。他14ファイル平均373行 |
| migrations | 42ファイル（002-043、欠番なし・001なし） |
| agents | 実働17 + 空1（graph_narrative） |

### 「同じものがN回」の一覧
| パターン | 実測 | 正本の有無 |
|---|---|---|
| `theory_review_events` への監査 INSERT | **約11箇所**の個別 `_record_*_event`（atlas.py と theory_components.py には**同名別実装**あり） | `services.record_review_event` が汎用ヘルパーとして**既に存在**するが徹底されていない |
| LLM worker 骨格（client/repair/counter） | 5系統（llm_client 90-95%同一 / repair 85-90%同一 / `_check_and_count_llm_call` ×5） | なし |
| コスト制御 env（`*_MAX_CALLS_*` + `*_LLM_MODEL`） | 8機能×2 = 16個 + in-memory カウンタ個別実装 | config.py に定義は集約、ロジックは分散 |
| k-匿名ゲート（k=3） | 4箇所独立 | なし |
| cartridge_loader / CartridgeContext | 12コピー / 10再定義 | パス解決のみ共有 |
| 素の `threading.Thread` worker | 11箇所・9モジュール+routes（lecture_studio.py だけで7箇所） | なし |
| draft/楽観ロック/freeze | 2実装（atlas_store / library、意図的コピーと明記） | なし |
| グループ権限テーブル | 2枚（course/document、スキーマ完全一致） | — |
| 通知テーブル+宛先解決 | 2系統 | 読み取りのみ併合済み |
| マイグレーション DDL | **全42本が SQL ファイルと main.py の2重管理**（043は片方欠落） | どちらが正本か曖昧 |
| 「core は FastAPI 非依存」ガードレールテスト | 5回ほぼ同一コードでコピー | なし |
| 状態合成（教材ステータス） | 2箇所（admin.py 独自 JOIN / projector.py） | projector が正本のはずが未接続 |
| スライド分割 | Python + JS の並行実装 | core/lecture.py が正本のはずが JS が再実装 |

### レイヤー命名の混乱
- A/B/C/D は逐次順序、以降は頭字語式（R/V/G/L/U/S）に切替わり、**「第五の層」を3つの設計書
  （E/R/V）が独立に自称**。序数レジストリが存在しない。
- 層⇄ディレクトリ対応: D/R/V/U/L はきれいに対応、B は複数ディレクトリに分散、G/S は他所に同居。
- docs/README.md の「①構造化②適応学習③没入講義」という別系統の3層モデルと語彙衝突。
- docs/architecture/data-model.md は「init→022」までしか記載なし（実体は043）。

---

## 第3部: 整理の提案

### 推奨する概念モデル（12機能の再グルーピング）

機能列挙ではなく「6つのプレーン + 横断基盤」で捉えると、コードと文書の対応が素直になる:

| プレーン | 含まれる機能（ユーザー番号） | 主なコード |
|---|---|---|
| **① コンテンツ・プレーン**（教材→構造化成果物） | 1, 6 | agents/ + document_pipeline/ + library/ + cartridges |
| **② オーサリング・プレーン**（人が講義に仕立てる） | 2 | course builder + course_content_builder + lecture_studio + lecture/tts |
| **③ ラーニング・プレーン**（受講・対話・学習シグナル） | 11 (+RAGチャット, R層) | learning.py + tension/anchor/reconstruction + learning_states |
| **④ トラスト・プレーン**（承認・疑義・共有・版） | 4 (+C/D/V層) | theory_components(C) + doubt(D) + versioning(V) + group permissions |
| **⑤ ガバナンス・プレーン**（権限・監査・状態・通知・案内） | 3, 5, 7, 8, 12 (+10は意図的不在) | dependencies + services権限9関数 + theory_review_events + status/ + admin_assistant/ |
| **⑥ 空間ナビゲーション**（地図） | 9 | atlas 群（現状の模範実装） |
| **⑦ オブザーバビリティ** | （リスト外） | llm_usage/(U層) |
| **横断基盤（未整備 → 新設提案）** | — | LLM worker 骨格 / コストゲート / 監査 / k-匿名 / draft-freeze / ジョブ実行 |

### Tier 0 — バグ・整合性（すぐやる、リスクほぼゼロ）
1. **migration 043 を `_run_migrations()` に追加**（または欠落理由の確認）。現状 U層が
   本番で機能しない可能性が高い。
2. ドキュメント修正: 設計書の migration 番号入替（038⇄039）/ CLAUDE.md に V層を追記 /
   A2 の「クローン」記述を現行仕様（learning_states 方式）に更新 / data-model.md を043まで更新 /
   E層設計書に「未実装」を明記。
3. **レイヤー索引表**を docs/architecture/ に1枚作る（文字/正式名/正本設計書/ディレクトリ/
   migration/実装状態）。「第五の層」重複のような事故の再発防止。

### Tier 1 — 削除（低リスク・見通し改善が最大）
4. **デッドコード削除**: extractor.py の PaperStructure 生成系（GROBID ユーティリティ2関数は
   orchestrator 用に移設）/ teacher_graph.py / batch.py / chat.py::generate_chat_response /
   agents/graph_narrative/ / calculate_progress の mastered 死にロジック（実装するか削除するか二択）。
5. **Neo4j の去就を決める**: 新パイプラインは Neo4j に書いておらず、書き込み経路（batch.py）は
   呼び出し元ゼロ。読み取りの実利用が本当に無ければ docker compose から Neo4j を落とせる =
   インフラ・運用コストの即時削減。CLAUDE.md の技術スタック表も要更新。**着手前に実利用の最終確認が必要**。

### Tier 2 — 横断基盤の抽出（中リスク、DRY 化の本丸）
6. **LLM worker 共通基盤**（最優先の共通化）: `core/llm_worker/`（仮）に
   `BaseLLMClient(model_key)` + `run_with_repair(...)` + `CostGate(daily_limit, session_limit)` を
   置き、5系統から差分注入で利用。8機能×2 の env と5個の重複カウンタが1実装になる。
   ドメインロジック（会話窓構築・claim 選定・台帳ロック）は各モジュールに残す。
7. **監査の一本化**: 既存の `services.record_review_event` に11箇所を寄せる + entity_type の
   語彙カタログ（14+種）を schema 定数化。CLAUDE.md の「再利用」記述を実態に一致させる。
8. **k-匿名ゲートの共通化**: `core/privacy.py` に K_ANON=3 と gate 関数を1つ。
9. **cartridge 読み込みの統合**: `agents/cartridge_loader.py` + `agents/cartridge_context.py` に
   12コピー+10再定義を集約（差分ほぼゼロで低リスク、600行超削減）。将来的には
   core/cartridges.py 系との2系統分裂も解消。
10. **権限判定の集約**: 3段フォールバックを `services.resolve_document_access(user, ref) -> {view, edit}`
    に吸収（N+1 解消）/ groups.py を services 経由に / 通知宛先解決を
    `services._resolve_recipients(object_type, object_id)` に一本化 / 命名は versioning.py 方式
    （canonical id 返却）に統一。
11. **講義系の判定共通化**: 音声 readiness を `core/lecture.py` の単一関数に集約し
    projector と lecture.py の両方から呼ぶ / スライド分割はプレビュー用エンドポイント
    （`POST /lecture-studio/preview-split`）でサーバ実装に一本化し JS 再実装を廃止。
12. **ガードレールテストヘルパー**: `backend/tests/guardrail_helpers.py` に
    FastAPI 非依存チェック等の共通アサーションを切り出し（5+回のコピーを1行呼び出しに）。

### Tier 3 — 構造改革（大規模、個別に判断）
13. **マイグレーション実行の一本化**: main.py の 1,600行 `_run_migrations()` を廃し、
    `backend/db/*.sql` を実行する薄いランナー（または Alembic）に。「正本が2つ」問題
    （043欠落はこの構造の必然的帰結）を根絶する。**Tier 0-1 と並ぶ優先度で検討推奨**。
14. **グループ権限テーブルの統合**: `object_group_permissions(object_type, object_id, group_id,
    permission)` に一本化。V層（037）が既に object_type ポリモーフィック方式の先例。
15. **通知テーブルの統合**: user_notifications + share_notifications → 1テーブル
    （宛先解決の一本化(10)の後で）。
16. **status projector の UI 接続**: `/api/admin/materials` の独自合成を projector ベースに
    差し替え（038 のフェーズ1完遂）。コース完了判定3箇所も projector に寄せる。
17. **巨大ファイルの分割**:
    - admin.js（13,050行）→ 前例（admin-assistant.js の `initApp()` 疎結合注入方式）を使い
      タブ単位に分割。最初の切り出し候補は原稿スタジオ（`ls` 接頭辞 約6,000行）。
    - lecture_studio.py（3,403行）→ scripts / pipeline / topics の3分割。
    - main.py のルーター二段ネスト（admin.py が8ルーターを隠れて束ねる）をフラット化。
18. **learning_courses.data の正本スキーマ化**: `CourseData` Pydantic モデル + アクセサを
    1箇所に定義し、40箇所超の素の dict アクセスを段階的に置換。
19. **orchestrator の PipelineStage 抽象化**: 26回繰り返される定型
    （artifact確認→実行→保存→報告）をステージオブジェクトのリストに（resume ロジックと
    密結合のため難度高・最後でよい）。
20. **draft/freeze 共通ミックスイン**: 第3の利用者が現れた時点で着手（現状2実装は許容範囲）。

### 優先順位の考え方
- **今すぐ**: Tier 0（043バグ・文書整合）→ 効果に対してコストがほぼゼロ。
- **次**: Tier 1（削除）+ 13（マイグレーション一本化）→ 「どれが本物か」を1つにする作業。
  以後の全変更の認知コストを下げる。
- **その後**: Tier 2 を「新機能を1つ追加するたびに1項目」のペースで混ぜる
  （6 → 7 → 9 → 10 → 11 → 8 → 12 の順を推奨。6 が最も再発防止効果が高い:
  次の LLM worker 機能からコピペが不要になる）。
- **Tier 3** は 16(projector 接続) と 17(admin.js 分割の第一歩) 以外は急がない。
  14/15 は機能追加でテーブルがもう1枚増えそうになった時が着手の合図。

### この設計の「良かった点」（維持すべき規律）
- 不変条項（P1-P8, G1-G8, U1-U8 等）+ ガードレールテストという「原則を構造的に守らせる」手法は
  非常に効果的に機能している（fail-closed / 削除しない / 数値を見せない / core 非 FastAPI が
  全層で実際に守られていることを今回の調査でも確認）。
- Atlas 群の「1ファイル=1関心事、FastAPI 非依存の純関数モジュール」は模範。
  学習シグナル系の共通化(6)は Atlas の流儀に寄せる形で行うとよい。
- 「導出 vs 保存」の境界（状態を複製しない）は 038/039 で正しく守られている。
  問題は導出器が UI に届いていないこと(16)だけ。

---

## 第4部: 実施記録（2026-07-12 追記）

Tier 0〜Tier 2 を同日に実装した。実施内容と、実装時の検証で判明した本レポートの訂正事項。

### 実施済み項目

**Tier 0（全項目完了）**
- 1: migration 043 の `_run_migrations()` 欠落は U層実装時に解消済みであることを確認（main.py:1661）。
- 2: 設計書の migration 番号入替（status_notification→038 / guidance→039）、CLAUDE.md への V層追記、
  A2 クローン記述の現行仕様（learning_states 方式）注記、data-model.md の 023〜043 追記、
  E層設計書への「未実装」明記 — すべて実施。
- 3: `docs/architecture/layer_registry.md` 新設（13層の索引）。追加発見: E層設計書は migration 034 を
  予約しているが 034 は assistant_actions が使用済み（実装時に番号衝突する）。

**Tier 1（全項目完了）**
- 4: extractor.py の旧抽出パイプライン（856→255行）/ teacher_graph.py / batch.py /
  chat.py::generate_chat_response（chat.py 181→78行）/ agents/graph_narrative/ /
  calculate_progress の mastered 死にロジック（app.js の常時0表示カードも撤去）— すべて削除。
  追加で seed_patterns.py（パターンシステム全体がデッド化）と、孤児化した
  parse_tei_to_logical_chunks / extract_text_from_pdf_bytes / chunk_text（extractor.py 側）も削除。
- 5: **Neo4j を完全撤去**。削除前の読み取り専用検証で「本番到達可能な Cypher 実行ゼロ・
  新パイプライン非参照・neo4j_node_id カラムは書き込み元なしで常に NULL」を実測確認の上、
  docker-compose（サービス/volume/depends_on/env）・core/db.py・services.py の第2ドライバ・
  config.py フィールド・ORM カラム・lecture_studio.py 読み出し・admin.js 分岐・requirements・
  関連ドキュメントを一括除去。物理 DB カラムの DROP は行っていない（無害な残置）。

**Tier 2（提案 6〜12 全項目完了）**
- 6: `core/llm_worker/`（client / repair / cost_gate、253行）新設。5系統が委譲、環境変数名・
  冪等性フラグ・失敗時ドメイン処理は不変。ガードレール+単体テスト34件追加。
- 7: 監査 INSERT 11箇所を `services.record_review_event` に委譲（core 層 2箇所と
  トランザクション同乗の persistence.py は理由付き残置）。entity_type カタログ26語彙を
  `core/schema.py` の `AUDIT_ENTITY_*` に定数化 + ガードレールテスト。
- 8: `core/privacy.py` 新設（K_ANONYMITY=3）。4箇所が委譲、レスポンス形式は不変。
- 9: `agents/cartridge_loader.py` + `agents/cartridge_context.py` 新設。9コピーは再エクスポート化、
  固有差分のある3エージェント（apparatus_semantics / component_assembly / component_graph）は
  サブクラス化。正味 -379行。
- 10: `services.resolve_document_access()` 新設で theory_components.py の chunk ごと再解決 N+1 を
  解消（material_id 単位に重複排除）。通知宛先解決は `core/notification_recipients.py` に共通
  JOIN を抽出（宛先集合の方針は各層に残置）。groups.py は group_members.role という別概念のため
  委譲せず残置（本レポート 10 の想定と異なる判断、下記訂正参照）。
- 11: 音声 readiness を `core/lecture.py::compute_material_audio_readiness()`（スライド単位+言語一致）に
  一本化し projector / lecture.py 両方が使用（chunk 粒度だった projector 側の判定は意図的に修正）。
  スライド分割は `POST /api/admin/lecture-studio/preview-split` でサーバ一本化し、admin.js の
  `lsSplitSlides` 並行実装を廃止。
- 12: `backend/tests/guardrail_helpers.py` 新設、9テストファイルの重複アサーションを置換
  （収集数 2,774 で不変）。

### 本レポートの訂正（実装時の実測による）

1. **§1 extractor.py**: 「GROBID ユーティリティ2関数を orchestrator が再利用」は誤り。実際に
   orchestrator が import するのは `extract_tei_xml_from_pdf_bytes` の1関数のみ。また
   `merge_paper_structures` という関数は存在しない（実体は `evaluate_and_merge_proposals`）。
2. **§1 chat.py**: `generate_chat_response` だけでなく **chat.py モジュール全体が本番未使用**
   だった（`search_chunks` は tier 契約のユニットテストが参照するため残置）。
3. **§3/§11 Neo4j**: core/db.py に加え、**services.py:34 に第2の独立 Neo4j ドライバ実装**
   （`_neo4j_driver`、これも呼び出し元ゼロ）が存在した。レポート未指摘。
4. **§ Tier2-10**: groups.py の「独立実装」は視点の誤り — group_members.role（グループ運営の
   admin/member）は文書の view/edit とは別ドメインで、services へ委譲するとむしろクエリが増える。
   統合対象ではなく「別概念の正当な独立実装」だった。
5. **§ Tier2-10**: 通知宛先解決の統合先として `services._resolve_recipients` を提案していたが、
   呼び出し元は両方 core/ 配下のため services.py（api 層）への配置は core→api の逆依存を作る。
   `core/notification_recipients.py` に配置した。
6. **§2 スライド分割 / §5 状態合成**: 提案11の実施により解消。projector の UI 接続（提案16）は
   未実施のまま（Tier 3 スコープ）。

### 残課題（Tier 3、未着手）

13〜20 は未実施。特に 13（マイグレーション実行の一本化）は Tier 0-1 と並ぶ優先度で検討推奨の
まま残っている。16（projector の UI 接続）・17（admin.js 分割）が次の候補。
