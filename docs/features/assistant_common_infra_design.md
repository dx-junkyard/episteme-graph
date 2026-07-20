# チャット型 AI 支援の共通基盤整理 — 設計書（2026-07-20）

対ユーザー支援エージェント調査
（[user_assistant_agents_survey_2026-07.md](../architecture/user_assistant_agents_survey_2026-07.md)）
の §6「共通化すべきところ」全項目と §8 の 🔴 2件を実装するための設計。
実装順・判断基準の正本。

## 0. 不変条項（全サブタスク共通）

- **I1 既定値は現行挙動を保存する**: モデル解決・履歴ウィンドウの既定は「現在と同じ
  応答内容」になるよう選ぶ。fast への暗黙格下げ・先頭コンテキストの欠落を起こさない。
- **I2 数値を見せない**: 上限超過の 429 は事実文のみ（残数・上限値を返さない。
  既存 W層・Copilot と同文体）。
- **I3 会話は死なせない**: チャット型のメイン応答の LLM 失敗は degraded 固定文
  （200）に縮退し、対話を継続する。500 即死をやめる。
- **I4 情報を落とさない（P4）**: degraded ターンも履歴に保存する。ゲート超過・
  縮退は正直にフラグで返す。
- **I5 fail-closed**: 権限ゲートは既存の `_ensure_document_*` / 
  `resolve_document_access` の流儀（不可視は 404）に合わせる。
- **I6 コスト上限の in-memory 制約は許容し明文化する**: CostGate はプロセスローカル
  （マルチワーカーで実効上限が緩む・再起動でリセット）。DB 化は本整理の非スコープ。
  この制約は本書と CLAUDE.md に明記して許容する。

## 1. コスト上限（CostGate 一本化 + 無制限3経路への導入）

### 1-1. 新 env var（`backend/core/config.py`）

| env var | Settings フィールド | 既定 | 対象 |
|---|---|---|---|
| `LEARNING_CHAT_MAX_CALLS_PER_DAY` | `learning_chat_max_calls_per_day` | 300 | 学習チャット（`POST .../chat` 1リクエスト=1カウント） |
| `COURSE_BUILDER_MAX_CALLS_PER_DAY` | `course_builder_max_calls_per_day` | 100 | コースビルダーチャット |
| `LECTURE_REWRITE_MAX_CALLS_PER_DAY` | `lecture_rewrite_max_calls_per_day` | 100 | 原稿スタジオ rewrite（scripts.py / topics.py の両経路で**同一ゲートインスタンス**） |

- ゲートは `core/llm_worker/cost_gate.py::CostGate` の day-only 構成
  （キーは `(today_str(), user_id)`）。既存 worker 系と同じ。
- カウント単位は「LLM を伴うリクエスト1回」（学習チャットは intent 分類〜本体まで
  含めて1）。埋め込み・音声 transcribe/speak・履歴 GET/DELETE は数えない。
- 超過時は 429。detail は事実文（例: 「本日の AI 呼び出し回数の上限に達しました。
  明日以降に再度お試しください。」）。
- **Admin Copilot**: `_reserve_llm_quota` の独自 dict（`admin_assistant.py:92,120-134`）
  を CostGate(day) に置換。`ASSISTANT_MAX_CALLS_PER_DAY`（既定20）と挙動
  （超過時は LLM なしのヒューリスティック縮退。429 にしない）は不変。
- **Field Atlas assist の DB 集計は維持**（`atlas.py:578-592`）: 再起動を跨いで
  数えられる方が強いため、意図的な差分として本書で明文化する（置換しない）。
- **apparatus の二重ガード**（CostGate + orchestrator の DB 集計）は現状維持・
  既知の限界として survey 文書に記載済み（本整理の非スコープ）。

## 2. 会話履歴ウィンドウ化（共通ユーティリティ）

### 2-1. 正本モジュール

`backend/core/llm_worker/history.py`（新設。FastAPI / LLM SDK 非 import）:

```python
def window_history(
    history: list | None,
    *,
    max_messages: int = 20,
    max_chars: int = 2000,
    head_keep: int = 0,
    current_message: str | None = None,
) -> list[dict]:
```

セマンティクス（Admin Copilot の `_normalize_history` を一般化したもの）:

1. dict 以外・`role not in ("user","assistant")`・空 content はスキップ。
2. content は文字列化・strip・`max_chars` でトリム。
3. `current_message` 指定時、末尾が同内容の user メッセージなら1件除去
   （フロントが送信直前に現在発話を履歴へ push する実装との二重化防止）。
4. `len > head_keep + max_messages` のとき **先頭 `head_keep` 件 + 末尾
   `max_messages` 件**を返す（head 保護）。それ以下なら全件。

### 2-2. 各チャットへの適用パラメータ

| 呼び出し元 | max_messages | max_chars | head_keep | 理由 |
|---|---|---|---|---|
| 学習チャット本体（`learning.py:1998` 付近） | 20 | 2000 | 0 | 直近20メッセージで通常の学習対話は十分。教材・RAG は毎回別途注入されるため先頭保護不要 |
| グラフ要素説明（`learning.py:1008`、現行 -6 件） | 6 | 2000 | 0 | 現行挙動をユーティリティ経由に置換（挙動同一） |
| コースビルダー（`admin.py:1817` 付近） | 20 | 4000 | **2** | フロントが course_draft をJSON化し**履歴先頭に疑似ターン注入**する（`admin.js:3036-3048`）。先頭2件を保護しないとドラフトがプロンプトから消える |
| W層対話（`routes/deliberation.py:836-840`） | 16 | 4000 | **1** | grounding は**最初の user メッセージにのみ**注入される（`dialogue.py:548-568`）ため先頭1件を保護。セッション8コール上限下では実質 no-op（安全網） |
| Admin Copilot（`intent.py::_normalize_history`） | 8 | 500 | 0 | 既存挙動を維持したまま、実装を共通ユーティリティへの委譲に置換（`_normalize_history` は薄いラッパとして残し既存テストを維持） |

- コースビルダーのドラフト注入をサーバ側注入へ作り替える案は**本整理では見送り**
  （フロント・セッション同期の再設計が必要。head_keep で安全に保護できる）。

## 3. モデル解決の統一（resolve_model）

- `core/llm_worker/client.py::resolve_model` に fallback 引数を追加:
  `resolve_model(key, *, fallback="fast")`。`"fast"` → `llm_fast_model`、
  `"analysis"` → `llm_analysis_model`。既定 `"fast"` で後方互換。
- 新 env var（既定はすべて空 = **現行モデルへフォールバック**、I1）:

| env var | 既定の解決先 | 適用箇所 |
|---|---|---|
| `LEARNING_CHAT_LLM_MODEL` | 空 → **analysis**（現行の暗黙依存を明示化） | 学習チャット本体の `generate_text`（`learning.py:2005`） |
| `COURSE_BUILDER_LLM_MODEL` | 空 → **analysis** | コースビルダー（`admin.py:1823`） |

- Admin Copilot: `_assistant_model()`（`admin_assistant.py:111-117`）を
  `resolve_model("assistant_llm_model")` に置換（fast フォールバック維持）。
- Field Atlas: `atlas_generator.py:370` を
  `resolve_model("atlas_assist_llm_model", fallback="analysis")` に置換
  （**analysis フォールバックの意図的差分を維持**）。
- 学習チャット内の intent 分類 / advice / 採点の tier 系（`get_llm_params`）は変更しない。

## 4. 縮退規約の統一

- **学習チャット本体**（`learning.py:2005-2008`）: LLM 例外時に 500 を投げず、
  固定文（「AI 応答を生成できませんでした。しばらくしてからもう一度お試し
  ください。」）を `degraded: true` 付きで 200 返却。degraded ターンも
  `persist_chat_history` で保存（I4）。誤解検出・ドリルダウン等、**回答本文に依存する
  後処理はスキップ**する。質問側の interest_traces 記録は行う（P4）。
  `LearningChatResponse` に `degraded: bool = False` を追加。
- **コースビルダー**（`admin.py:1824-1826`）: 同様に degraded 固定文 + 200。
  `course_draft` は変更なし（None）。セッション保存は通常どおり行う。
- **コンポーネント候補生成（B2）**: 「0件」と「失敗」を区別する。
  `core/component_candidates.py` の握りつぶし（`:120-122`）をやめ、LLM 例外は
  専用例外（`CandidateGenerationError`）で送出 → route（`theory_components.py:3469`）
  が 503 + 事実文に変換。真の0件のみ 200 + `created=[]`。
- **原稿スタジオ rewrite は 500 維持**: 単発の明示操作であり「失敗をエラーとして
  返す」のが正しい UX（縮退統一の対象はマルチターン対話のみ）。本書で明文化。
- Copilot（heuristic 縮退）・W層（degraded 縮退）は現行のまま。

## 5. 原稿スタジオ rewrite / save の権限ゲート（🔴）

- 対象: `lecture_studio/scripts.py::rewrite_lecture_script`（:845）/
  `save_lecture_script`（:615）、および `topics.py` の rewrite 経路（:533 付近）に
  同種の欠如があれば同時に是正。
- 方式: chunk 行から `material_id` を取得し、
  `services.resolve_document_access(user_id, material_id)` で **edit 権限**を要求
  （CLAUDE.md の横断基盤に定める権限判定の入口。owner / editor / SYSTEM_ADMIN）。
  不可視・権限なしは既存流儀に合わせ 404（fail-closed、I5）。
- 同ファイルの settings 系（`get_editable_course_data` 使用）と権限モデルの
  水準を揃えることが目的。コース単位でなく **document 単位**で判定する
  （chunk の帰属は document であり、course を経由しない教材にも効くため）。

## 6. `save_cb_session` の正直化（🔴）

- `services.py::save_cb_session`（:4272-4308）: 例外握りつぶしをやめ、
  成否 bool を返す（例外は内部で catch し False。ログは維持）。
- `admin.py::course_builder_chat`: 応答に `session_saved: bool` を追加。
  チャット応答自体は返す（生成済みの回答を失わない）。
- `admin.js`: `session_saved === false` のとき「セッションの保存に失敗しました。
  履歴が保存されていない可能性があります」を画面に警告表示（answer は表示する）。
- セッション CRUD（PUT /sessions/{id} 等）の既存挙動は変更しない。

## 7. usage_context の補完

- `check_topic_understanding`（`learning.py:1344-1400`）の `generate_text` を
  `usage_context("learning:understanding_check", user_id=...)` でラップ。
- feature 語彙 `learning:understanding_check` を `core/llm_usage/schema.py` の
  KNOWN_FEATURES に追加（語彙の正本を更新）。

## 8. 非スコープ（本整理では行わない）

- 音声 transcribe / speak エンドポイント自体の回数上限
- CostGate の DB 化（I6 で明文化のうえ許容）
- 会話本文の不変監査ログ（P3 との設計判断が別途必要）
- casual モードのテキスト UI
- Copilot 応答文の LLM 生成化・画面コンテキスト全タブ展開
- デッドコード削除（`chat_sessions`/`chat_messages`、`core/chat.py`、
  `_llm_retry_policy` — 別タスク）
- コースビルダーのドラフト注入のサーバ側移設（§2-2）

## 9. テスト計画

- `history.py` 単体（head 保護・トリム・重複除去・境界）
- 各ゲート: 上限到達で 429（or Copilot は縮退）、翌日キーで回復、
  対象外エンドポイントが数えないこと
- rewrite/save: 非所有 TEACHER が 404、owner/editor/SYSTEM_ADMIN が成功
- degraded: LLM 例外時に 200 + degraded + 履歴保存、誤解検出スキップ
- B2: LLM 例外 → 503、0件 → 200
- `save_cb_session`: DB 失敗時に `session_saved: false`
- resolve_model: fallback 引数、Copilot/atlas の置換後挙動同一
- 既存ガードレール（test_admin_assistant.py の `_normalize_history` テスト等）を
  壊さない

## 10. 実施記録（2026-07-20 実装完了）

全項目実装済み。バックエンド **5,029 pass / 0 fail**（+ src 1,608 pass）。
Fable 5 指揮 + Sonnet サブエージェント6体（Phase 1 基盤×1 → Phase 2 並行×5）で実施。

| 設計節 | 実装 | 主な変更ファイル |
|---|---|---|
| §1 コスト上限 | CostGate(day-only) を学習チャット・コースビルダー・rewrite に導入（429 + 事実文）。Copilot の独自 dict を CostGate に置換（縮退挙動は不変）。atlas の DB 集計は設計どおり維持 | `learning.py` / `admin.py` / `lecture_studio/_shared.py` / `admin_assistant.py` |
| §2 履歴ウィンドウ化 | `core/llm_worker/history.py::window_history` 新設。学習チャット(20/2000)・グラフ要素説明(6/2000)・コースビルダー(20/4000/head_keep=2 — draft 疑似ターン2件を実コードで確認)・W層(16/4000/head_keep=1 — grounding 注入先の先頭 user メッセージを保護、`run_turn` 呼び出し前に適用)・Copilot `_normalize_history` は委譲化 | `history.py` / 各 route / `intent.py` |
| §3 モデル解決 | `resolve_model(key, *, fallback)` 拡張。学習チャット・コースビルダーは新 env var（空 → analysis で現行挙動保存）。Copilot `_assistant_model` / atlas `_assist_model` を委譲化 | `llm_worker/client.py` / `config.py` / `atlas_generator.py` |
| §4 縮退統一 | 学習チャット・コースビルダーの 500 即死を廃止（degraded 固定文 + 200 + 履歴保存 + 誤解検出等スキップ）。B2 は `CandidateGenerationError` → 503 で「0件」と区別。rewrite は設計どおり 500 維持 | `learning.py` / `admin.py` / `schemas.py` / `component_candidates.py` / `theory_components.py` |
| §5 rewrite/save 権限（🔴） | `_ensure_chunk_editable`（chunk→material_id→`resolve_document_access` edit 要求、fail-closed 404）を rewrite/save に導入。**追加発見**: `topics.py` の rewrite が閲覧権限のみだった欠如も editable 水準（`_course_data_for_studio_editable`）に是正 | `lecture_studio/_shared.py` / `scripts.py` / `topics.py` |
| §6 save_cb_session（🔴） | 成否 bool 返却 + レスポンス `session_saved` + admin.js 警告表示 | `services.py` / `admin.py` / `admin.js` |
| §7 usage_context | `check_topic_understanding` を `learning:understanding_check` でラップ（KNOWN_FEATURES に追加） | `learning.py` / `llm_usage/schema.py` |

新設テスト: `test_llm_worker_history.py`(24) / `test_learning_chat_infra.py`(15) /
`test_course_builder_infra.py`(14) / `test_lecture_rewrite_gates.py`(27) /
`test_assistant_infra_unification.py`(27) / `test_component_candidates_failure.py`(5)。
既存テストの更新は `test_endorsement_sharing.py`（B2 の新仕様追随）と
`tests/core/test_llm_worker.py`（fallback 引数）のみ。

実装上の注記:
- コースビルダーのレスポンス拡張は schemas.py 非改変のためローカルサブクラス
  `_CourseBuilderChatResponseOut` で対応（admin.py 内）。
- 429 / degraded の文言は学習チャット・コースビルダーで完全一致させた。
- 学習チャットのゲート消費は「LLM を実際に呼ぶ直前・リクエスト内1回」
  （承認済み説明のグラフ要素タップ等、非LLM パスは消費しない）。
- docker E2E は未実施（ローカル .venv での pytest のみ）。
