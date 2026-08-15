# API とルーティング

[← ドキュメント目次](../README.md)

FastAPI バックエンドのエンドポイント構成、認証・RBAC、開示範囲の制御をまとめます。
実装は `backend/api/`。

> **役割分担（正本の所在）**: 本ページは**エンドポイントの正本**（メソッド / パス / 権限 / 一行説明）。
> 画面操作の**手順の正本**は [docs/admin_operations/](../admin_operations/) 各ページ
> （materials / course / lecture_studio / atlas / doubt / library / llm_usage / interest_dashboard /
> users / system）に委ねる。手順の詳細をここに書かない。
>
> **網羅性（2026-08-14 時点）**: `backend/api/routes/` の全ルーターデコレータ + `/healthz` を
> 突合し、本ページの一覧と過不足なく一致することを確認済み（346 経路。`GET
> /api/admin/documents/{id}/figures` は admin.py と figure_presentation.py の2定義が
> 1経路に収束するため1行）。ルーターやエンドポイントを追加したら本ページの該当節にも行を足すこと。

---

## 1. アプリ構成

| ファイル | 役割 |
|---|---|
| `backend/api/main.py` | FastAPI アプリ本体。lifespan（下記）・ルーター登録・CORS・エラーログ middleware |
| `backend/api/dependencies.py` | 認証・RBAC の依存関数（JWT 検証、ロール要求） |
| `backend/api/schemas.py` | API 固有の Pydantic リクエスト/レスポンスモデル |
| `backend/api/services.py` | 共通ビジネスロジック（バックグラウンドタスク CRUD、権限判定 `resolve_document_access` など） |
| `backend/api/routes/*.py` | 機能別ルーター（下記マウント一覧参照。`export_artifacts.py` はルーターではなく export のヘルパー） |

**lifespan（`main.py::_lifespan`）の起動時処理**: ①マイグレーション適用
（`core/migrations.py::run_migrations`。PostgreSQL 起動待ちで最大10回リトライ）→
②システム管理者アカウント `Administrator` の初期化 → ③カートリッジ／`backend/atlas_domains/` 同梱の
凍結骨格シード取込（`core.atlas_store.import_bundled_skeletons`、migration 027）→
④ビルトインスキーマ型・述語の seed（`core.schema_registry.seed_builtin_schema`）→
⑤L層ナレッジライブラリの同梱シード取込（`core.library.seed`、migration 042）→
⑥M層 LLM モデルポリシーの env → DB シード取込と `DbPolicyBackend` への差し替え（migration 061）→
⑦V層の削除猶予スイーパ起動（`core.versioning.worker`、migration 037）→
⑧状態管理・通知基盤の遷移検知 watcher 起動（`core.status.watcher`、migration 038）→
⑨help_kb のバリデーション3種（`validate_manual` / `check_ui_anchor_mappings` /
`check_admin_ui_anchor_mappings`）→ ⑩help_kb 配信スナップショットの content-hash 監査記帳
（`core.help_kb.audit`）→ ⑪help_kb ベクトル補助層の同期（バックグラウンドスレッド、migration 058）。
③〜⑪はすべて fail-open（失敗しても起動を止めず warning ログのみ）。

**ルーターのマウント（main.py、Tier 3-17c でフラット化）**: 全ルーターは `main.py` から直接
`app.include_router(...)` で登録される（admin.py 経由の二段ネストは廃止済み）。

- **自前 prefix で直接登録（24本、`main.py` の登録順）**: `auth`（/api/auth）/ `learning`
  （/api/learning）/ `figure_presentation`（/api/admin）/ `element_explanations`（/api/admin）/
  `admin`（/api/admin）/ `error_logs`（/api/admin/error-logs）/ `lecture`（/api/learning/lecture）/
  `groups`（prefix なし。/api/groups・/api/me をパスに直書き）/ `export`（prefix なし。
  /api/courses・/api/documents）/ `atlas.learning_router`（/api/learning/atlas）/
  `atlas.report_router`（/api/atlas）/ `atlas_view`（/api/atlas）/ `doubt.learning_router`
  （/api/learning）/ `reconstruction.learning_router`（/api/learning）/
  `discuss_observation.learning_router`（/api/learning）/ `cycle.learning_router`（/api/learning）/
  `library`（/api/admin/library）/ `llm_usage`（/api/admin/llm-usage）/ `llm_models`
  （/api/admin/llm-models）/ `personal_map.router`（/api/learning）/ `personal_map.me_router`
  （/api/me）/ `my_records.me_router`（/api/me） / `descent.learning_router`（/api/learning）/ `landscape.learning_router`（/api/learning）
- **`prefix="/api/admin"` を付けて登録される admin 系子ルーター（20本、`main.py` の登録順）**:
  `lecture_studio`（パッケージ。`_shared`/`scripts`/`pipeline`/`topics` に分割、Tier 3-17a）/
  `theory_components` / `cartridges`（/cartridges）/ `revisions` / `atlas.router`（/cartridges 配下）/
  `atlas.admin_atlas_router`（/atlas）/ `atlas.binding_router`（/courses）/ `atlas_gaps`
  （/cartridges 配下）/ `doubt.admin_router`（/doubt）/ `admin_assistant.admin_router`（/assistant）/
  `reconstruction.admin_router` / `seminar_brief.admin_router`（/documents 配下）/
  `discuss_observation.admin_router` / `versioning` / `status`
  （/status）/ `notifications`（/notifications）/ `deliberation`（/deliberation）/ `teaching_figures` /
  `landscape.router`（/landscape）/ `admin_assistant.help_kb_router`（/help-kb）
- **例外（#496）**: `GET /api/admin/documents/{id}/figures` は admin.py にも定義が残るが、main.py が
  admin.router から当該 GET ルートを除去して `figure_presentation.py` 側のハンドラだけを配信する
  （admin.py 側は後方互換テスト用の残置）。

> `main.py` 側のコメントは 2026-08-14 に「本数はコードを数える」方式へ改訂済み
> （`doc_review_findings_2026-08-13.md` 7-2 の対応）。本ページの一覧が登録実態。

> `core/` には FastAPI を import しない方針（テスタビリティ確保）。API 固有モデルは `api/` 側に置きます。

---

## 2. 認証と RBAC

### 認証方式（`dependencies.py`）
- **JWT (HS256)**, 有効期限 24 時間。`JWT_SECRET` で署名。
- パスワードは **bcrypt**（passlib）でハッシュ。
- トークン payload: `{sub, username, email, role, exp}`。

### ロールのマッピング
| DB 上のロール | アプリ定数 | 説明 |
|---|---|---|
| `learner` | `ROLE_STUDENT` | 学生 |
| `instructor` | `ROLE_TEACHER` | 教員 |
| `admin` | `ROLE_SYSTEM_ADMIN` | システム管理者（最高権限） |

### 依存関数
- `_get_current_user()` — Bearer トークンを検証し `{id, username, email, role}` を返す（全保護エンドポイントで使用）
- `_require_teacher()` — TEACHER 以上でなければ 403
- `_require_system_admin()` — SYSTEM_ADMIN でなければ 403

権限階層は **SYSTEM_ADMIN > TEACHER > STUDENT**。詳細・開示範囲の制御は [認証・権限・開示範囲](../features/auth-visibility.md) を参照。
表中の「TEACHER」は `_require_teacher`（= TEACHER または SYSTEM_ADMIN）、
「SYSTEM_ADMIN」は `_require_system_admin` を指す。「本人のみ」は JWT の user_id で行レベルに絞ることを指す。

---

## 3. エンドポイント一覧

### 共通

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/healthz` | 公開 | ヘルスチェック |

### 認証 `/api/auth`（`routes/auth.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/auth/login` | 公開 | ログイン（JWT 取得） |
| GET | `/api/auth/me` | 要ログイン | 現在のユーザー情報 |

### 学習 `/api/learning`（`routes/learning.py`）

全エンドポイントが要ログイン。ロールゲートは無く、権限は「所有 / 受講（`learning_states` 行）/
グループ共有 / 本人スコープ SQL」で決まる。RAG の中身は [RAG チャットフロー](rag-chat.md)、
学習 UI 側は [学習機能](../features/learning.md)。

#### コース CRUD・進捗・受講登録

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/learning/courses` | 要ログイン（`visibility='group'` は当該グループメンバーのみ） | コース作成（バックグラウンドでコース内容生成を起動） |
| GET | `/api/learning/courses` | 要ログイン | コース一覧（所有 + 受講中 + 公開テンプレ + グループ共有を重複排除） |
| GET | `/api/learning/courses/{cid}` | 所有者 or 受講者（純 viewer は V層発行版スナップショット） | コース詳細（マスター教材 + 個人レイヤーの分離形式） |
| GET | `/api/learning/courses/{cid}/version-notice` | 所有者 or 受講者（fail-open） | 削除予約など版ライフサイクルの一行通知（V層バナー用） |
| PUT | `/api/learning/courses/{cid}` | 所有者 or editor | コースの指定フィールドのみ部分更新 |
| DELETE | `/api/learning/courses/{cid}` | 所有者のみ | コース削除（object_group_permissions 孤児行も明示削除） |
| GET | `/api/learning/courses/{cid}/progress` | 所有者 or 受講者 | 学習進捗の計算 |
| POST | `/api/learning/courses/{cid}/enroll` | 公開テンプレ / group 可視 + メンバー / コース共有 viewer・editor | 受講登録（コースは複製せず `learning_states` に1行 INSERT。migration 011 でクローン方式は廃止済み） |

#### トピック教材・確認問題・チャット

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/topics/{tid}/material` | 所有者 or 受講者 | トピック教材（`student_material` 最優先 → content/summary → PDF チャンク） |
| POST | `/api/learning/courses/{cid}/topics/{tid}/check` | 所有者 or 受講者 | 確認問題の LLM 採点（不合格は誤解記録、合格はトピック完了を永続化） |
| POST | `/api/learning/courses/{cid}/topics/{tid}/chat` | 所有者 or 受講者 | RAG チャット（意図分類・casual モード・書き直し `replace_message_id`・tier/grounding 判定・tension/anchor 痕跡記録を内包） |
| GET | `/api/learning/courses/{cid}/topics/{tid}/chat` | 本人の履歴のみ | チャット履歴取得 |
| DELETE | `/api/learning/courses/{cid}/topics/{tid}/chat` | 所有者 or 受講者（本人の行のみ） | 本人のトピック別チャット履歴を全削除 |
| DELETE | `/api/learning/courses/{cid}/topics/{tid}/chat/messages/{mid}` | 所有者 or 受講者（本人の履歴のみ） | 指定メッセージ以降の往復を truncate（派生 interest_traces は `superseded` 化。機能3） |
| GET | `/api/learning/courses/{cid}/source-chunk/{chunk_id}` | 所有者 or 受講者 **かつ chunk の document が当該コースの source** | 出典ポップアップ用のチャンク本文・数式・出典名（音声会話の教材パネルにも使用）。スコープは `list_course_source_document_ids(course_data)` を `get_chunk_passage(..., allowed_document_ids=)` の SQL 内 `ANY(...)` で強制。コース非アクセス・コース source 外・不明 chunk はすべて同一 404 |
| GET | `/api/learning/courses/{cid}/chunks/{chunk_id}/claim-refs` | 所有者 or 受講者 かつ chunk がコース教材に属する | 出典タブの台帳併記（D3-6）を claim へ拡張する読み取り。claim の id・claim_type・短い label のみ（confidence 等の数値なし）。属さない chunk は 404 |
| GET | `/api/learning/courses/{cid}/figures/{fid}/image` | 受講ゲート + 図の出所条件（下記） | 学習者向け図画像配信。**抽出図**（`document_figures`）は「受講 ∧ 図の document がコース sources ∧ コース content から参照されている」の AND、**教材図**（`course_teaching_figures`）は「受講 ∧ 図の course_id 一致 ∧ 参照されている ∧ `status='adopted'`」の AND。いずれか欠ければ 404（draft/retired は学習者に出ない） |

#### 「論文と話す」（discuss、B層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/discuss/opening` | 所有者 or 受講者 | 開幕画面（非LLM・読み取り専用）。中心命題・支持構造・別の見方・理論のバックボーン・「最も脆い一手」（主語別）・`course_focus`・教員承認済みの「議論のきっかけ」（`documents[].discussion_seeds`）を投影 |
| POST | `/api/learning/courses/{cid}/discuss/reflection` | 本人のみ | 着地画面「今日の理解を自分の言葉で」を `kind='tension'` / `status='articulated'` の痕跡として直接記録（LLM 0回・候補を経由しない。空文字は 422） |

#### 問いの軌跡（interest_traces）・地図導線

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/interest-traces` | 本人のみ | 問いの軌跡一覧（`map_excluded` フラグ付き。`topic_id` で絞り込み可） |
| POST | `/api/learning/courses/{cid}/interest-traces/{tid}/resolve` | 本人のみ | 痕跡を resolved にする |
| POST | `/api/learning/courses/{cid}/interest-traces/{tid}/internalize` | 本人のみ | 「なぜ自分に重要か」を payload に保存 |
| POST | `/api/learning/courses/{cid}/atlas/path-decision` | 要ログイン（本人名義で記録） | 学習パス提案カードの選択（proceed/edit/dismiss/connect）を痕跡に記録 |
| POST | `/api/learning/traces/{trace_id}/map-exclude` | 本人の行のみ | 個人知識ネットワーク表示から除外（`payload.map_excluded` のみ。status・行は触らない） |
| POST | `/api/learning/traces/{trace_id}/map-restore` | 本人の行のみ | 表示へ戻す |

#### 違和感（tension、B層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/tension/digest` | 本人のみ | tension 候補（confidence≥0.55・最大3件・数値は返さない）+ 遅延マイニング起動 |
| POST | `/api/learning/tension/{trace_id}/confirm` | 本人の candidate 行のみ | 候補を本人が確定（→ open / `learner_text` 付きは articulated） |
| POST | `/api/learning/tension/{trace_id}/dismiss` | 本人の candidate 行のみ | 却下（dismissed 遷移。行は保持） |
| POST | `/api/learning/tension/{trace_id}/connect` | 本人の open/articulated 行のみ + 接続先 document の閲覧可否を fail-closed 検証 | 確定済み tension をグラフ node/edge に接続（`connected_refs` に記録） |

#### 構造帰属型の問い（structure_anchor）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/anchors/digest` | 本人のみ | llm_candidate の帰属候補（最大3件・数値なし）+ 遅延マイニング起動 |
| POST | `/api/learning/anchors/{trace_id}/confirm` | 本人の行のみ | 帰属の確定/訂正（未生成なら segment 縮退アンカーを新規作成） |
| POST | `/api/learning/anchors/{trace_id}/dismiss` | 本人の llm_candidate 帰属のみ | `structure_anchor.status='dismissed'`（問い自体は保持） |

#### ハンズフリー音声会話・インスペクト・C層学習者向け

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/learning/voice/transcribe` | 要ログイン | multipart 音声の文字起こし（Whisper 系。10MB 上限、openai プロバイダ以外 503） |
| POST | `/api/learning/voice/speak` | 要ログイン | テキスト整形（LaTeX/markdown/出典マーカー除去）→ TTS で MP3(base64) |
| GET | `/api/learning/help/ui-anchors` | 要ログイン | 学習画面インスペクト・モードの UI 論理アンカー配信（student audience のみ・読み取り専用。ログイン時1回フェッチ想定） |
| POST | `/api/learning/help/ui-anchor-events` | 要ログイン（本人記録） | 未整備アンカーへのホバー滞留を `kind='help_usage'` 痕跡として記録（質問の逐語は積まない。30分デデュープ。G層 `manual.help_gaps_pending` に相乗り） |
| GET | `/api/learning/courses/{cid}/components/{comp_id}/explanations` | コース閲覧権限 | 承認済み（teacher_approved）説明バージョン一覧（段階ラベルのみ・数値スコアなし） |
| GET | `/api/learning/courses/{cid}/components/{comp_id}/context` | 受講ゲート + component の document がコースの document 集合に含まれる | コーススコープ component 文脈（instance / shared_part / graph）。DB UUID と agent 側 legacy ID の両方を受理。いずれか欠ければ 404 |
| GET | `/api/learning/courses/{cid}/elements/{etype}/{eid}/context` | 同上（`etype` は `claim` / `equation` のみ） | 学習者向け claim / equation 文脈。`relation_status='candidate'` を除外し confidence 等を再帰除去。要素は解決できたが投影が空のときのみ `{"available": false}` を 200 で返す |

### レクチャーモード `/api/learning/lecture`（`routes/lecture.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/lecture/courses/{cid}/topics/{tid}/sequence` | 受講者（コース可視性） | レクチャーシーケンス構築（トピック教材 `_lecture_uses_topic_material` 優先、無ければ PDF チャンク経路） |
| POST | `/api/learning/lecture/courses/{cid}/topics/{tid}/tts` | 受講者 | **キャッシュ済み** TTS 音声の配信のみ（未生成は 404。生成は管理側バッチ限定。`topic:{tid}` 形式はトピック音声キャッシュから） |
| GET | `/api/learning/lecture/courses/{cid}/topics/{tid}/audio-status` | 受講者 | 再生可能な音声の有無を軽量判定（レクチャーボタン活性用。生成は行わない） |
| POST | `/api/learning/lecture/courses/{cid}/topics/{tid}/interrupt` | 受講者 | レクチャー一時停止中の質問チャット（現在チャンクをコンテキストに回答） |

### 分野の地図 — 学習者向け（`routes/atlas.py` learning_router / report_router、`routes/atlas_view.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/atlas/runtime-config` | 公開 | atlas-data.js のデータソース既定（api / fixture）を返す |
| GET | `/api/atlas` | 要ログイン | 地図データ一式（骨格+キャッシュ+個人層。`course`/`topic`/`focus` をサーバ側で解決。骨格なしは 404） |
| GET | `/api/atlas/node/{node_id}` | 要ログイン | 詳細パネル用のノード情報 |
| GET | `/api/learning/atlas/{cartridge_id}/skeleton` | 要ログイン | 学習者向け骨格（凍結・レビュー済み版のみ。draft のみの domain は 404） |
| GET | `/api/learning/atlas/cues/state` | 要ログイン | 導線の永続状態（first_login_seen。DB 不通時は seen=True の fail-closed） |
| POST | `/api/learning/atlas/cues/events` | 要ログイン | 導線イベント（shown/opened/dwell/learn_reached）の内部計測記録 |
| POST | `/api/atlas/report` | 要ログイン | 地図上からの修正報告を帰属つきで記録（教員レビューキューへ） |
| GET | `/api/atlas/reports/mine` | 本人のみ | 自分の報告一覧（`unacked=true` で未読の処理結果のみ） |
| POST | `/api/atlas/reports/{report_id}/ack` | 報告者本人 | 採用/見送り結果の既読化 |

### D層 — 学習者向け読み取り（`routes/doubt.py` learning_router、D3-6）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/ledger/{target_type}/{target_id}` | 要ログイン + コースアクセスゲート | 台帳の正直表示（検証状態ラベル・スコープ4軸・事実文のみ。記帳者 ID・疑義・生スコアは返さない。台帳なしは 404 の fail-closed） |
| GET | `/api/learning/courses/{cid}/open-assumptions` | 要ログイン + コースアクセスゲート | 未検証合意リストの閲覧（疑義者名を含めない読み取り専用版） |

### 再構成ループ — 学習者向け（`routes/reconstruction.py` learning_router、R層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/topics/{tid}/reconstruction/next` | 受講者本人 | 次の出題を返す（expected 等の伏せフィールドは返さない） |
| POST | `/api/learning/reconstruction/{item_id}/submit` | 本人 | 応答保存 → 非LLM DIFF → verdict + 出典リビール |
| POST | `/api/learning/reconstruction/{item_id}/revise` | 本人 | 再挑戦（改訂履歴 `revision_of` でつなぐ） |
| POST | `/api/learning/reconstruction/{recon_id}/self-check` | 本人 | 自己確認（agreed / disagreed / verdict_wrong） |
| POST | `/api/learning/reconstruction/{recon_id}/descend` | 本人 | 記号葉（SymbolRegistry）への降下プローブ |

### 理解サイクル — 学習者向け（`routes/cycle.py` learning_router、UCサイクル）

全エンドポイントが本人のみ（受講ゲートは `get_accessible_course_data`）。非LLM・同期（UC8）で、
intention / 軽量アンカーは行削除せず状態遷移のみで保持する（UC6。削除 API は無い）。
監査記帳は行わない（本人専用メモ）。詳細は `docs/features/understanding_cycle_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/learning/courses/{cid}/cycle/intention` | 本人のみ | OPEN（初回動機・予想・持ち越し問いへの再回答）と LEAVE（持ち越す問いの選択）を `role` で分岐して記録。`role='revisit_answer'` のときだけ「帰り道の景色」の差分事実文（数値なし・最大3件）を `facts` に同梱（導出失敗は fail-open） |
| POST | `/api/learning/cycle/intention/{trace_id}/dismiss` | 本人の行のみ | intention 痕跡の dismiss（status 遷移のみ） |
| POST | `/api/learning/courses/{cid}/cycle/anchor` | 本人のみ | 軽量アンカー4ボタンの1タップ確定。既存 structure_anchor 経路A（`attribution_source='learner_selected'`）へ相乗りし、element → selection → chunk → segment の順に縮退 |
| GET | `/api/learning/courses/{cid}/cycle/landing-candidates` | 本人のみ | LEAVE の選択候補一覧（数値・件数を含めない。導出失敗時は空配列で縮退） |
| GET | `/api/learning/courses/{cid}/cycle/return-door` | 本人のみ | 帰還の扉（`docs/features/return_door_design.md` §2.1）。書き置き（`leave_note`）・持ち越しの問い（carryover）・最後に確定した tension を**本人の逐語のみ**で返す（AI 要約ゼロ・経過日数/件数なし）。3部品とも無ければ `{empty: true}`（導出失敗も同値へ縮退） |
| GET | `/api/learning/courses/{cid}/cycle/todays-words` | 本人のみ | 「今日のあなたの言葉」トレイ（同 §2.2）。当日にやり取りのあった学習チャットの **user ロール発話の逐語のみ**（assistant 行は返さない）。最新30件上限で超過は `truncated: true`（当日判定は行 `updated_at` の近似） |

### 個人知識ネットワーク（`routes/personal_map.py`、読み取り専用）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{cid}/personal-network` | 本人のみ | コースビュー（互換 API。`course_id` は provenance + フィルター） |
| GET | `/api/learning/courses/{cid}/personal-network/journey` | 本人のみ | コーススコープの旅（クエリ `node_id` 必須。当該コース sources 内限定 + cross_course_hint） |
| GET | `/api/me/personal-network` | 本人のみ | 正本 API（本人所有の全痕跡由来。`include_candidate_links=true` は 422 の fail-closed） |
| GET | `/api/me/personal-network/journey` | 本人のみ | コース横断の旅（クエリ `node_id` 必須。hop ごとに can_view_document で fail-closed フィルタ） |

> このルーターは**読み取り専用**（書き込み API を作らないことをガードレールで固定）。
> 訂正操作（map-exclude / map-restore）は `routes/learning.py` 側にある。

### わたしの記録（`routes/my_records.py`、読み取り専用）

主権台帳v1（`docs/features/trace_registry_sovereignty_ledger_design.md` §3）。本人の
`interest_traces` 全行（kind 条件・status 条件なし — dismissed / superseded / candidate も
含む一望、P4）を系統（`core/trace_registry.py` の宣言順）でグルーピングし、各行に
公表状態の事実文を添えて返す。実体は `core/trace_ledger.py`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/me/records` | 本人のみ | 台帳 overview（系統グルーピング + status ラベル + 公表状態の事実文。新しい順・上限 500 行、超過は `truncated: true`。件数・スコアの数値フィールドなし — TR6） |
| GET | `/api/me/records/export` | 本人のみ | 持ち出し JSON ダウンロード（`Content-Disposition: attachment`。payload 全文・無加工、`user_id` キーなし、`schema_version: 1`。本人の持ち出しは監査記帳しない — 意図的） |

> このルーターは**読み取り専用**（書き込み・削除・封印メソッドを作らないことを
> `test_trace_registry_guardrails.py` で固定。TR4）。封印は v2 の専用設計書を経る。
> `{user_id}` パスパラメータは作らない — 対象は常に認証ユーザー本人。

### 構造の降下路（`routes/descent.py`、読み取り専用）

足場ダイヤル・楽屋の降下エンジン（`docs/features/structure_descent_design.md`。実体は
`backend/core/descent/`、非LLM・決定論）。**閲覧をサーバに記録しない**（開示履歴・使用数の
記録なし — SD1/SD5）。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/learning/courses/{course_id}/descent/ladder` | 受講者本人 | 足場ダイヤルの梯子（想起プロンプト→stage 骨格事実文→記号の定義・スコープ・表記ゆれ→出典リビール。素材が無い段は返さず、全段不成立は `available: false`） |
| GET | `/api/learning/courses/{course_id}/descent/backstage-path` | 受講者本人 | 楽屋の降下路（notation_patterns → 記号定義 → generic 説明。宣言文「ここでの質問と閲覧は集計に入りません…」同梱） |

> `element_type` は `equation` / `component` / `claim` のみ（他は 422）。書き込みメソッドなし。
> 楽屋からの質問は既存 learning_chat の `backstage: true` フラグで送られ、痕跡 kind
> `backstage_question`（教員集約・digest・わたしの地図から除外）になる。

### グループ `/api/groups`・`/api/me`（`routes/groups.py`）

グループ内ロールは `admin`（作成者ほか）/ `member`。下表の「グループ admin」はグループ内ロールを指し、アプリロールとは別。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/groups` | 要ログイン | グループ作成（作成者がグループ admin になる） |
| GET | `/api/groups` | 要ログイン | 自分が所属するグループ一覧 |
| GET | `/api/groups/{gid}` | グループ member | グループ詳細（招待コードは admin のみ表示） |
| PUT | `/api/groups/{gid}` | グループ admin | 名前・説明の更新 |
| DELETE | `/api/groups/{gid}` | グループ admin | グループ削除（members / invitations は CASCADE） |
| POST | `/api/groups/{gid}/invite-code/rotate` | グループ admin | 招待コードの再発行 |
| POST | `/api/groups/{gid}/members` | グループ admin | ユーザーを直接招待（username / email 指定） |
| DELETE | `/api/groups/{gid}/members/{uid}` | admin または本人 | 退会/除名（非 admin は自分自身のみ） |
| POST | `/api/groups/join-by-code` | 要ログイン | 招待コードで参加 |
| GET | `/api/groups/{gid}/invitations` | グループ admin | グループの招待一覧 |
| GET | `/api/me/invitations` | 本人 | 自分宛ての pending 招待一覧 |
| POST | `/api/me/invitations/{inv}/accept` | 本人 | 招待を承諾 |
| POST | `/api/me/invitations/{inv}/decline` | 本人 | 招待を辞退 |

---

### 管理 `/api/admin`（`routes/admin.py`）

教材・コース・スキーマ・ユーザー管理の中核ルーター（43 エンドポイント）。
手順の正本: [admin_operations/materials.md](../admin_operations/materials.md) /
[admin_operations/course.md](../admin_operations/course.md) /
[admin_operations/users.md](../admin_operations/users.md)（グループ管理） /
[admin_operations/students.md](../admin_operations/students.md) /
[admin_operations/teachers.md](../admin_operations/teachers.md) /
[admin_operations/system.md](../admin_operations/system.md)。

#### 教材管理・タスク

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/materials/upload` | TEACHER | PDF/TeX を MinIO へ保存しバックグラウンドで解析パイプライン起動、task_id を即時返却（`analyze_images` で装置図解析をオプトイン） |
| POST | `/api/admin/documents/{id}/reanalyze` | TEACHER + document 編集権（所有者 / document editor / SYSTEM_ADMIN） | 保存済み PDF を Agent パイプラインで再解析（`analyze_images` 未指定時は前回 run の options を継承）。認可は MinIO 取得・background task 起動より前。閲覧のみ（public / viewer / コース経由）と不明 ID は同一 404 |
| GET | `/api/admin/materials` | TEACHER（自分 + public + group/document 共有 + コース参照） | 教材一覧（status は projector 正本。`?include=summary` でサマリ付加） |
| GET | `/api/admin/materials/{id}` | TEACHER（一覧と同じポリシー） | 教材詳細（ナレッジグラフ含む） |
| GET | `/api/admin/materials/{id}/pdf` | TEACHER（閲覧権） | 教材 PDF を MinIO からプロキシ配信 |
| PUT | `/api/admin/materials/{id}/pdf` | TEACHER + document 編集権（所有者 / document editor / SYSTEM_ADMIN） | PDF の再登録（テキスト類似度 <0.3 は 409。欠落ページ情報を推定補完）。認可はファイル読取・PDF パース・MinIO upload より前。閲覧のみ（public / viewer / コース経由）と不明 material は同一 404 |
| PUT | `/api/admin/materials/{id}/visibility` | 教材所有者のみ | 開示範囲（public/group/private）を更新 |
| DELETE | `/api/admin/materials/{id}` | 教材所有者 + `confirm_name` 一致必須 | 教材削除（参照コース・チャンク・W層孤児行も削除、V層 teardown 通知） |
| GET | `/api/admin/tasks/{task_id}` | TEACHER | バックグラウンドタスクのステータス（ポーリング用） |

#### コースビルダー

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/course-builder/chat` | TEACHER | 選択教材の theory_components 等を注入して AI とコース設計対話、course_draft JSON を抽出 |
| POST | `/api/admin/course-builder/sessions` | TEACHER | セッション新規作成 |
| GET | `/api/admin/course-builder/sessions` | TEACHER（本人のみ） | セッション一覧（更新日時降順） |
| GET | `/api/admin/course-builder/sessions/{sid}` | TEACHER（本人のみ） | セッション詳細（チャット履歴・course_draft） |
| PUT | `/api/admin/course-builder/sessions/{sid}` | TEACHER（本人のみ） | タイトル・履歴・draft・status の更新 |

#### コース管理・公開・グループ共有

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses` | TEACHER（所有 + group 共有 editor/viewer） | 管理可能なコース一覧（role・公開状態・atlas binding 状況付き） |
| GET | `/api/admin/courses/{cid}/draft-format` | 所有者 or editor | 登録済みコースを course_draft 形式へ変換（ビルダー再インポート用） |
| PUT | `/api/admin/courses/{cid}/visibility` | コース所有者のみ | 開示範囲更新（`is_published` を実態と同期） |
| DELETE | `/api/admin/courses/{cid}` | 所有者 or editor + `confirm_name` 一致必須 | コース削除（チャット履歴・group 権限行も明示削除、V層 teardown 通知） |
| GET | `/api/admin/courses/{cid}/unanswered-queries` | TEACHER + コース編集権（所有者 / course editor / SYSTEM_ADMIN） | コースの RAG 未回答クエリ（最大200件・学生表示名を含む）。認可は SQL 実行より前。権限なし・不明コースは空配列ではなく同一 404 |
| GET | `/api/admin/courses/{cid}/groups` | 閲覧可能な者 | コースのグループ権限一覧 |
| POST | `/api/admin/courses/{cid}/groups` | コース所有者のみ | グループ権限（viewer/editor）の付与・更新 |
| DELETE | `/api/admin/courses/{cid}/groups/{gid}` | コース所有者のみ | グループ権限の削除 |

#### ドキュメント × グループ共有（パイプライン成果共有、migration 044）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/groups` | 閲覧可能な者 | ドキュメントのグループ共有設定一覧 |
| POST | `/api/admin/documents/{id}/groups` | ドキュメント所有者のみ | 解析成果のグループ共有（viewer/editor。`document_share` 監査記録） |
| DELETE | `/api/admin/documents/{id}/groups/{gid}` | ドキュメント所有者のみ | グループ共有の解除（監査記録あり） |

#### 図画像配信（L層、migration 041）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/figures/{fid}/image` | TEACHER + document 閲覧権 | 図画像本体（PNG）を MinIO `figure-images` から配信 |

※ 図の**一覧** `GET /documents/{id}/figures` は admin.py にも関数定義が残るが非配信
（main.py がルート除去。配信される正本は下記 figure_presentation 節）。

#### ユーザー管理・システム統計

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/users/student` | TEACHER | 学生（learner）アカウント作成（重複 409） |
| POST | `/api/admin/users/teacher` | SYSTEM_ADMIN | 教員（instructor）アカウント作成 |
| GET | `/api/admin/system/materials-stats` | SYSTEM_ADMIN | 全コースのパイプライン進捗・受講者数・チャット数 |

#### スキーマ進化（[動的スキーマ進化](../pipeline/schema-evolution.md)）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/schema/types` | TEACHER | 登録済み OntologyType 一覧 |
| POST | `/api/admin/schema/types` | TEACHER | OntologyType 追加 |
| GET | `/api/admin/schema/predicates` | TEACHER | 登録済み CorePredicate 一覧 |
| POST | `/api/admin/schema/predicates` | TEACHER | CorePredicate 追加 |
| GET | `/api/admin/schema-proposals` | TEACHER | スキーマ拡張提案一覧（`?status=` フィルタ可） |
| POST | `/api/admin/schema-proposals/analyze` | TEACHER | 未回答クエリの LLM 分析 → 提案生成 |
| POST | `/api/admin/schema-proposals/{pid}/simulate` | TEACHER | Shadow Testing（Target/Similar/Control への試験適用と差分） |
| PUT | `/api/admin/schema-proposals/{pid}/approve` | TEACHER | 提案承認 → Type/Predicate 登録 + 再抽出ジョブのエンキュー |
| PUT | `/api/admin/schema-proposals/{pid}/approve-with-scope` | TEACHER | スコープ付き承認（full / canary=指定コースのみ） |
| PUT | `/api/admin/schema-proposals/{pid}/reject` | TEACHER | 提案却下 |
| GET | `/api/admin/reextraction-jobs` | TEACHER | 再抽出ジョブ一覧 |

#### 教員向け集約ダッシュボード（B層 / 個人知識ネットワーク Phase B）

手順の正本: [admin_operations/interest_dashboard.md](../admin_operations/interest_dashboard.md)。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/interest-dashboard` | TEACHER | interest_traces の集団集計（件数・比率・関与人数のみ。個人特定情報なし） |
| GET | `/api/admin/courses/{cid}/bridge-insights` | TEACHER + コース編集権（所有者 / course editor / SYSTEM_ADMIN） | 学習者が connect した橋候補の k-匿名集約（k=3・人数レンジ表示）。認可は `aggregate_bridge_candidates()` より前（権限のない教員へ集約の存在・空非空を開示しない）。権限なし・不明コースは同一 404 |

### 図の表示分類 `/api/admin`（`routes/figure_presentation.py`、#496）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/figures` | TEACHER + document 閲覧権 | 図一覧 + 分類（機能構成図/データグラフ/解説画像）・装置候補・bbox・`viewer_is_owner` を返す（admin.py の旧ハンドラを置換） |
| PATCH | `/api/admin/documents/{id}/figures/{fid}/presentation-mode` | TEACHER + document 編集権 | 教員による表示モード上書き（`null` で解除し提案値に復帰。監査記録あり） |
| POST | `/api/admin/documents/{id}/figures/{fid}/reanalyze` | TEACHER + document 編集権 | 教員指示付き図再解析（`hint_text` / `focus_bbox`。AI 候補生成のみで自動確定しない） |

### 要素説明のレビュー `/api/admin`（`routes/element_explanations.py`、migration 056/062）

パイプライン（ContextualExplanationAgent / DiscussOpeningAgent 等）が書く `candidate` を教員が
確認・承認・却下・編集するためのルーター。全エンドポイント TEACHER + document の
閲覧（GET）/ 編集（承認・却下・編集）権。**DELETE endpoint は作らない**（P4。本文編集は旧行を
`superseded` に遷移させて新 revision 行を作る）。詳細は
`docs/features/hierarchical_context_explanation_design.md` §5.2 /
`docs/features/discuss_opening_authoring_design.md` §7.1。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/element-explanations` | TEACHER + document 閲覧権 | document 内の説明一覧（レビューキューの供給元。`element_type` / `status` / `kind` / `role` で絞り込み可）。開幕素材（`element_type='document'` / `role='discussion_seed'`）には鮮度 `stale` / `stale_notice` を付ける（approved も対象。自動で非承認へは落とさない） |
| POST | `/api/admin/element-explanations/{eid}/approve` | TEACHER + document 編集権 | `candidate → approved`（監査記録あり） |
| POST | `/api/admin/element-explanations/{eid}/dismiss` | TEACHER + document 編集権 | `candidate → dismissed`（行は保持） |
| POST | `/api/admin/documents/{id}/element-explanations/bulk-review` | TEACHER + document 編集権 | 一括承認 / 一括却下（`action` は `approve` / `dismiss`、それ以外は 422）。1回あたり最大200件、遷移できなかった行は `skipped` に理由付きで返す部分成功セマンティクス |
| PATCH | `/api/admin/element-explanations/{eid}` | TEACHER + document 編集権 | 本文編集（旧行を `superseded` に遷移させ新 revision 行を作る。履歴保持） |

### エラーログ（`routes/error_logs.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/error-logs` | SYSTEM_ADMIN | 直近ログ一覧（keyword / minutes / limit / include_info で絞り込み。5xx は middleware が記録） |

### 原稿スタジオ（`routes/lecture_studio/` パッケージ、Tier 3-17a）

`scripts.py` / `pipeline.py` / `topics.py` の3ルーター（いずれも自前 prefix なし）を `__init__.py` が束ね、
`/api/admin` にマウント。全エンドポイント TEACHER。コース単位の追加ゲートは
`get_viewable_course_data`（閲覧）/ `get_editable_course_data`（所有者 + editor グループ）。
手順の正本: [admin_operations/lecture_studio.md](../admin_operations/lecture_studio.md)。

#### scripts.py — 設定・原稿生成・音声生成

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses/{cid}/lecture-studio/settings` | TEACHER + コース閲覧 | コース単位設定（口調ペルソナ + 読み上げ言語）の取得 |
| PUT | `/api/admin/courses/{cid}/lecture-studio/settings` | TEACHER + コース編集 | 設定保存（変更時は原稿再生成フラグ。language 省略は変更しない） |
| POST | `/api/admin/courses/{cid}/lecture-scripts/generate` | TEACHER + コース編集 | 全チャンクの display/spoken_text・数式を非同期一括生成（202。`auto_audio` で音声チェーン可。チャンク0件は 422） |
| GET | `/api/admin/courses/{cid}/lecture-scripts` | TEACHER + コース閲覧 | チャンク原稿一覧（スライド数・音声準備状況付き） |
| POST | `/api/admin/lecture-studio/preview-split` | TEACHER | スライド分割プレビュー（配信側と同一の `core/lecture.py::split_slides`。DB 非変更）。応答の各 slide に WMレンズ（`core/lecture_wm.py`、教員支援 Phase 4 §3.2）の相互作用性段階 `wm: {level, level_label, fact, degraded?}` を相乗り — 最低段のスライドには wm キー自体を付けず、optional `document_id` 省略時は textual 照合へ縮退（degraded） |
| PUT | `/api/admin/chunks/{chunk_id}/lecture-script` | TEACHER | 教員編集の原稿・数式を保存し当該チャンクの音声キャッシュを無効化 |
| POST | `/api/admin/chunks/{chunk_id}/lecture-script/rewrite` | TEACHER | 教員指示に基づく LLM 原稿書き換え |
| POST | `/api/admin/courses/{cid}/lecture-audio/generate` | TEACHER + コース編集 | スライド単位 TTS 音声の非同期一括生成（202。空 spoken_text ありは 422、言語切替チェーン時は免除） |
| GET | `/api/admin/chunks/{chunk_id}/lecture-audio` | TEACHER | スライド単位音声の試聴配信（キャッシュのみ・未生成 404） |

#### pipeline.py — Agent Pipeline・再解析・コース内容生成

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/courses/{cid}/structure/reanalyze` | TEACHER + コース編集 | 既存チャンク維持のまま構造 DSL/変数を非同期再解析（進行中 409） |
| POST | `/api/admin/courses/{cid}/document-pipeline/run` | TEACHER + コース編集 | コース配下全教材の Agent Pipeline 起動（target_stage/start_stage 指定可） |
| GET | `/api/admin/materials/{mid}/document-pipeline/status` | TEACHER + アップロード者本人 or SYSTEM_ADMIN | 教材単位のパイプライン実行状態（stage 別・縮退情報・進行中タスク） |
| POST | `/api/admin/materials/{mid}/document-pipeline/run` | TEACHER + アップロード者本人 or SYSTEM_ADMIN | 教材単位の Agent Pipeline 起動（stage 単独再実行可。進行中 409） |
| POST | `/api/admin/courses/{cid}/course-content/generate` | TEACHER + コース編集 | パイプライン成果物からコース内容（章・トピック）を非同期再生成 |
| GET | `/api/admin/courses/{cid}/tasks/active` | TEACHER + コース閲覧 | 進行中バックグラウンドタスク最新1件（ポーリング再開用） |

#### topics.py — 章・トピック構造・授業用ドラフト

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses/{cid}/lecture-studio/course-structure` | TEACHER + コース閲覧 | 章・トピック構造（原稿/音声生成ステータス集計付き） |
| PUT | `/api/admin/courses/{cid}/lecture-studio/course-topics/{tid}` | TEACHER + コース所有者本人 or SYSTEM_ADMIN | トピックの授業用ドラフト（教材・読み上げ原稿・確認問題）保存 + トピック音声キャッシュ無効化 |
| POST | `/api/admin/courses/{cid}/lecture-studio/course-topics/{tid}/draft/rewrite` | TEACHER + コース閲覧 | LLM によるドラフト案生成（保存は別 PUT。DB 非変更） |
| GET | `/api/admin/courses/{cid}/lecture-studio/document-structure` | TEACHER + コース閲覧 | コース教材の文書構造（Agent 復元構造優先） |
| GET | `/api/admin/courses/{cid}/lecture-studio/components` | TEACHER + コース閲覧 | 理論コンポーネント一覧・依存グラフ・解析ステータスの集約 |

### 教材図スタジオ（`routes/teaching_figures.py`、migration 063）

AI 対話で説明図（SVG）を作り、既存の `![[figure:id]]` 記法で教材へ埋め込む層。全エンドポイント
TEACHER で、**書き込み系はコース所有者 / SYSTEM_ADMIN のみ**（`course_data_for_owner`。editor 共有教員は
403）、**読み取り系は editor 共有教員にも開く**（`_course_data_for_studio_editable`）。保存の唯一の入口は
`core/teaching_figures/sanitizer.py`（外部参照 / script / foreignObject / image / on* を拒否 = 422）。
行削除 API は無い（`draft` / `adopted` / `retired` の状態遷移のみ）。詳細は
`docs/features/teaching_figure_studio_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/courses/{cid}/figure-studio/turn` | TEACHER + コース所有者 | 図（SVG）を生成・修正する1ターン（DB 非変更）。LLM 失敗・サニタイズ失敗は 500 にせず `degraded=true` の 200 で返し、プレビューは前回版を保つ |
| POST | `/api/admin/courses/{cid}/teaching-figures` | TEACHER + コース所有者 | SVG をサニタイズして保存（拒否は 422）。`adopt=true` なら §7.1b の採用まで実行（本文挿入 + `topic.linked_figure_ids` / `evidence_links` 登録）。MinIO 配信スナップショットの失敗は `image_snapshot_failed: true` で正直に返す |
| PATCH | `/api/admin/courses/{cid}/teaching-figures/{fid}` | TEACHER + コース所有者 | 図の修正・状態遷移。`svg_source` 差し替えは再サニタイズ + 旧版を `revisions` に append、`draft → adopted` はトピック側登録も実行、`adopted → retired` はトピック側登録を**削除しない**（生 UUID 露出を防ぐ）。`register_topic_id` で別トピックへの参照登録のみも可（冪等） |
| GET | `/api/admin/courses/{cid}/teaching-figures` | TEACHER + コース編集権（editor 共有可） | コースの生成図一覧（draft を含む。挿入タブ・ストック表示用） |
| GET | `/api/admin/courses/{cid}/teaching-figures/{fid}/image` | TEACHER + コース編集権（editor 共有可） | 教員向け図画像配信（draft も見える）。正本は DB の `svg_source` で、MinIO 取得失敗時も DB の SVG で配信。SVG は `nosniff` + CSP sandbox 付き |
| POST | `/api/admin/courses/{cid}/topics/{tid}/figure-suggestions/generate` | TEACHER + コース所有者 | 「図で補うとよい箇所」候補の生成（単発 LLM・candidate のみ）。学習者信号は k-匿名集約のレンジ・段階ラベルのみを LLM に渡す |
| GET | `/api/admin/courses/{cid}/topics/{tid}/figure-suggestions` | TEACHER + コース編集権（editor 共有可） | candidate + accepted の提案一覧（段階ラベルのみ） |
| POST | `/api/admin/courses/{cid}/figure-suggestions/{sid}/accept` | TEACHER + コース所有者 | 提案を採択済みにする（行削除しない） |
| POST | `/api/admin/courses/{cid}/figure-suggestions/{sid}/dismiss` | TEACHER + コース所有者 | 提案を却下（`dismissed` 遷移で保持） |

### 理論コンポーネント + 承認・共有レイヤー（`routes/theory_components.py`、C層）

全エンドポイント TEACHER。document 単位は `_ensure_document_viewable/editable`
（所有者 / public / group 共有 / document 共有 / 閲覧可コース経由。SYSTEM_ADMIN は無条件）、
コース単位は course の閲覧/編集ゲート。詳細は [承認・共有レイヤー](../features/endorsement-sharing.md)。

#### A層成果の閲覧（structure / claims / component-graph）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{id}/structure` | TEACHER + document 閲覧権 | チャンクからセクション構造を構築して返す |
| GET | `/api/admin/documents/{id}/chunks/{chunk_id}/claims` | TEACHER + document 閲覧権 | チャンク単位の theory_claims（0件ならドキュメント全体にフォールバック） |
| GET | `/api/admin/documents/{id}/sections/{sid}/components` | TEACHER + document 閲覧権 | セクション単位の theory_components（0件ならドキュメント全体にフォールバック） |
| GET | `/api/admin/documents/{id}/component-graph` | TEACHER + document 閲覧権 | 保存済み TheoryOperationGraph の正規化返却（無ければ決定論的に構築） |
| PATCH | `/api/admin/claims/{claim_id}` | TEACHER + document 編集権 | claim の全項目更新（review_status 遷移は監査、rejected は伝播、承認時は R層 item オーサリングを非同期起動） |

#### 理論コンポーネント CRUD（コース単位）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/courses/{cid}/theory-components` | TEACHER + コース閲覧 | コンポーネント一覧（`?chunk_id` 絞込可。0件時は chunk→document→sources の順にフォールバック） |
| POST | `/api/admin/chunks/{chunk_id}/theory-components/extract` | TEACHER + 当該教材を含む編集可能コース | SMILES DSL からの構造抽出（`use_llm` で LLM 補強）を candidate として upsert |
| POST | `/api/admin/courses/{cid}/theory-components` | TEACHER + コース編集 | コンポーネント手動作成（重複候補を自動付与） |
| PUT | `/api/admin/theory-components/{comp_id}` | TEACHER + コース編集 | 全項目更新（review_status 遷移は監査・rejected 伝播） |
| POST | `/api/admin/theory-components/{comp_id}/reject` | TEACHER + コース編集 | rejected 遷移（行削除しない）+ グラフエッジ無効化 |
| POST | `/api/admin/courses/{cid}/theory-components/validate-connection` | TEACHER + コース閲覧 | 2コンポーネント間の接続妥当性を非LLM検証（warnings を返す） |

#### C層: 説明バージョン・承認・引用

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/theory-components/{comp_id}/explanations` | TEACHER + コース閲覧 | 説明バージョン一覧（standard 無ければ summary から遅延生成） |
| POST | `/api/admin/theory-components/{comp_id}/explanations` | TEACHER + コース閲覧 | `kind='personal'` の独自解釈を作成（監査記録） |
| PATCH | `/api/admin/explanations/{eid}` | 作者本人 or SYSTEM_ADMIN | title/body/backing_claims/shared/review_status の部分更新（backing_claims の確定/却下も監査） |
| POST | `/api/admin/explanations/{eid}/endorse` | TEACHER + コース閲覧 | 承認を upsert（level=provisional/endorsed/strong。再承認で revoked 解除） |
| DELETE | `/api/admin/explanations/{eid}/endorse` | TEACHER（自分の承認行のみ） | 承認取り消し（`revoked=TRUE`。行削除せず履歴保持） |
| GET | `/api/admin/explanations/{eid}/endorsements` | TEACHER + コース閲覧 | 有効な承認一覧 + 集計 + 段階ラベル |
| POST | `/api/admin/explanations/{eid}/cite` | TEACHER + shared or 自分の説明 + 引用先コース編集権 | 説明を自コースへ帰属付き引用（V層の版固定 + auto-pin を best-effort 実行） |
| GET | `/api/admin/courses/{cid}/sharing-dashboard` | TEACHER + コース閲覧 | 承認・共有・引用の集団集計（個人追跡ではない） |
| POST | `/api/admin/theory-components/candidates/from-query` | TEACHER + コース編集 | 質問 + AI 回答からコンポーネント候補を生成（candidate + `confirmed=False` の backing_claims。確定は教員のみ） |

### カートリッジ `/api/admin/cartridges`（`routes/cartridges.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/cartridges` | TEACHER | 利用可能カートリッジのサマリ一覧 |
| GET | `/api/admin/cartridges/{id}` | TEACHER | カートリッジ全体（manifest + ontology + 各種定義） |
| GET | `/api/admin/cartridges/{id}/ontology` | TEACHER | ontology 部分のみ |
| GET | `/api/admin/cartridges/{id}/component-types` | TEACHER | component type 語彙 |
| GET | `/api/admin/cartridges/{id}/relation-types` | TEACHER | relation type 語彙 |
| GET | `/api/admin/cartridges/{id}/maturity-levels` | TEACHER | 成熟度レベル定義 |
| GET | `/api/admin/cartridges/{id}/support-statuses` | TEACHER | サポートステータス定義 |

### リビジョン（`routes/revisions.py`、マイグレーション 019）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/documents/{id}/revisions` | TEACHER | ドキュメントのリビジョン作成 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/run` | TEACHER | リビジョンの再解析実行（非同期） |
| GET | `/api/admin/documents/{id}/revisions/{rid}/run-status` | TEACHER | 実行状態の取得 |
| GET | `/api/admin/documents/{id}/revisions` | TEACHER | リビジョン一覧 |
| GET | `/api/admin/documents/{id}/revisions/{rid}` | TEACHER | 単一リビジョン取得 |
| GET | `/api/admin/documents/{id}/revisions/{rid}/report` | TEACHER | 差分レポート取得 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/accept` | TEACHER | 受理 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/reject` | TEACHER | 棄却 |
| POST | `/api/admin/documents/{id}/revisions/{rid}/revise` | TEACHER | 差し戻し（再修正） |

### 分野の地図 — 管理（`routes/atlas.py` router / admin_atlas_router / binding_router）

手順の正本: [admin_operations/atlas.md](../admin_operations/atlas.md)。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/atlas/domains` | TEACHER | 骨格を持つ（または draft 中の）domain 一覧（migration 027） |
| GET | `/api/admin/cartridges/{cid}/atlas/skeleton` | TEACHER | 骨格のレビュー状態（draft / 凍結済み。DB が正本） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/generate` | TEACHER | 骨格 draft の LLM バッチ生成（再実行は `force` 明示。カートリッジ無し新分野は `body.domain` で可） |
| PUT | `/api/admin/cartridges/{cid}/atlas/skeleton/draft` | TEACHER | draft 保存（`revision` 楽観ロック、衝突 409） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/draft/from-frozen` | TEACHER | 現行凍結版を複製して次版 draft を作る（決定論・LLM 不使用。既存 draft あり / retired は 409、凍結版なしは 404）。node_id を振り直さないため既存の binding・配置・足跡が切れない |
| DELETE | `/api/admin/cartridges/{cid}/atlas/skeleton/draft` | TEACHER | draft の破棄（作業コピーのため AB3 の対象外。**retired 中も許可**する唯一の書き込み経路。凍結版履歴・学習者表示に影響しない） |
| GET | `/api/admin/cartridges/{cid}/atlas/freeze-impact` | TEACHER | 凍結前の影響プレビュー（draft ⇄ 現行凍結版の node_id 差分 + バインド中コースの topic 影響。draft なしは 404） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/freeze` | TEACHER | draft の凍結・版付与（レスポンスに `impact` 同梱。関係教員へ `atlas_skeleton_frozen` 通知） |
| POST | `/api/admin/cartridges/{cid}/atlas/retire` | TEACHER | domain を retired にする（削除ではなく状態遷移。propose 候補から除外・generate/draft 保存/freeze は 409。学習者表示は不変。関係教員へ best-effort 通知） |
| POST | `/api/admin/cartridges/{cid}/atlas/restore` | TEACHER | retired → active へ戻す（監査のみ・通知なし。retired でなければ 409） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/assist/interpret` | TEACHER | AI アシスト: 教員の発言を対象・要望に解釈（まだ編集しない） |
| POST | `/api/admin/cartridges/{cid}/atlas/skeleton/assist/propose` | TEACHER | AI アシスト: 確定済み解釈から編集案（JSON Patch）生成（draft は書き換えない） |
| GET | `/api/admin/cartridges/{cid}/atlas/reports` | TEACHER | 修正報告のレビューキュー |
| POST | `/api/admin/cartridges/{cid}/atlas/reports/{rid}/resolve` | TEACHER | 報告の採用/見送り確定（報告者へ結果通知） |
| POST | `/api/admin/cartridges/{cid}/atlas/reports/{rid}/incorporate` | TEACHER | 報告内容の骨格 draft への取り込み |
| POST | `/api/admin/cartridges/{cid}/atlas/overlay/refresh` | TEACHER | `atlas_overlay_cache` の状態導出バッチを明示実行 |
| POST | `/api/admin/courses/{cid}/atlas-binding/propose` | TEACHER | コース→地図配置の決定論的提案（LLM 不使用。教員が保存するまで確定しない） |
| PUT | `/api/admin/courses/{cid}/atlas-binding` | TEACHER | 承認済みバインディング保存（`cartridge_id` + `topics[].atlas_node_id`。監査記録あり。保存で pending は自動クリア） |
| PUT | `/api/admin/courses/{cid}/atlas-binding/pending` | TEACHER | 凍結待ちドメインの仮予約（`course_data.atlas_binding_pending`。コースの地図表示には影響しない。domain_key は英小文字・数字・アンダースコアのみ＝それ以外は 422） |
| DELETE | `/api/admin/courses/{cid}/atlas-binding/pending` | TEACHER | 仮予約の取り消し（予約が無くても 200 の冪等） |

### カテゴリギャップ候補（`routes/atlas_gaps.py`、migration 066）

論文の解析で「置けなかった」主題のうち、2論文以上で反復したものだけをレビュー候補として
**毎回読み時導出**する（候補行を蓄積しない）。骨格 draft を書くのは教員の既存
`PUT .../atlas/skeleton/draft` だけで、本ルーターは骨格に書き込まない（KN-3 / AB4）。
詳細は `docs/features/category_gap_candidates_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/cartridges/{cid}/atlas/gap-candidates` | TEACHER | 候補一覧（毎回導出。生 confidence・件数フィールドなし、支持論文はタイトル列挙）+ `skeleton_version` / `draft_exists` / `draft_revision`。`include_dismissed=true` で見送り済み・統合済みも返す |
| POST | `/api/admin/cartridges/{cid}/atlas/gap-candidates/decide` | TEACHER | 候補を採用・見送りにする（見送りの取り消しも同 API。見送りは理由必須）。骨格 draft は変わらない |
| POST | `/api/admin/cartridges/{cid}/atlas/gap-candidates/incorporate-preview` | TEACHER | 採用済み候補を次版 draft へ追加する JSON Patch（op は add のみ）と適用後 draft の**提示のみ**。DB 非変更。未採用 / draft なしは 409、満杯領域・親領域不在は 422 |
| POST | `/api/admin/cartridges/{cid}/atlas/gap-candidates/mark-incorporated` | TEACHER | 教員の `PUT draft` **成功後**に取り込み先 node を刻印。`draft_node_id` が現 draft に無ければ 409（誤順序を弾く） |

### 知識ランドスケープ（`routes/landscape.py`、migration 065）

論文（document）を分野の地図（atlas 骨格の凍結版）のアンカーへ複数観点で配置する層。
管理側は `/api/admin/landscape/...`、学習者向けは `/api/learning/courses/{cid}/landscape`。
**DELETE ルートは無い**（却下は `status='rejected'`、再解析での置換は `superseded` で保持）。
レスポンスは必ず `core.landscape.projection` の DTO を通し、`weight` / `confidence` はキーごと出さない
（教員向けも同じ）。不在・権限なしは 404 に統一する（403 を使わない）。詳細は
`docs/features/knowledge_landscape_design.md` §9 / `docs/features/release_review_flow_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/landscape/documents/{ref}/placements` | TEACHER + document 閲覧権 | 1論文の配置一覧（`document_ref` は UUID / source_path 両対応）。`unplaced_domains` / 骨格版 / `last_run_at` / `gap_signals_recorded`（真偽値のみ・件数なし）を同梱。`include_history=true` で `superseded` も返す |
| PATCH | `/api/admin/landscape/placements/{pid}` | TEACHER + document 編集権 | 配置の status 遷移（確認 / 却下 / 再検討）。`superseded` を指す・履歴行を動かすは 422、行なし・document 不可視は 404。監査 `entity_type='landscape_placement'` |
| POST | `/api/admin/landscape/documents/{ref}/placements/propose` | TEACHER + document 編集権 | 配置候補の作り直し（パイプラインと同一ビルダー・同一日次予算）。日次上限は 429、素材なし・骨格なしは 422。detail は数値を含まない事実文 |
| GET | `/api/admin/landscape/courses/{cid}/placements` | TEACHER（edit 不能なソース論文は静かに除外） | コースのソース論文の live 配置（リリース前の確認 ステップ2）。document 別に `editable` / `unplaced_domains`、未確認件数 `pending_count` を返す |
| POST | `/api/admin/landscape/courses/{cid}/placements/accept` | TEACHER + ソース論文の編集権 | 「次へ」= 一括確認。**edit 権限のある document の `inferred` のみ** `confirmed` へ。個別に却下・再検討された行は動かさない。権限外は除外件数として返す。監査は `action='accept_on_release'` |
| GET | `/api/admin/landscape/overview` | TEACHER | 本人可視 document の live 配置をノード別に集約（`domain_key` 必須。凍結骨格なしは 404）。骨格に無いノードの配置は集約に載せない |
| GET | `/api/learning/courses/{cid}/landscape` | 受講ゲート（`get_accessible_course_data`） | 学習者向け「論文の位置づけ」。対象はコース sources のみ・status は `confirmed` / `inferred` / `review_required` のみ。配置ゼロ・骨格なしでも 200 で空構造（非表示への縮退はフロント責務）。`unplaced_documents` / `skeleton_version` を同梱し、weight / confidence / claim_id は投影が構造的に落とす |

### D層 — 管理 `/api/admin/doubt`（`routes/doubt.py` admin_router）

全34本が TEACHER（metrics のみ SYSTEM_ADMIN）。withdraw（疑義者本人）と反実仮想 PATCH（作成者本人）を除き、
course_id / target_id への所有・共有チェックは行わない（ロールゲートのみ）。
手順の正本: [admin_operations/doubt.md](../admin_operations/doubt.md)。
賭け金の台帳（SL層, migration 067）で反証条件・観測反実仮想の7本が加わっている
（下表の「反証条件（SL層）」節ほか）。

#### 台帳（Epistemic Ledger）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/doubt/ledger/{ttype}/{tid}` | TEACHER | 台帳行 + 検証スコープ + LLM 候補 + 合意2軸 + 負荷段階 + 疑義一覧（無ければ 404） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/scopes` | TEACHER | 検証スコープの記帳（4軸のうち1つ以上 + 根拠 + reason 必須。監査記録） |
| PATCH | `/api/admin/doubt/ledger/{ttype}/{tid}/scopes/{sid}` | TEACHER | 既存スコープの訂正（旧値は監査イベントに保持） |
| PUT | `/api/admin/doubt/ledger/{ttype}/{tid}/verification-status` | TEACHER | 検証状態の変更（`directly_verified` 昇格はスコープ1件以上必須 = 全称検証の構造的禁止） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/scope-candidates/{cid}/confirm` | TEACHER | LLM スコープ候補の確定（教員の帰属で本体へ転記。候補行は保持） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/scope-candidates/{cid}/dismiss` | TEACHER | LLM スコープ候補の却下（`dismissed` で保持） |
| GET | `/api/admin/doubt/courses/{cid}/ledger-summary` | TEACHER | コース単位の台帳サマリ（スコープあり/空欄の件数を事実として返す） |
| POST | `/api/admin/doubt/courses/{cid}/scope-candidates/refresh` | TEACHER | スコープ候補生成の非同期スケジュール（同期パスに LLM を入れない） |

#### 反証条件（SL層、migration 067）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/falsification-conditions` | TEACHER | 反証条件の手動記帳（人間専用の記帳先。statement / kind / reason / 根拠 / reachability を必須検証） |
| PATCH | `/api/admin/doubt/ledger/{ttype}/{tid}/falsification-conditions/{cond_id}` | TEACHER | 反証条件の訂正（訂正後も必須項目を再検証） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/falsification-candidates/{cid}/confirm` | TEACHER | LLM 候補の確定（候補行は `confirmed` で保持し、教員の帰属で新規 FalsificationCondition を発行。候補が本体へ直接入らない） |
| POST | `/api/admin/doubt/ledger/{ttype}/{tid}/falsification-candidates/{cid}/dismiss` | TEACHER | LLM 候補の却下（`dismissed` で保持） |
| POST | `/api/admin/doubt/courses/{cid}/falsification-candidates/refresh` | TEACHER | 反証条件候補生成の非同期スケジュール（同期パスに LLM を入れない） |
| GET | `/api/admin/doubt/courses/{cid}/observation-targets` | TEACHER | 「観測を仮に倒す」の選択肢となる観測系 claim 一覧（`identified_via` 併記・数値なし） |

#### 素朴な問い・負荷度・暗黙前提

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/doubt/courses/{cid}/naive-signals` | TEACHER | anchor 単位の k-匿名集計（k=3・n<3 非表示・件数レンジのみ） |
| POST | `/api/admin/doubt/courses/{cid}/load/recompute` | TEACHER | 負荷度の決定論的バッチ再計算（生スコアは返さない） |
| GET | `/api/admin/doubt/courses/{cid}/assumptions` | TEACHER | 前提一覧（status フィルタ + 学習者シグナル有無を合成） |
| POST | `/api/admin/doubt/assumptions` | TEACHER | 前提の手動登録（即 confirmed、台帳行を `untested`・スコープ空欄で自動生成） |
| POST | `/api/admin/doubt/assumptions/{aid}/confirm` | TEACHER | candidate 前提の確定（reason 必須。台帳行を自動生成） |
| POST | `/api/admin/doubt/assumptions/{aid}/dismiss` | TEACHER | 前提候補の却下（`dismissed` 遷移で保持） |
| POST | `/api/admin/doubt/courses/{cid}/assumption-mining/run` | TEACHER | 経路A（導出の隙間の反復）マイニングの非同期スケジュール |
| POST | `/api/admin/doubt/courses/{cid}/corpus-audit/run` | TEACHER | 経路B（コーパス横断監査）の同期実行（非LLM） |
| GET | `/api/admin/doubt/courses/{cid}/assumption-atlas` | TEACHER | 前提の地図（負荷度×検証度の散布データ。生スコア・評価語なし） |

#### 疑義・検証提案・反実仮想・KPI

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/doubt/targets/{ttype}/{tid}/challenges` | TEACHER | 疑義の作成（assumption/claim のみ。challenge_type + reason 必須・匿名不可・監査記録） |
| GET | `/api/admin/doubt/targets/{ttype}/{tid}/challenges` | TEACHER | 対象への疑義一覧（数値スコア化しない） |
| POST | `/api/admin/doubt/challenges/{chid}/withdraw` | 疑義者本人のみ | 疑義の取り下げ（`withdrawn` 遷移で履歴保持） |
| POST | `/api/admin/doubt/challenges/{chid}/proposals` | TEACHER | 疑義 → 検証提案への昇格（元 challenge を `led_to_verification` に遷移） |
| PATCH | `/api/admin/doubt/proposals/{pid}` | TEACHER | 検証提案のステータス遷移（proposed→in_progress→completed の前進 + 任意時点から withdrawn）と reachability の更新 |
| GET | `/api/admin/doubt/courses/{cid}/open-assumptions` | TEACHER | 未検証合意リスト（台帳の自動編纂・編集不可。教員版=疑義者名あり） |
| POST | `/api/admin/doubt/counterfactual/compute` | TEACHER | 保存なしの反実仮想試算（collapsed/surviving/indeterminate の決定論的伝播） |
| POST | `/api/admin/doubt/counterfactual/sessions` | TEACHER | 反実仮想セッションの保存（shared_scope 既定 private） |
| GET | `/api/admin/doubt/counterfactual/sessions` | TEACHER（自分所有 + public + 所属グループ共有のみ） | セッション一覧（`course_id` 絞り込み可） |
| PATCH | `/api/admin/doubt/counterfactual/sessions/{sid}` | 作成者本人のみ | notes・共有範囲の変更（scope 変更時のみ監査記録） |
| GET | `/api/admin/doubt/metrics` | SYSTEM_ADMIN | 運用判断用の内部 KPI（ダッシュボード UI は作らない前提） |

### ゼミ前ブリーフ（`routes/seminar_brief.py` admin_router）

輪講の前に教員が対象論文の「賭け金」を10分で把握するための read-only 合成ビュー
（正本: [features/seminar_brief_mirroring_design.md](../features/seminar_brief_mirroring_design.md) §1、
不変条項 SB1〜SB4）。新テーブル・新 LLM ゼロ（SB1）— D層 open-assumptions の document 絞り込み +
SL層 `support_paths` + claim つまづきサマリー（k-匿名集約の再利用のみ, SB4）の読み時合成。
合成の実体は `core/doubt/seminar_brief.py::build_seminar_brief`（FastAPI 非 import）。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/documents/{document_ref}/seminar-brief` | TEACHER + document 閲覧権（`_ensure_document_viewable`・404 fail-closed） | 4区画の合成ビュー: ①脆い前提（未検証×下流影響「高」。`dependent_count` 等の生数値は落とし、load 段階ラベル・challenge_count_label・SL 4キーの段階表示のみ・上位8件）②一点吊りの支持線（`level=single` の事実文・上限5）③晴れ間（「このコーパスの中では検証記録が見つかりません。」の閉世界固定文, SL1・上限8）④学習者からの問い（v1 は空欄予約, SB3）。document / course 対応が解決できないときは `{available: false, reason}` の正直縮退で 200 |

### W層 — 要素検討ワークスペース `/api/admin/deliberation`（`routes/deliberation.py`）

全17本が TEACHER。document-scoped 要素は `_ensure_document_viewable/editable`（404 fail-closed）、
domain-scoped 共通部品（shared_part = L層 `library_entries`）は TEACHER + 由来 document 権限での
route 層フィルタ。詳細は `docs/features/element_deliberation_workspace_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/overview` | TEACHER + document 閲覧権（equation は `document_id` クエリ必須） | 面①内訳 + 面②位置づけ4レンズの集約（非LLM・DB 非変更） |
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/context` | TEACHER + document 閲覧権（equation / evidence / derivation は `document_id` クエリ必須） | 要素中心コンテキストレンズ（面③）のみを軽量に返す（`context_lens.build()` だけを実行。非LLM・DB 非変更）。ゲートは DB 行由来の `document_id` に対して行い、クエリの hint を信用しない |
| POST | `/api/admin/deliberation/identity-links` | TEACHER + インスタンス側 document 編集権 | 同一性リンク候補の作成（常に `candidate`。監査記録） |
| POST | `/api/admin/deliberation/identity-links/{lid}/confirm` | TEACHER + document 編集権（決定済みは 409） | 同一性リンクの人間確定 |
| POST | `/api/admin/deliberation/identity-links/{lid}/reject` | TEACHER + document 編集権 | 同一性リンクの却下（status 遷移で保持） |
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/identity-links` | TEACHER + document 閲覧権 | インスタンス要素の同一性リンク一覧（shared_part 指定は 422） |
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/shared-part-candidates` | TEACHER + document 閲覧権（document-scoped インスタンスのみ） | 手動リンク作成用の類似 `library_entries` 候補（非LLM・DB 非変更）。`domain_key` はサーバ側で決定論解決し、解決不能は 0 件 + 事実文で縮退。距離等の数値は返さない |
| GET | `/api/admin/deliberation/shared-parts/{spid}/identity-links` | TEACHER（閲覧不可 document 由来は除外し `hidden_count` で正直に返す） | 共通部品側の同一性リンク一覧 |
| POST | `/api/admin/deliberation/sessions` | TEACHER + document 閲覧権 | 対話的検討セッションの開始 |
| GET | `/api/admin/deliberation/sessions/{sid}` | 作成者本人のみ（他人は 404） | セッションのメッセージ履歴込み取得 |
| POST | `/api/admin/deliberation/sessions/{sid}/messages` | 作成者本人 + document 閲覧権 + コスト上限（超過 429） | 1ターン送信 → LLM 応答 + 候補注釈（1応答=1 LLM コール。figure は vision 添付） |
| GET | `/api/admin/deliberation/elements/{etype}/{eid}/annotations` | TEACHER + document 閲覧権 | 候補/確定/却下注釈の一覧（confidence は段階ラベルのみ） |
| POST | `/api/admin/deliberation/annotations/{aid}/commit` | TEACHER + document 編集権（未対応 kind は 422） | 候補注釈の確定 → 既存構造（C層 explanation / component / identity / standardization 等）へルーティング |
| POST | `/api/admin/deliberation/annotations/{aid}/dismiss` | TEACHER + document 編集権（candidate 以外 409） | 候補注釈の却下（status 遷移で保持） |
| POST | `/api/admin/deliberation/shared-parts/{spid}/standardization/assess` | TEACHER | 共通部品1件の標準化判定（三角測量）を非同期開始（`force` なしで既評価はスキップ） |
| POST | `/api/admin/deliberation/domains/{dkey}/standardization/assess` | TEACHER | domain 内 active 共通部品すべての標準化判定を非同期開始 |
| GET | `/api/admin/deliberation/documents/{id}/elements` | TEACHER + document 閲覧権 | 要素インベントリ: 教材1件分の全検出要素（component/claim/equation/figure）を統一カード形式で返す |

### Admin Copilot + G層 `/api/admin/assistant`（`routes/admin_assistant.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/assistant/chat` | TEACHER | 統合 AI アシスタントチャット（guidance / locate / action / clarify を自動振り分け。1 LLM コール上限。`support_action="usage_help"` は意図分類 LLM をバイパスして非LLM guidance 直行） |
| GET | `/api/admin/assistant/capabilities` | TEACHER | 現在ロールで到達可能な capability 一覧（サーバ側でフィルタ）。`executable` で「代行できる」と「道案内のみ」を区別。読み取り専用・LLM 非呼び出し |
| GET | `/api/admin/assistant/help/ui-anchors` | TEACHER | 管理画面インスペクト・モードの UI 論理アンカー配信（TEACHER=teacher/ のみ・SYSTEM_ADMIN=+system_admin/ のロール fail-closed。ログイン時1回フェッチ想定） |
| POST | `/api/admin/assistant/help/ui-anchor-events` | TEACHER（本人記録） | 未整備アンカーへのホバー滞留を `kind='help_usage'` 痕跡として記録（逐語は積まない。course_id センチネル `"_ui"` で G層 `manual.help_gaps_pending` に相乗り） |
| POST | `/api/admin/assistant/actions` | TEACHER | 操作代行の実行（capability registry + ロールで fail-closed。不可逆操作は確認ゲート） |
| POST | `/api/admin/assistant/actions/{action_id}/revert` | TEACHER | 代行操作の取り消し（before スナップショットから復元。`reversible=false` は 409） |
| GET | `/api/admin/assistant/actions` | TEACHER | 代行操作の履歴一覧 |
| GET | `/api/admin/assistant/next-steps` | TEACHER | 状態導出型 To-Do（G層。`{steps, hidden, truncated, assistant_cue_pending}`） |
| POST | `/api/admin/assistant/next-steps/{step_key}/dismiss` | TEACHER | 却下を upsert（行削除しない） |
| POST | `/api/admin/assistant/next-steps/{step_key}/restore` | TEACHER | 却下の取り消し（`revoked` 遷移） |

### 利用者マニュアル KB `/api/admin/help-kb`（`routes/admin_assistant.py` help_kb_router）

`admin_router`（/assistant 配下）とは別ルーターとして `main.py` から直接登録される。
全エンドポイント SYSTEM_ADMIN、書き込みは `theory_review_events`（`entity_type='manual'`）に監査記帳。
**削除 API は無い**（版は append-only）。配信の既定はファイル（`docs/manual`）で、DB 配信へ切り替わるのは
`freeze` 成功後のみ。詳細は `docs/features/manual_help_kb_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/help-kb/refresh` | SYSTEM_ADMIN | `docs/manual` / capability KB のキャッシュをクリアし再構築後の状態（audience 別節数・validator 違反数・除外節）を返す。volume-mount 開発・hotfix 用の非常口（定期実行は想定しない） |
| GET | `/api/admin/help-kb/drafts` | SYSTEM_ADMIN | draft 一覧 + 配信状態（`serving_source` 等） |
| GET | `/api/admin/help-kb/drafts/{audience}/{file}` | SYSTEM_ADMIN | 単一 draft の取得（未知 audience/file は 400、不在は 404） |
| PUT | `/api/admin/help-kb/drafts/{audience}/{file}` | SYSTEM_ADMIN | draft 更新（`expected_revision` 楽観ロック。衝突は 409 + `current_revision`） |
| POST | `/api/admin/help-kb/drafts/seed` | SYSTEM_ADMIN | 現配信ファイルのスナップショットから draft を冪等シード（既存 draft は上書きしない） |
| POST | `/api/admin/help-kb/freeze` | SYSTEM_ADMIN | 全 draft を凍結検証ゲート（validator 全チェック + student denylist）に通し、通過時のみ新版発行 + db 配信へ切替。違反時は 422（`violations`）で版・配信状態を変更しない |
| POST | `/api/admin/help-kb/serving-source` | SYSTEM_ADMIN | 配信ソースの明示切替（`db` への切替は freeze 済みの版が必須。無ければ 409）。files への退避経路 |
| GET | `/api/admin/help-kb/versions` | SYSTEM_ADMIN | 版一覧（メタのみ・内容なし） |

### 再構成ループ — 管理（`routes/reconstruction.py` admin_router、R層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/reconstruction/items/review-queue` | TEACHER | 疑わしさランク順の item レビューキュー（document_id / course_id で絞り込み） |
| PATCH | `/api/admin/reconstruction/items/{item_id}` | TEACHER | item の status 遷移（flagged / retired / confirmed）・prompt / expected 修正（削除 API は無い） |
| POST | `/api/admin/reconstruction/documents/{id}/author` | TEACHER | 手動オーサリング（claim → item の LLM 生成バッチ） |
| GET | `/api/admin/documents/{id}/claims/stumble-summary` | TEACHER | claim 単位のつまづきサマリー（k-匿名・レンジ表示） |

### 共有物のバージョン管理 `/api/admin/shared`（`routes/versioning.py`、V層）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/admin/shared/{otype}/{oid}/releases` | TEACHER + 所有者 | 共有版の発行（共有先へ version_published 通知） |
| GET | `/api/admin/shared/{otype}/{oid}/releases` | TEACHER | 版一覧 |
| GET | `/api/admin/shared/releases/{release_id}` | TEACHER | 単一版の取得 |
| GET | `/api/admin/shared/{otype}/{oid}/version-state` | TEACHER | 版状態（更新あり / 削除予定バッジ用） |
| POST | `/api/admin/shared/{otype}/{oid}/deletion` | TEACHER + 所有者 | 削除予約（既定14日猶予。期限後スイーパが物理削除） |
| DELETE | `/api/admin/shared/{otype}/{oid}/deletion` | TEACHER + 所有者 | 削除予約の取り消し |
| POST | `/api/admin/shared/{otype}/{oid}/subscription/adopt` | TEACHER | 版の取り込み（`expected_pinned_release_id` 楽観ロック・不一致 409） |
| GET | `/api/admin/shared/subscription/me` | TEACHER | 本人のピン一覧（更新有無つき） |
| GET | `/api/admin/shared/notifications` | TEACHER | 共有通知インボックス（`unread_only` 可） |
| POST | `/api/admin/shared/notifications/{nid}/read` | TEACHER | 既読化 |
| POST | `/api/admin/shared/notifications/read-all` | TEACHER | 全件既読化 |

`object_type ∈ {course, document}`。エラーは `PurgedError`→410 / `PendingDeletionError`・`AdoptConflictError`→409 / `VersioningError`→422。
学習者向けの削除予定バナーは `GET /api/learning/courses/{cid}/version-notice`（learning 側）。

### 状態・通知（`routes/status.py` / `routes/notifications.py`、migration 038）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/status/overview` | TEACHER | 自分の教材・コースの導出状態一覧（`core/status/projector.py` が正本） |
| GET | `/api/admin/status/materials/{id}` | TEACHER + 所有者 | 教材単体の状態 |
| GET | `/api/admin/status/courses/{id}` | TEACHER + 所有者 | コース単体の状態 |
| GET | `/api/admin/status/events` | TEACHER | status_events の読み取り（自分の所有分に限定。entity_id 指定時は entity_type 必須） |
| GET | `/api/admin/notifications` | TEACHER | 統合インボックス一覧（status / shared 由来を統合。`unread_only` 可） |
| POST | `/api/admin/notifications/{nid}/read` | TEACHER | 既読化（source 不問） |
| POST | `/api/admin/notifications/read-all` | TEACHER | 全件既読化 |
| POST | `/api/admin/notifications/{nid}/dismiss` | TEACHER | 却下（行削除しない。v1 は status 由来のみ対象） |

### ナレッジライブラリ `/api/admin/library`（`routes/library.py`、L層）

手順の正本: [admin_operations/library.md](../admin_operations/library.md)。削除 API は無く `retire` 遷移のみ。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/library/entries` | TEACHER | エントリ一覧（domain_key / entry_type / q / include_retired で絞り込み） |
| POST | `/api/admin/library/entries` | TEACHER | draft エントリ新規作成（昇格 / 手動作成の共通経路。例示画像は元 document 所有者のみ） |
| GET | `/api/admin/library/entries/{eid}` | TEACHER | 単一エントリ取得 |
| GET | `/api/admin/library/entries/{eid}/versions` | TEACHER | 凍結版履歴 |
| PUT | `/api/admin/library/entries/{eid}` | TEACHER | draft 更新（`revision` 楽観ロック・衝突 409。retired は 409） |
| POST | `/api/admin/library/entries/{eid}/freeze` | TEACHER | 凍結版の発行（パイプラインが読むのは凍結版のみ） |
| POST | `/api/admin/library/entries/{eid}/retire` | TEACHER | retire 遷移（retrieval から除外） |
| POST | `/api/admin/library/entries/{eid}/restore` | TEACHER | retire からの復帰 |
| GET | `/api/admin/library/domains` | TEACHER | domain 別サマリ |
| POST | `/api/admin/library/entries/similar` | TEACHER | 類似エントリ検索（昇格モーダルの統合候補提示用） |

### LLM 使用量 `/api/admin/llm-usage`（`routes/llm_usage.py`、U層）

手順の正本: [admin_operations/llm_usage.md](../admin_operations/llm_usage.md)。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/llm-usage/metrics` | SYSTEM_ADMIN | 使用量集計（reported / estimated 分離 + dropped_events + cost_usd） |
| GET | `/api/admin/llm-usage/estimate/documents/{id}` | TEACHER + document 閲覧権 | 解析実行前のトークン見積り（レンジのみ・金額なし） |
| GET | `/api/admin/llm-usage/forecast` | TEACHER | コスト見通しの一行（document 不要版・アップロードゾーン用。末端4ステージの日次カウンタ残数のみによる保守判定。返却は `{show, message}` のみ・数値なし・fail-open。教員支援 Phase 4 §3.1） |
| GET | `/api/admin/llm-usage/forecast/documents/{id}` | TEACHER + document 閲覧権 | コスト見通しの一行（document 版・再解析モーダル用。見積り上振れ × 日次カウンタ残数の合成による保守判定。返却は `{show, message}` のみ） |

### 場面別 LLM モデル選択 `/api/admin/llm-models`（`routes/llm_models.py`、M層 / migration 061）

モデル決定の正本は `core/llm_policy.py`。解決順序は 呼び出し引数 > 実行時 override > user 行 >
system 行 > `*_LLM_MODEL` env > tier 既定で、**選択はユーザーごとに保存される**。検証は
サーバ側 fail-closed（カタログ外・capability 不足・未知 scene は 422）。表示は実モデル名のみで
tier 名・金額は出さない。監査 `entity_type='llm_model_policy'`。詳細は
`docs/features/llm_model_selection_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| GET | `/api/admin/llm-models/catalog` | TEACHER | 選択肢 + 本人にとっての実効モデル（`scene` 未指定なら全 scene）。`catalog_available` でカタログ不在も正直に返す |
| GET | `/api/admin/llm-models/pipeline-stages` | TEACHER | 解析パイプラインの LLM ステージ一覧（`PIPELINE_STAGES` の順序 × `LLM_STAGE_NAMES` の交差）。各行に `feature`（`pipeline:<stage>`）/ `label` / `vision` / `effective` |
| GET | `/api/admin/llm-models/policies` | SYSTEM_ADMIN | システム既定一覧（各行に `is_feature_level`＝ステージ別上書き行かどうか、と `label`） |
| PUT | `/api/admin/llm-models/policies/{scene_key}` | SYSTEM_ADMIN | システム既定の設定・変更 |
| DELETE | `/api/admin/llm-models/policies/{scene_key}` | SYSTEM_ADMIN | システム既定の解除（行なしは 404） |
| PUT | `/api/admin/llm-models/my-policies/{scene_key}` | TEACHER（user_id は認証ユーザー固定） | ユーザー別既定の設定・変更 |
| DELETE | `/api/admin/llm-models/my-policies/{scene_key}` | TEACHER（本人の行のみ） | ユーザー別既定の解除（行なしは 404） |

### discuss 観測基盤（`routes/discuss_observation.py`、migration 060）

discuss モードの Phase 3 着手判断を実測ゲートで行うための観測層。本文非含有・仮名化で、
学習者に数値を見せる API は作らない。削除 API は無い（append-only）。詳細は
`docs/features/discuss_observation_design.md`。

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/learning/discuss/metric-events` | 要ログイン（本人記録） | discuss UI 操作イベントの取込（1リクエスト最大20件、語彙はサーバ側ホワイトリスト検証で未知は 422）。フロントは fire-and-forget 前提で、レスポンスは件数のみ |
| GET | `/api/admin/discuss/observation-status` | SYSTEM_ADMIN | 蓄積状況 + 分析開始の参考目安（`criteria` / `ready_for_analysis` は表示専用。自動ゲートにしない） |
| GET | `/api/admin/discuss/observation-dump` | SYSTEM_ADMIN | 分析用ダンプ（`format` は `tar.gz` / `zip`、それ以外は 422）。取得を `entity_type='discuss_observation'` で監査 |

### エクスポート（`routes/export.py`）

| メソッド | パス | 権限 | 説明 |
|---|---|---|---|
| POST | `/api/courses/{course_id}/export-bundle` | TEACHER | コース一式のエクスポート ZIP 生成・ダウンロード |
| POST | `/api/documents/{document_id}/export-bundle` | TEACHER | ドキュメント一式のエクスポート ZIP 生成・ダウンロード |

---

## 4. 非同期処理パターン

重い処理（教材アップロード解析・スクリプト/音声バッチ生成・再抽出）は次の形を取ります。

```
クライアント         api-server                  background_tasks
   │  POST .../upload  │                              │
   │ ───────────────▶ │ 202 { task_id } を返す       │ INSERT (pending)
   │ ◀─────────────── │                              │
   │                  │  別スレッドで処理 ──────────▶ │ processing → completed/failed
   │  GET /tasks/{id} │                              │
   │ ───────────────▶ │  状態を返す                  │
```

`services.py` の `create_background_task` / `update_background_task` / `get_background_task` が状態を管理し、
`document_analysis_runs` の進捗も合わせて返します。

---

[← データモデル](../architecture/data-model.md) ｜ 次へ: [コアエンジン →](core-engine.md)
