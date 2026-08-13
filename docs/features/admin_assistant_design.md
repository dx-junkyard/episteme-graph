# 管理画面 統合AIアシスタント（Admin Copilot）設計

> ステータス: **実装済み**（migration 034、`backend/core/admin_assistant/` + `routes/admin_assistant.py`。本書は設計正本として凍結。※旧表記「Draft（設計提案）」は 2026-08-13 の総点検で更新）
> 対象ブランチ想定: `learning-ux` の後続 / `feature/admin-assistant`
> 関連層: 本機能は **横断ユーティリティ層**。A層（`src/episteme_graph/agents/`）・B層（学習）・C層（承認）・D層（Doubt）のコードは**変更しない**（既存 API を呼ぶ側として実装する）。

---

## 0. 背景と課題

管理画面（`frontend/public/admin.html` / `frontend/public/js/admin.js`、約 10,900 行の Vanilla JS/ES5）には、現在 **画面ごとに独立したAI機能**が点在している。

| 既存AI機能 | 画面 | 実体 | 種別 |
|---|---|---|---|
| コース構築チャット | `course-builder` タブ | `POST /api/admin/course-builder/chat`（[`admin.py:1451`](../../backend/api/routes/admin.py)）+ `course_builder_sessions` 永続化 | 多ターンチャット・提案のみ（DB非変更） |
| 原稿スタジオ アシスタント | `lecture-studio` タブ（`#ls-assistant-modal`） | `POST /api/admin/chunks/{chunk_id}/lecture-script/rewrite` / `.../course-topics/{topic_id}/draft/rewrite`（[`routes/lecture_studio/`](../../backend/api/routes/lecture_studio/) パッケージ） | 表示中要素の**修正代行**（`lsRewriteScript()` [`admin.js:9226`](../../frontend/public/js/admin.js)） |
| コンポーネント候補生成 | 質問由来 | `POST /api/admin/theory-components/candidates/from-query`（[`theory_components.py:3340`](../../backend/api/routes/theory_components.py)） | 候補をDBに作成（`status=candidate`） |

**課題:**
1. **操作方法のヘルプが存在しない。** 「どのタブで何ができるか」「この画面でどう操作するか」を尋ねられる相手がいない。ドキュメントは `docs/` にあるが管理画面から参照できない。
2. **修正代行アシスタントが原稿スタジオに閉じている。** モーダル（`#ls-assistant-modal`）は選択中チャンク／トピックにしか効かず、他画面から呼べない。
3. **やり直しができない。** `resetToNewSession()`（[`admin.js:1069`](../../frontend/public/js/admin.js)）や `studentForm.reset()` 程度で、汎用の Undo/Revert 機構が無い（フロント調査で確認）。原稿スタジオの提案反映は `lsState` へのローカル適用のみ・保存で確定という段階なので、思った通りでなかったときに**確定前も確定後も戻せる導線**が要る。

**本設計のゴール:** これらを **画面横断の統合AIアシスタント（Admin Copilot）** に統合する。すなわち
(a) ユーザーの**管理権限範囲に限定した操作説明**、(b) 現在表示中の要素に対する**チャット形式の修正代行**、(c) **戻す（Undo/Revert）ボタン**、(d) 「どこでやるか分からない」に対する**画面遷移＋入力箇所の点灯（道案内）** の4点を、全管理画面で常設のパネルから提供する。

---

## 1. ゴール / 非ゴール

### ゴール
- **G1**: 全管理タブで常設起動できる単一のアシスタントパネル（フローティング）。
- **G2**: 「説明モード」— ログインユーザーの**ロール（STUDENT / TEACHER / SYSTEM_ADMIN）で実行可能な操作のみ**を対象に、手順を自然言語で説明する。権限外の操作は「あなたの権限では実行できません」と正直に返す。
- **G3**: 「操作代行モード」— 現在画面に表示中の要素（選択中チャンク・トピック・コース draft・可視性設定など）を、チャット指示で修正する。既存の原稿スタジオ rewrite を**この経路に吸収**する。
- **G4**: 戻す（Undo/Revert）— アシスタントが行った各修正を、確定前（ローカル）・確定後（永続）いずれも1操作単位で取り消せる。
- **G5**: 「道案内モード」— ユーザーが「〜をどこでやればいいか分からない」と伝えたら、アシスタントが**該当タブへ画面遷移し、入力すべき箇所を点灯（スポットライト）**して案内する。案内は本人のロールで実行可能な操作に限る。
- **G6**: 全ての操作代行を監査する（既存 `theory_review_events` を再利用）。

### 非ゴール
- 学習画面（`app.js`）向けアシスタントの統合（本設計は管理画面限定。学習チャットは別系統で維持）。
- 汎用的な「何でも実行するエージェント」化（後述の **capability registry に登録された操作に限定**する）。
- リアルタイムのコード生成／スキーマ自動変更（説明と、登録済み操作の代行のみ）。
- 操作説明のためのリアルタイム画面スクレイピング（DOM スクレイプではなく、各画面が渡す**構造化コンテキスト**を使う。§8）。

---

## 2. 設計原則（不変条項）

D層 / TensionMining の様式に倣い、実装が構造的に守るべき不変条項を先に固定する。

- **P1 権限を越えない（fail-closed）**: 説明も代行も **capability registry**（§4）に登録され、かつ現在ユーザーのロールで許可された操作のみ対象。判定はサーバ側で行い、フロントの表示制御を信頼しない。登録に無い/権限外は「できない」と返す（推測で API を叩かない）。
- **P2 破壊的操作は必ず確認**: `reversible=false`（物理削除・学生への公開・外部共有・アカウント作成等）は、代行前に**明示確認ゲート**を挟む。無確認で実行しない。
- **P3 情報を落とさない**: 修正代行は原則 before スナップショットを保持してから適用する。取り消しは行削除でなく状態遷移／スナップショット復元で行う。
- **P4 断定・捏造しない**: 説明モードは**登録済みの操作ドキュメント**に基づいて答え、根拠（対象タブ・API・必要ロール）を併記する。KB に無いことは「未整備」と言い、手順をでっち上げない。
- **P5 監査必須・帰属あり**: 代行・取り消しは全て `theory_review_events` に `entity_type='assistant_action'` で記録（誰が・いつ・何を・戻したか）。
- **P6 同期パスを重くしない**: チャット応答は 1 LLM コール（intent 分類 + 応答/操作計画）を上限とし、重い処理（コーパス横断の説明索引再構築等）は非同期。
- **P7 既存 A/B/C/D 層のコードを変更しない**: Copilot は既存 API/関数を呼ぶ側として実装する。既存エンドポイントの契約は変えず、必要なら**薄いラッパ**を追加する。
- **P8 道案内は誘導まで（自動入力・自動送信しない）**: 画面遷移と入力箇所の点灯（スポットライト）は行うが、値の入力・送信・保存は**本人が行う**。道案内は「操作代行」ではないため確認ゲート不要だが、fail-closed（P1）は同じく適用し、**本人の権限で実行できない操作の場所は案内しない**（点灯先が権限外なら「あなたの権限では実行できません」と返す）。

---

## 3. 全体アーキテクチャ

```
┌───────────────────────────── 管理画面 (admin.html / admin.js, ES5) ─────────────────────────────┐
│                                                                                                  │
│  各タブ (materials / course-builder / lecture-studio / atlas / groups / ...) │
│     │  registerScreenContext(tab, () => structuredState)   ← 画面が自分の状態を提供             │
│     │  registerUiAnchors(tab, {anchorId: () => domElement}) ← 画面が点灯先を提供（道案内）      │
│     ▼                                                                                            │
│  ┌───────────────── admin-assistant.js（新規・常設フローティングパネル）───────────────┐        │
│  │  ・トグルボタン（topbar-r）／パネル（position:fixed, 全タブ横断）                      │        │
│  │  ・チャット UI（course-builder のチャットUIを共通化して流用）                        │        │
│  │  ・collectScreenContext(): {tab, selection, visibleEntities}                        │        │
│  │  ・ActionStack（戻すボタン。L1 ローカル / L2 サーバ revert を一元管理）               │        │
│  │  ・runLocatePlan(): activateTabView → scrollIntoView → スポットライト点灯（道案内）  │        │
│  └──────────────────────────────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────┬───────────────────────────────────────────────┘
                                                │  POST /api/admin/assistant/chat
                                                ▼
┌──────────────────────── backend/api/routes/admin_assistant.py（新規, /api/admin/assistant/*）──────────┐
│  1) 認証・ロール取得 (_get_current_user / _require_teacher)                                             │
│  2) intent 分類 + 応答生成（1 LLM コール, structured output）                                           │
│         ├─ guidance → 操作KB を role×screen でフィルタし RAG 説明（DB非変更）                          │
│         └─ action   → capability registry から tool を解決 → ActionPlan を返す（未実行 or 実行）        │
│  3) 実行時: AssistantActionRunner が before スナップショット取得 → 既存 API/関数呼び出し → after 記録   │
│  4) 監査: _record_review_event(entity_type='assistant_action')                                         │
└──────────────────────────────────────────────┬───────────────────────────────────────────────────────┘
                                                ▼
   backend/core/admin_assistant/                         再利用（変更しない）
     ├─ capabilities.py   … capability registry（唯一の真実源）    既存 API:
     ├─ knowledge.py      … 操作KB ローダ + role/screen フィルタ     ・course-builder/chat
     ├─ intent.py         … LLM intent 分類 & 応答（llm_client）      ・lecture-script/rewrite, /draft/rewrite
     ├─ actions/          … tool 実装（reversible 宣言 + apply/revert） ・courses/{id}/visibility, /publish
     ├─ action_store.py   … assistant_actions テーブル I/O           ・theory-components/candidates/from-query
     └─ schema.py         … dataclass / Pydantic
                                                ▼
   PostgreSQL: assistant_actions (migration 034) + theory_review_events（既存・entity_type 拡張）
   操作KB: docs/admin_operations/*.md（role タグ付き）→ 起動時にインデックス化
```

Copilot は **3つのモード**を単一チャットで扱う。ユーザーがモードを選ぶ必要はなく、`intent.py` の分類で振り分ける（`guidance`＝説明 / `locate`＝道案内 / `action`＝代行 / `clarify`＝聞き返し）。`guidance` と `locate` は読み取り専用（DB非変更）、`action` のみが変更を伴う。

---

## 4. 権限スコープ設計 — Capability Registry（本設計の心臓部）

現状、権限判定は**各エンドポイントに散在**している（`_require_teacher` / `_require_system_admin` / `user_can_edit_course()` 等。RBAC 調査で確認）。Copilot が「その人の権限でできることだけ」を説明・代行するには、**操作の宣言的カタログ**が要る。

`backend/core/admin_assistant/capabilities.py` に、画面横断で**単一の真実源**として定義する。

```python
@dataclass(frozen=True)
class Capability:
    id: str                      # 例 "lecture_studio.rewrite_chunk_script"
    screen: str                  # タブ名 "lecture-studio"（admin.js の data-tab に一致）
    title: str                   # "チャンクの原稿をAIで書き換える"
    required_role: str           # "TEACHER" | "SYSTEM_ADMIN"
    scope: str = "any"           # "own_course" 等の追加条件（既存 helper に委譲）
    kind: str                    # "guidance_only" | "action"
    reversible: bool             # action のとき: 取り消し可能か（§6）
    api: dict | None             # action のとき: {method, path_template, body_builder}
    revert: dict | None          # reversible=True のとき: 取り消し方法
    howto_doc: str               # 操作KB のアンカー（docs/admin_operations/... の見出し）
    confirm: bool = False        # reversible=False は必ず True（P2）
    locate_steps: list = ()      # 道案内（§5.3）: 順序付きステップ。各要素 =
                                 #   {screen, anchor_id, hint, precondition?}
                                 #   anchor_id は各画面が registerUiAnchors で解決する論理ID
                                 #   （バックエンドは DOM セレクタを持たない＝ domain/DOM 非依存）
```

### 4.1 registry と実エンドポイントの対応（初期登録例）

| capability id | screen | required_role | kind | reversible | 実 API |
|---|---|---|---|---|---|
| `materials.upload` | materials | TEACHER | guidance_only | – | `POST /admin/materials/upload` |
| `materials.set_visibility` | materials | TEACHER (own) | action | ✅ | `PUT /admin/materials/{id}/visibility` |
| `materials.delete` | materials | TEACHER (own) | action | ❌ confirm | `DELETE /admin/materials/{id}` |
| `course_builder.chat` | course-builder | TEACHER | action | ✅(session) | `POST /admin/course-builder/chat` |
| `lecture_studio.rewrite_chunk_script` | lecture-studio | TEACHER (own) | action | ✅(L1) | `POST /admin/chunks/{id}/lecture-script/rewrite` |
| `lecture_studio.rewrite_topic_draft` | lecture-studio | TEACHER (own) | action | ✅(L1) | `POST /admin/courses/{cid}/lecture-studio/course-topics/{tid}/draft/rewrite` |
| `course.set_visibility` | course-management | TEACHER (own) | action | ✅ | `PUT /admin/courses/{id}/visibility` |
| `course.publish` | course-management | TEACHER (own) | action | ❌ confirm | `PUT /admin/courses/{id}/visibility`（body `{"visibility": "public"}`。旧 `/publish` は撤去済み） |
| `course.delete` | course-management | TEACHER (own) | action | ❌ confirm | `DELETE /admin/courses/{id}` |
| `atlas.generate_skeleton` | atlas | TEACHER | action | ✅ | `POST /cartridges/{id}/atlas/skeleton/generate` |
| `atlas.freeze_skeleton` | atlas | TEACHER | action | ❌ confirm | `POST /cartridges/{id}/atlas/skeleton/freeze` |
| `schema.analyze_proposals` | schema-proposals | TEACHER | action | ✅ | `POST /admin/schema-proposals/analyze` |
| `users.create_student` | groups | TEACHER | action | ❌ confirm | `POST /admin/users/student` |
| `users.create_teacher` | groups | **SYSTEM_ADMIN** | action | ❌ confirm | `POST /admin/users/teacher` |
| `system.view_stats` | system-stats | **SYSTEM_ADMIN** | guidance_only | – | `GET /admin/system/materials-stats` |
| `system.view_error_logs` | error-analysis | **SYSTEM_ADMIN** | guidance_only | – | `GET /admin/error-logs` |

（初期は上記のような「よく使う・安全な」操作から段階登録。全 API を一度に載せない＝P1 の fail-closed。）

各 capability は `locate_steps`（§5.3 道案内の点灯手順）も併せ持つ。例:
- `materials.upload` → `[{screen:"materials", anchor_id:"upload_dropzone", hint:"ここにPDF/TeXをドロップ"}]`
- `course.publish` → `[{screen:"course-management", anchor_id:"course_row:{course_id}", hint:"公開したいコースを選択"}, {screen:"course-management", anchor_id:"publish_button", hint:"『公開する』を押す"}]`
- `lecture_studio.rewrite_chunk_script` → `[{screen:"lecture-studio", anchor_id:"chunk_list", hint:"対象チャンクを選択", precondition:"chunk_selected"}, {screen:"lecture-studio", anchor_id:"assistant_open_button", hint:"AIアシスタントを開く"}, {screen:"lecture-studio", anchor_id:"ls-rewrite-prompt", hint:"ここに指示を入力"}]`

`anchor_id` は**論理ID**で、実 DOM セレクタは各画面の `registerUiAnchors`（§9.5）が解決する。`{course_id}` のような差し込みは実行時コンテキストで置換する。

### 4.2 権限フィルタ

- サーバは `_get_current_user()` からロールを取り、`capabilities_for(role)` で**そのロールが到達可能な capability 集合**を得る。
- `guidance`: KB 検索対象を `capabilities_for(role)` の `howto_doc` に**限定**。TEACHER に「教員アカウント作成」の手順は返さない。
- `action`: 解決した capability が `required_role` を満たさなければ実行拒否（`403` を Copilot が「あなたの権限では実行できません」に翻訳して返す）。`scope="own_course"` は既存の `user_can_edit_course()` / `user_owns_course()` に委譲（Copilot が独自判定しない＝二重実装を避ける）。

---

## 5. 説明・道案内モード（Guidance & Locate）

**目的:** 「この画面で何ができる？」「コースを学生に公開するには？」に手順で答える（§5.1–5.2）。さらに「どこでやればいいか分からない」に対しては**画面遷移＋入力箇所の点灯**で案内する（§5.3）。いずれも読み取り専用で DB を変更しない。

### 5.1 操作ナレッジベース（KB）
- 実体: `docs/admin_operations/*.md`（新規）。既存の `docs/features/*.md`（設計文書）とは別に、**操作手順**に特化した短い節を書く。各節は front-matter で `capability: <id>` `role: TEACHER|SYSTEM_ADMIN` `screen: <tab>` を持つ。
- インデックス: 起動時に `knowledge.py` が読み込み、節単位のチャンクを（既存の embedder を使うなら）pgvector に、あるいは軽量にキーワード+capability タグで検索できるようにする。**リアルタイム生成はしない**（P4/P6）。KB に無ければ「未整備」と返す。
- role フィルタ: 検索前に `capabilities_for(role)` の `howto_doc` 集合で絞る。

### 5.2 応答生成
- `intent.py` が `intent="guidance"` と判定 → 該当節を上位 N 件取得 → 「手順 + 対象タブへのジャンプ導線 + 必要ロール」を返す。
- 応答の `next_actions` に `{type:"navigate", screen:"course-management"}` を含め、フロントは `activateTabView(screen)`（[`admin.js:204`](../../frontend/public/js/admin.js)）で該当タブへ誘導できる。
- 権限外を尋ねられたら、KB に節があっても**手順を出さず**「この操作は SYSTEM_ADMIN のみです」と返す（P1）。

### 5.3 道案内（Locate & Spotlight）

**目的:** 「教材ってどこからアップロードするの？」「このコースを公開する場所が分からない」のような**「どこで（where）」を問う発話**に対し、説明文だけでなく**実際にその画面へ連れて行き、入力すべき箇所を点灯**する。ユーザー要望の中核。

**判定と解決フロー:**
1. `intent.py` が `intent="locate"` と分類（「どこ」「どうやって行く」「見つからない」等の where 型発話）。同時に、発話が指す capability を registry から解決（説明モードと同じ KB/registry を使う）。
2. 解決した capability が**本人のロールで実行可能かを判定**（P1/P8）。不可なら点灯せず「あなたの権限では実行できません」と返す。
3. 可能なら capability の `locate_steps` を、実行時コンテキスト（`screen_context.selection` 等）で差し込み解決し、`locate_plan` として返す。
4. フロントの `runLocatePlan()`（§9.4）がステップを順に実行: `activateTabView(step.screen)` → `anchor_id` を `registerUiAnchors`（§9.5）で DOM 解決 → `scrollIntoView({block:"center"})` → **スポットライト点灯**（既存の `.mg-highlight`（[`styles.css:411`](../../frontend/public/css/styles.css)）/ `atlas-pulse`（[`atlas-overlay.js:508`](../../frontend/public/js/atlas-overlay.js)）と同系の一時ハイライト class `.admin-assistant-spotlight`）＋ `step.hint` を吹き出しで表示。

**多段案内（precondition）:** ある入力箇所が「先に別の選択をしないと表示されない」場合（例: 原稿 rewrite は**チャンク選択後**にしか入力欄が出ない）、`locate_steps` を順序付きで持ち、各ステップに `precondition`（例: `chunk_selected`）を付ける。`runLocatePlan()` は前提が未達なら**そのステップで一旦停止**し「まずここを選んでください」と点灯して、選択完了イベント（画面が発火）を待って次ステップへ進む。アンカーが現時点で解決できない（DOM に無い）場合は、そこまで点灯して「この先はこの画面の操作後に案内します」と正直に伝える（点灯先を捏造しない、P4）。

**設計上の一線（P8）:** 道案内は**遷移と点灯まで**。値の入力・ボタン押下・保存は本人が行う。これにより「案内」と「代行」を明確に分離し、道案内経由で意図せぬ変更が起きないことを保証する（確認ゲート不要）。「ここまで案内したので、あとは入力して『実行』を押してください。私に代わりにやらせることもできます」と、必要なら操作代行モード（§6）へ橋渡しする。

---

## 6. 操作代行モード（Action）+ 戻す機構

### 6.1 Action 抽象
各 `action` capability は `backend/core/admin_assistant/actions/` に **tool** として実装し、以下を宣言する。

```python
class AssistantAction(Protocol):
    capability_id: str
    reversible: bool
    def capture_before(self, ctx) -> dict: ...   # 変更前スナップショット（対象行の JSON 等）
    def apply(self, ctx, args) -> dict: ...       # 既存 API/関数を呼ぶ。after を返す
    def revert(self, before: dict) -> None: ...   # reversible=True のときのみ
```

`apply` は**既存エンドポイント/関数を呼ぶだけ**（P7）。例: `lecture_studio.rewrite_chunk_script` は内部で `rewrite_lecture_script()` の結果を返し、確定保存は既存 `PUT /chunks/{id}/lecture-script`（[`routes/lecture_studio/`](../../backend/api/routes/lecture_studio/) パッケージ）に委ねる。

### 6.2 リスク階層と確認ゲート（P2）
| リスク | 例 | 挙動 |
|---|---|---|
| 局所・可逆（L1） | 原稿 rewrite（保存前のエディタ反映） | 即適用。戻す＝ローカル復元 |
| 永続・可逆（L2） | 可視性変更・topic draft 保存・候補生成 | 即適用。戻す＝サーバ revert |
| 不可逆（要確認） | 削除・公開・骨格 freeze・アカウント作成 | **実行前に確認カード**を出し、承認まで実行しない |

「代行してくれる」体験（ユーザー要望）を満たすため、可逆操作は**確認なしで適用し、戻すで担保**する。不可逆のみ確認を必須にする。

### 6.3 戻す（Undo/Revert）— 2層

現状、原稿スタジオの提案反映は `lsState` へのローカル適用（未保存）で、保存で確定という2段構造（[`admin.js:9271-9287`](../../frontend/public/js/admin.js)）。よって戻すも2層で設計する。

**L1: クライアント側 Undo（未保存の局所編集）**
- `admin-assistant.js` の `ActionStack` が、apply 前に対象画面のエディタ状態スナップショット（`lsState.chunks[i]` の該当フィールド、フォーム値など）を取得。
- 戻す押下 → スナップショットを書き戻し + 該当画面の再レンダ関数（`lsRenderSelectedCourseTopic` / `lsRenderChunkList` 等）を呼ぶ。DB に触れない。
- 画面がスナップショット/復元をどう行うかは、各画面が **`registerUndoHandler(tab, {snapshot, restore})`** を登録する（Copilot が画面内部を知らずに済む）。

**L2: サーバ側 Revert（永続化された変更）**
- `apply` 実行時に `assistant_actions` へ `before_snapshot` / `after_snapshot` / `reversible` / `revert_spec` を記録。
- 戻す押下 → `POST /api/admin/assistant/actions/{action_id}/revert` → `revert(before)` 実行（例: 可視性を before に戻す、候補 explanation を `status='dismissed'` にする）。
- `reversible=false` の action は `assistant_actions.reversible=FALSE` で記録し、戻す UI では**無効化 + 理由表示**（「この操作は取り消せません」）。
- revert 自体も監査（`theory_review_events`）し、`assistant_actions.reverted_at` を立てる（P3: 行は消さない）。

**ActionStack の一元化**: L1/L2 を UI 上は「直前の操作を戻す」1ボタンに統合。スタック要素は `{kind:"local"|"server", label, undo()}`。複数戻し（履歴）は Phase 2。

---

## 7. データモデル（migration 034）

`backend/db/034_assistant_actions.sql`（現状の最新は `033_counterfactual_sessions.sql`）。

```sql
CREATE TABLE IF NOT EXISTS assistant_actions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    session_id      TEXT,                       -- Copilot 会話セッション（任意）
    capability_id   TEXT NOT NULL,              -- capabilities.py の id
    screen          TEXT NOT NULL,              -- 実行時のタブ
    target_type     TEXT NOT NULL,              -- 'chunk'|'course'|'material'|'topic'|...
    target_id       TEXT,
    args            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 指示・パラメータ
    before_snapshot JSONB,                      -- 変更前（P3）
    after_snapshot  JSONB,
    reversible      BOOLEAN NOT NULL DEFAULT TRUE,
    revert_spec     JSONB,                      -- revert に必要な情報
    status          TEXT NOT NULL DEFAULT 'applied',  -- applied|reverted|failed|confirm_pending
    reverted_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_user   ON assistant_actions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_assistant_actions_target ON assistant_actions(target_type, target_id);
```

会話セッションは既存 `course_builder_sessions` の様式を流用してもよいが、Copilot は横断的なので専用の軽量セッション（`assistant_sessions`、任意）か、既存テーブル無改変ならセッションはフロント保持 + `session_id` を actions に持たせるだけでも成立する。**MVP は `assistant_sessions` を作らず、履歴永続は Phase 2**。

監査は新テーブルを増やさず既存 `theory_review_events` を再利用（D層と同方針）:
`entity_type='assistant_action'`、`entity_id=assistant_actions.id`、`action IN ('apply','revert','confirm')`。

---

## 8. API 仕様（`/api/admin/assistant/*`）

全て `Depends(_require_teacher)`（実行時に capability の `required_role` で二次判定）。ルータは `backend/api/routes/admin_assistant.py`（新規）、`main.py` に `include_router`。

### 8.1 `POST /api/admin/assistant/chat`
```jsonc
// Request
{
  "message": "このコースを学生に公開したい",
  "history": [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}],
  "session_id": "opt-uuid",
  "screen_context": {
    "tab": "course-management",
    "selection": { "course_id": "c_123" },
    "visible_entities": [ {"type":"course","id":"c_123","title":"量子力学入門"} ]
  }
}
// Response
{
  "answer": "『量子力学入門』を公開します。これは公開後すぐ学生の受講対象になります。実行しますか？",
  "intent": "action",                       // guidance | locate | action | clarify
  "action_plan": {                          // intent=action のときのみ
    "capability_id": "course.publish",
    "target": {"type":"course","id":"c_123"},
    "args": {},
    "reversible": false,
    "confirm_required": true                 // P2: 実行前確認
  },
  "locate_plan": {                          // intent=locate のときのみ（§5.3）
    "capability_id": "course.publish",
    "steps": [
      {"screen":"course-management","anchor_id":"course_row:c_123","hint":"公開したいコースを選択"},
      {"screen":"course-management","anchor_id":"publish_button","hint":"『公開する』を押す","precondition":"course_selected"}
    ]
  },
  "next_actions": [ {"type":"navigate","screen":"course-management"} ],
  "citations": [ {"doc":"admin_operations/course.md#publish"} ]  // guidance のとき
}
```
- `screen_context` はフロントの `collectScreenContext()` が生成（DOM スクレイプではなく構造化状態）。
- `confirm_required=true` のときフロントは確認カードを出し、承認後に §8.2 を叩く。可逆・局所は `action_plan.reversible=true` かつ `confirm_required=false` として、フロントが即 §8.2 実行 or L1 ローカル適用。
- `locate_plan` はフロントの `runLocatePlan()`（§9.4）が実行する。**サーバ API を追加で叩かず DB も変更しない**（遷移と点灯のみ）ため確認ゲート不要。監査対象にもしない（読み取り操作、P8）。

### 8.2 `POST /api/admin/assistant/actions`（実行）
```jsonc
// Request
{ "capability_id":"course.publish", "target":{"type":"course","id":"c_123"}, "args":{}, "session_id":"..." }
// Response
{ "action_id":"a_789", "status":"applied", "after":{...}, "reversible":false,
  "message":"公開しました。" }
```
- サーバ: capability 解決 → role/scope 判定 → `capture_before` → `apply`（既存 API 呼出）→ `assistant_actions` 記録 → 監査。
- 失敗時は `status='failed'` を記録し、変更は適用しない（トランザクション）。

### 8.3 `POST /api/admin/assistant/actions/{action_id}/revert`
```jsonc
{ "status":"reverted", "restored":{...}, "message":"公開を取り消しました。" }
```
- `reversible=false` は `409 not_reversible` を返し、フロントは戻すを無効化。
- 本人以外・別ユーザーの action は 403（`user_id` 照合）。

### 8.4 `GET /api/admin/assistant/actions?limit=20`（戻す履歴）
- 直近の自分の action を返す（戻すパネルの L2 スタック復元用）。

### 8.5 スキーマ配置（開発ルール準拠）
- リクエスト/レスポンス Pydantic は `backend/api/schemas.py`（`Assistant*` プレフィックス）。
- ドメインモデル（Capability / ActionPlan 等）は `backend/core/admin_assistant/schema.py`（`core/` に FastAPI import を持ち込まない＝開発ルール2）。

---

## 9. フロントエンド設計（`admin.js` は ES5 準拠）

### 9.1 マウント
- 新規 `frontend/public/js/admin-assistant.js`（ES5 / IIFE、`window.AdminAssistant` を公開）。`admin.html` の末尾で読み込み、`initApp()`（[`admin.js:~10357`](../../frontend/public/js/admin.js)）から `AdminAssistant.init({apiFetch, state, activateTabView})` を呼ぶ（依存を注入して疎結合に）。
- **トグル**: `topbar-r`（[`admin.html:20`](../../frontend/public/admin.html)、`#admin-username` の隣）に「🤖 操作アシスタント」ボタン。
- **パネル**: `position: fixed; bottom/right; z-index > topbar(10)`。全タブ横断で状態保持。CSS は `styles.css` に `.admin-assistant-*` プレフィックス（Field Atlas / Doubt Atlas と衝突しない命名）。
- **チャットUI流用**: `renderCourseChat()`（[`admin.js:1245`](../../frontend/public/js/admin.js)）の `.mg.usr` / `.mg.ai` / `.typing` マークアップと `renderSimpleMarkdown` を共通ヘルパに切り出して再利用。

### 9.2 画面コンテキストの収集
DOM スクレイプを避け、各画面が自分の構造化状態を提供する登録フックを設ける（既存 `onTabActivate` の隣に併設）:

```js
// admin-assistant.js が公開
AdminAssistant.registerScreenContext("lecture-studio", function () {
  return {
    selection: { chunk_id: lsState.selectedChunkId, scope: lsState.selectedScope },
    visible_entities: lsState.chunks.slice(0, 20).map(function (c) {
      return { type: "chunk", id: c.chunk_id, title: c.display_text.slice(0, 40) };
    })
  };
});
```
- `collectScreenContext()` は「現在アクティブなタブ（`document.querySelector('.admin-tab.on').dataset.tab`）」+ 登録関数の戻り値を合成して送る。
- 未登録タブは `{tab}` のみ（説明モードは動く／代行は「この画面では対象要素が取得できません」）。

### 9.3 既存原稿スタジオ アシスタントの吸収
- `#ls-assistant-modal` と `lsRewriteScript()` の**バックエンド呼び出しは維持**しつつ、UI 導線を Copilot パネルに寄せる。移行方針は2択（§13 で選択）:
  - **(a) ラップ**: モーダルは残し、Copilot から「原稿を書き換える」指示が来たら内部で `lsRewriteScript()` 相当を呼ぶ（原稿スタジオを開いていない場合は該当タブへ誘導）。改修が最小。
  - **(b) 統合**: モーダルを廃し、`lecture_studio.rewrite_chunk_script` capability として Copilot に一本化。UI 一貫性は最良だが原稿スタジオの改修が大きい。
  - **推奨: 段階的に (a)→(b)**。Phase 1 は (a) で既存機能を壊さず統合の器を作り、Phase 3 で (b)。

### 9.4 道案内の実行（`runLocatePlan()` + スポットライト）
- 入力: §8.1 の `locate_plan.steps`。各ステップを順に処理する。
```js
AdminAssistant.runLocatePlan = function (plan) {
  var i = 0;
  function step() {
    if (i >= plan.steps.length) return;
    var s = plan.steps[i++];
    activateTabView(s.screen);                       // タブ遷移（既存 admin.js:204）
    var el = AdminAssistant.resolveAnchor(s.screen, s.anchor_id); // §9.5
    if (!el) { AdminAssistant.say("この先はこの画面の操作後に案内します"); return; } // 捏造しない(P4)
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("admin-assistant-spotlight");   // 点灯（一時ハイライト）
    setTimeout(function () { el.classList.remove("admin-assistant-spotlight"); }, 4000);
    AdminAssistant.showHint(el, s.hint);             // 吹き出し
    if (s.precondition && !AdminAssistant.preconditionMet(s.screen, s.precondition)) {
      AdminAssistant.awaitPrecondition(s.screen, s.precondition, step); // 選択完了を待って次へ
    } else { step(); }                               // 前提が要らないステップは連続点灯
  }
  step();
};
```
- **点灯（スポットライト）**: 既存の一時ハイライト（`.mg-highlight` [`styles.css:411`](../../frontend/public/css/styles.css) / `atlas-pulse` [`atlas-overlay.js:508`](../../frontend/public/js/atlas-overlay.js)）と同系。新 class `.admin-assistant-spotlight`（`box-shadow` の脈動 + 4秒フェード）を `styles.css` に追加。既存 `highlightTarget()`（[`atlas-assist-panel.js:357`](../../frontend/public/js/atlas-assist-panel.js)）が骨格エディタで同様の「対象点灯」を既に行っており、実装様式はそれに倣える。
- **多段（precondition）**: 前提未達のステップで停止し、画面が発火する選択完了イベント（例 `lecture-studio` のチャンク選択）を `awaitPrecondition` が待って次ステップへ。無限待ちを避けるためタイムアウト（例 30s）で「見つからないときは…」の説明にフォールバック。
- **入力・送信はしない（P8）**: `runLocatePlan` は `click()`/`value=` を行わない。点灯と誘導のみ。

### 9.5 UIアンカー登録（`registerUiAnchors`）
バックエンドの `locate_steps` は**論理 anchor_id** のみを持ち、実 DOM セレクタは各画面が登録する（DOM 構造の所有権を画面側に残し、registry を DOM 非依存に保つ）。`registerScreenContext` / `registerUndoHandler` と同じ hook 群の一員。
```js
AdminAssistant.registerUiAnchors("lecture-studio", {
  "chunk_list":            function () { return document.getElementById("ls-chunk-list"); },
  "assistant_open_button": function () { return document.getElementById("ls-assistant-open"); },
  "ls-rewrite-prompt":     function () { return document.getElementById("ls-rewrite-prompt"); }
});
AdminAssistant.registerUiAnchors("materials", {
  "upload_dropzone": function () { return document.getElementById("upload-dropzone"); }
});
```
- `resolveAnchor(screen, anchor_id)`: `{course_id}` 等の差し込みを解決してから登録関数を引き、返った要素を返す（無ければ null）。
- アンカー未登録の画面は道案内対象外（説明モードには影響しない）。段階的に主要導線から登録していく（fail-closed）。

### 9.6 戻すボタン
- パネル下部に常設の「↩ 直前の操作を戻す」。`ActionStack` の先頭が `local` なら L1 復元、`server` なら §8.3 を呼ぶ。
- `reversible=false` の直後は戻すを無効表示（ツールチップ「この操作は取り消せません」）。
- 道案内（§9.4）は変更を伴わないため ActionStack に積まない（戻す対象外）。

---

## 10. 監査・安全

- **監査**: apply / revert / confirm を `_record_review_event(entity_type='assistant_action', ...)` で記録（既存関数を再利用、C/D層と同方式）。`entity_type` の許容値拡張のみ。
- **fail-closed（P1）**: capability 未登録・ロール不足・scope 不一致は実行拒否。フロントの表示を信頼せずサーバで判定。
- **確認ゲート（P2）**: `reversible=false` は必ず `confirm_required=true`。二重送信防止のため確認トークン（`action_plan` に nonce）を付与。
- **PII / シークレット**: `screen_context` に生パスワードやトークンを載せない（フロントで除外）。LLM へ渡すのは表示用メタのみ。
- **コスト上限**: `ASSISTANT_MAX_CALLS_PER_DAY`（既定10）等の env（D層 `DOUBT_SCOPE_MAX_CALLS_PER_DAY` に倣う）。モデルは fast tier 既定（`ASSISTANT_LLM_MODEL` で上書き）。LLM は `system` ロール/`temperature` 回避（開発ルール4、o1系互換）。

---

## 11. 実装フェーズ

| Phase | 内容 | 主な成果物 |
|---|---|---|
| **P1: 説明・道案内モード + 器** | フローティングパネル / チャットUI流用 / `assistant/chat` の guidance + locate / capability registry（読み取り系中心 + `locate_steps`）/ 操作KB 初版 / role フィルタ / スポットライト（`runLocatePlan` + `registerUiAnchors`、主要導線から段階登録） | `admin-assistant.js`, `admin_assistant.py`, `capabilities.py`, `knowledge.py`, `docs/admin_operations/*.md`, `.admin-assistant-spotlight`(css) |
| **P2: 操作代行（可逆）+ 戻す** | `assistant/actions` / L1・L2 Undo / migration 034 / 監査 / 原稿 rewrite をラップ吸収(a) | `actions/`, `action_store.py`, `034_assistant_actions.sql` |
| **P3: 不可逆操作 + 確認ゲート + 統合仕上げ** | 削除・公開・freeze・アカウント作成の confirm フロー / 原稿スタジオUIの一本化(b) / 戻す履歴 | 確認カードUI, registry 拡張 |
| **P4: 磨き込み** | 会話セッション永続 / KB 拡充 / KPI（`theory_review_events` 再集計で `GET /admin/assistant/metrics`） | – |

各フェーズ後に CI テスト追加（`episteme-graph-ci-tests` スキル、§12）。

---

## 12. テスト方針

`backend/tests/`（FastAPI/core）に追加。

- **権限**: 各ロール（STUDENT/TEACHER/SYSTEM_ADMIN）× capability で、guidance フィルタ・locate 可否・action 実行の許可/拒否を検証（STUDENT は `/api/admin/assistant/*` に到達不可）。
- **fail-closed**: 未登録 capability / scope 不一致（他人のコース）で 403/拒否。
- **道案内（locate）**: TEACHER が SYSTEM_ADMIN 専用操作の場所を尋ねても `locate_plan` を返さない（P1/P8）。`locate_steps` の全 anchor_id が、対応画面のいずれかで登録され得る論理IDであること（registry ⇄ `registerUiAnchors` の整合、構造テスト）。locate 応答が DB を変更しない・監査行を作らないこと。
- **Undo**: L2 apply→revert で before に戻ることを DB レベルで検証。`reversible=false` の revert が 409。
- **確認ゲート**: `reversible=false` は無確認実行を拒否。
- **監査**: apply/revert が `theory_review_events` に記録される。
- **非改変**: 既存 `course-builder/chat`・`lecture-script/rewrite`・`theory-components/candidates` の契約が変わっていない（既存テストが通る）。
- **ガードレール的構造テスト**（D層 `test_doubt_guardrails.py` に倣う）: registry の全 `reversible=false` が `confirm=True` を持つ／`core/admin_assistant/` が FastAPI を import しない、を静的に検査。

---

## 13. 決定事項と選択肢

以下は本設計で採った推奨と、実装前に確認したい分岐。

1. **代行の実行モデル（採用: リスク階層）**: 可逆は即適用+戻す、不可逆のみ確認。「代行」体験と安全性の両立。
   - 代替: 全操作を必ず確認（安全だが「代行」感が薄い）。
2. **原稿スタジオの統合（採用: (a)ラップ → (b)一本化 の段階移行）**。
3. **操作KBの検索（採用: capability タグ + キーワード。必要なら pgvector）**。全 KB を LLM に丸投げしない（P4/コスト）。
4. **セッション永続（採用: MVP はフロント保持、Phase 4 で永続）**。
5. **capability 網羅度（採用: 安全・高頻度から段階登録）**。全 admin API を初手で載せない（fail-closed 維持）。

### 未決（実装着手前に確認したい点）
- **Q1**: 代行対象の初期スコープは「原稿スタジオ + コース公開/可視性 + 教材可視性」で十分か、それとも骨格生成やスキーマ提案まで含めるか。
- **Q2**: 「戻す」は直前1手のみ（MVP）で良いか、複数手の履歴を最初から要るか。
- **Q3**: SYSTEM_ADMIN 専用操作（教員作成・エラーログ）を代行対象に含めるか、説明のみに留めるか。
- **Q4**: 操作KB は本設計で新規に書き起こす前提だが、既存 `docs/features/*.md` からの流用範囲。
- **Q5**: 道案内の点灯先（`registerUiAnchors`）を初期にどこまで整備するか。まず最頻の導線（教材アップロード / コース公開 / 原稿修正）から登録し、未登録画面は「説明のみ・点灯なし」に縮退する方針で良いか。

---

## 14. 影響範囲サマリ（新規/変更ファイル）

**新規**
- `backend/api/routes/admin_assistant.py` — `/api/admin/assistant/*`
- `backend/core/admin_assistant/{__init__,capabilities,knowledge,intent,action_store,schema}.py` + `actions/`
- `backend/db/034_assistant_actions.sql`
- `frontend/public/js/admin-assistant.js`
- `docs/admin_operations/*.md`（操作KB）
- `backend/tests/test_admin_assistant.py`

**変更（最小限）**
- `backend/api/main.py` — ルータ登録
- `backend/api/schemas.py` — `Assistant*` モデル
- `frontend/public/admin.html` — パネル/トグルの DOM、`admin-assistant.js` 読み込み
- `frontend/public/js/admin.js` — `initApp()` から `AdminAssistant.init(...)`、各画面の `registerScreenContext` / `registerUndoHandler` / `registerUiAnchors`（道案内の点灯先）
- `frontend/public/css/styles.css` — `.admin-assistant-*`（`.admin-assistant-spotlight` 点灯を含む）
- `.env.example` — `ASSISTANT_LLM_MODEL` / `ASSISTANT_MAX_CALLS_PER_DAY`

**変更しない（P7）**: `src/episteme_graph/agents/`（A層）、既存の course-builder/chat・lecture-script/rewrite・theory-components/candidates の**実装本体**（呼ぶだけ）。
