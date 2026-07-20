# 対ユーザー支援エージェント 全体調査（2026-07-20）

教員・学習者向けに「対ユーザー支援エージェント」として動作している機能を全列挙し、
それぞれの動作機構を横断比較したうえで、**共通の仕組みにするべきところ / 意図的に
分けるべきところ / 足りない機能** を洗い出した調査記録。

- 調査方法: Fable 5 指揮 + Sonnet サブエージェント5体による領域別コード実査
  （学習チャット / コースビルダー等教員AI / W層対話・図再解析 / 非対話型支援 /
  LLM共通基盤マップ）。Admin Copilot は同日の先行調査結果を統合。
  すべて実コードの Read/Grep に基づく（file:line 付き）。
- 関連文書: [consolidation_survey_2026-07.md](consolidation_survey_2026-07.md)
  （横断基盤の整理記録）、`docs/features/admin_assistant_design.md`、
  `docs/features/element_deliberation_workspace_design.md`
- 注: 本調査と同日に Admin Copilot の会話履歴デッドパラメータ修正
  （`core/admin_assistant/intent.py` の `_normalize_history` 追加）を実施済み。
  本文の記述は修正後の状態を正とする。

---

## 1. インベントリ（3分類）

### A. チャット型（マルチターン対話・同期）

| # | 名称 | 対象 | 入口 |
|---|---|---|---|
| A1 | 学習チャット（RAG + casual + 音声） | 学習者 | `POST /api/learning/courses/{id}/topics/{id}/chat`（`backend/api/routes/learning.py:1731`） |
| A2 | Admin Copilot（管理画面操作アシスタント） | 教員/管理者 | `POST /api/admin/assistant/chat`（`backend/api/routes/admin_assistant.py:487`） |
| A3 | コースビルダーチャット | 教員 | `POST /api/admin/course-builder/chat`（`backend/api/routes/admin.py:1785`） |
| A4 | W層 対話的検討（要素検討・vision 対応） | 教員 | `POST /api/admin/deliberation/sessions/{id}/messages`（`backend/api/routes/deliberation.py:778`） |

### B. 単発型 AI 支援（1リクエスト完結・履歴なし）

| # | 名称 | 対象 | 特記 |
|---|---|---|---|
| B1 | 原稿スタジオ AI 書き換え | 教員 | fast tier・studio_view 別4プロンプト（`backend/api/routes/lecture_studio/scripts.py:841`） |
| B2 | コンポーネント候補生成（C層） | 教員 | structured output + サニタイズが最も堅牢（`backend/core/component_candidates.py`） |
| B3 | 教員指示付き図再解析（guided reanalysis） | 教員 | 1回のAPIで最大十数 LLM コール（hypothesis→observation→alignment→verification、`backend/core/figure_reanalysis.py:505`） |

### C. 非対話型・提案型（裏で候補生成 → 本人確認のみ求める）

| # | 名称 | 対象 | LLM |
|---|---|---|---|
| C1 | TensionMiningAgent（B層） | 学習者 | 非同期 worker（llm_worker フル利用） |
| C2 | structure_anchor（3経路: 明示選択/非同期帰属/回答末尾確認） | 学習者 | 同上 |
| C3 | 再構成ループ（R層） | 学習者/教員 | オーサリングのみ LLM。実行時 DIFF は非LLM |
| C4 | G層 次にやること（AdminNextSteps） | 教員 | 非LLM（状態導出型 To-Do） |
| C5 | Atlas cues（節目カード） | 学習者 | 非LLM |
| C6 | 通知インボックス（status/V層統合） | 教員 | 非LLM（決定論 fan-out） |

---

## 2. チャット型4者の機構詳細

### A1. 学習チャット

- **同期/非同期**: 本体 `learning_chat()` は同期（`learning.py:1735`）。応答後に
  tension / anchor の worker を `threading.Thread` で best-effort 起動
  （`learning.py:2070-2075`、応答は遅延させない）。
- **LLM コール**（1ターンあたり、ケース分岐）:
  - `_classify_intent()`（`learning.py:611`）: fast tier。casual / 型付き action /
    atlas_context ではスキップ。
  - 本体 RAG 回答 `generate_text(messages, temperature=0.3)`（`learning.py:2005`）:
    **model 未指定 → `LLM_ANALYSIS_MODEL` に暗黙フォールバック**。
  - `_generate_learning_advice_response()` / `_generate_graph_element_explanation()`:
    standard tier。
  - 埋め込み `embed_text`（`services.py:1371`）が別途1回。
  - **同一エンドポイント内でモデル解決方式が二重化**（tier 系 `get_llm_params` と
    暗黙デフォルトの混在）。
- **履歴**: 正本は `learning_chat_history`（`backend/db/init.sql:207-215`、
  `(user_id, course_id, topic_id)` 一意・history JSONB を**全件 UPSERT 上書き**）。
  LLM へは `body.history` を**全件・無制限**で渡す（`learning.py:1998-1999`）。
  同ファイル内のグラフ要素説明だけは直近6件に絞る（`learning.py:1008-1011`）—
  **同一ファイル内で不統一**。書き直し/削除は `truncate_chat_and_supersede()`
  （`services.py:2026`）でサーバ正本を切り詰め（クライアント履歴は信用しない）。
- **コンテキスト注入**: 表示中トピック教材 先頭5000字 + pgvector 検索 top_k=8
  （スコア0.30以上、tier / origin 付き）+ OutOfSourceGuard（out_of_source 時）+
  casual 用は別システムプロンプト。`selection_text` は anchor 記録に使うが
  **回答生成プロンプトには入らない**。
- **コスト上限**: **なし**（`learning.py` 全体に CostGate / 429 は0件）。
  音声は 1リクエスト 10MB のみ（`learning.py:2494`）。
- **縮退**: intent 分類・advice は例外握りつぶしでフォールバックする一方、
  **本体回答生成は失敗すると 500 即死**（`learning.py:2006-2008`）— 非対称。
- **監査**: 会話本文の不変ログなし。`interest_traces`（質問側のみ・supersede 保持）、
  `unanswered_query_logs`、`llm_usage_events`（トークンのみ）が周辺記録。
- **音声**: `POST /voice/transcribe`（Whisper 系、`learning.py:2501`）/
  `POST /voice/speak`（TTS、`learning.py:2527`）。フロントは MediaRecorder + RMS VAD
  （無音1400ms 区切り）で `intent_mode="casual"` 送信 → TTS 再生（`app.js:3000-3245`）。
  **casual はハンズフリー音声からしか入れない**（テキスト UI なし、`app.js:3155`）。

### A2. Admin Copilot

- **3モード**: `intent.py` が guidance / locate / action / clarify / status_query に
  分類。ヒューリスティック一次分類 + `allow_llm` 時のみ 1 LLM コールで精緻化
  （`intent.py:235-262`）。**応答文は KB 検索 + テンプレート組み立てで LLM 生成ではない**。
- **履歴**: フロントが直近10件を送信（`admin-assistant.js:265-271`）。サーバ側は
  2026-07-20 の修正で `_normalize_history`（8ターン/500字/重複除去）を経て
  唯一の LLM コールに注入（従来はデッドパラメータ）。**DB 保存はなし**
  （`assistant_actions` は操作代行の before/after 台帳であり会話ログではない）。
  リロードで履歴消失。
- **画面コンテキスト**: `{tab, selection, visible_entities}` を毎回送信
  （`admin-assistant.js:76-88`）。ただし provider 登録は **lecture-studio のみ**
  （`admin.js:7633`、`admin-lecture-studio.js:7159` の `getScreenContext`）で、
  他タブは「タブ名だけ」。用途は intent tie-break（`intent.py:122-136`）・
  LLM プロンプト・locate プレースホルダ解決・action 対象特定。
  **`capabilities_for_screen`（`capabilities.py:594`）は定義のみで呼び出し元ゼロ**。
- **コスト上限**: `ASSISTANT_MAX_CALLS_PER_DAY`（既定20）を**独自 dict 実装**
  `_reserve_llm_quota`（`admin_assistant.py:120-134`）で管理（CostGate 不使用）。
  モデルも `_assistant_model()`（`admin_assistant.py:111-117`）が
  `resolve_model()` を使わず同等ロジックを再実装。
- **縮退**: LLM 失敗・quota 超過時はヒューリスティック分類へ（P6）。
- **監査**: apply / revert / confirm を `theory_review_events`
  （`entity_type='assistant_action'`）に記録。チャット本文は記録しない。

### A3. コースビルダーチャット

- **LLM コール**: 1リクエスト1回。`generate_text(messages, temperature=0.4)`
  （`admin.py:1823`）— **model 未指定 → `LLM_ANALYSIS_MODEL`**。出力は自由文 +
  `---COURSE_DRAFT_JSON---` 区切りの半構造化。パース失敗は握りつぶして
  `course_draft=None`（`admin.py:1440-1472`、repair なし）。
- **履歴**: 正本は `course_builder_sessions`（`backend/db/002_a1_a2_a3.sql:12-20` +
  018 で status 等追加）。フロントは `state.chatHistory` を**全件毎回送信**
  （`admin.js:3050-3057`）、サーバも**全件を LLM へ**（`admin.py:1817-1818`、切り詰めなし）。
  セッションは遅延作成（初回送信時）・一覧から手動復元・自動復元なし
  （`admin.js:2886, 3021-3028`）。
  **`save_cb_session` は例外を握りつぶすため保存失敗でも 200 が返る**
  （`services.py:4272-4308`）。
- **コンテキスト注入**: `selected_material_ids` → `_build_material_context`
  （`admin.py:1486-1782`）。教材あたり component 40 / graph edge 80 / claim 80 /
  chunk 4000字の上限はあるが**教材点数の上限なし**。
- **コスト上限**: **なし**。
- **縮退**: LLM 失敗で 500 即死（`admin.py:1824-1826`）。
- **監査**: なし（logger.info のみ）。usage は `admin:course_builder` で計測。
- 補: `core/course_content_builder.py` はビルダーチャットとは別系統
  （コース承認後・トピック閲覧時のコンテンツ生成）。

### A4. W層 対話的検討

- **LLM コール**: 1ターン1回。`generate_conversation_turn`（`core/llm.py:1230`、
  **OpenAI 専用**・他プロバイダは NotImplementedError）。structured output で
  reply + 候補注釈を同時取得。`run_with_repair` は**意図的に不使用**
  （W6 同期パスを重くしない、`dialogue.py:23-25`）。
- **履歴**: 正本は `deliberation_sessions.messages` JSONB（**追記のみ**・P4、
  `store.py:103-130`）。LLM へは**全件**（`deliberation.py:836-840`、切り詰めなし。
  8コール上限による暗黙抑制のみ）。**クライアントは履歴を送らない**
  （`{content}` + `selected_context` のみ、`deliberation.js:2190-2232`）。
  セッション復元 UI は v1 なし（モーダル閉で新規、`deliberation.js:28`）。
- **コンテキスト注入**: grounding（内訳 + 4レンズ + context lens + 説明 + 同一性候補）を
  **最初の user メッセージにのみ**注入（`dialogue.py:548-568`）。figure は毎ターン
  MinIO から画像取得し**その回のみ**添付（過去ターンの画像は再送しない、
  `llm.py:1271-1283`）。
- **コスト上限**: `DELIBERATION_MAX_CALLS_PER_SESSION`（8）/ `_PER_DAY`（40）を
  **CostGate** で管理（`dialogue.py:136-154`）。超過は 429（残数は返さない）。
- **縮退**: LLM 失敗時は `degraded=True` の固定文で**対話は継続**
  （`dialogue.py:598-611`）。ユーザー発話・縮退応答とも DB 保存される。
- **監査**: session.create / annotation.candidate_generated / commit / dismiss を
  `entity_type='deliberation'` で記録。**会話本文そのものは監査対象外**。
- **権限**: 閲覧 `_ensure_document_viewable`（作成・送信の両方）、
  注釈 commit/dismiss は editable。セッションは作成者本人のみ。

---

## 3. 単発型の機構要点

### B1. 原稿スタジオ AI 書き換え（`lecture_studio/scripts.py:841-983`）

- fast tier（`get_llm_params("fast")`）1コール。疑似構造化（自由文プロンプトで
  JSON を要求 → 手動 `json.loads`、repair なし・失敗は 500）。
- chunk の text/display/spoken/formulas/ancestors をトリミング注入。theory view
  のみ `body.theory_components` を追加注入。persona 注入あり。
- **コスト上限なし**。バッチ生成は `time.sleep(1.5)` のみ。
- 🔴 **権限不整合**: `rewrite_lecture_script`（`scripts.py:845`）と
  `save_lecture_script`（`scripts.py:615`）は `_require_teacher` のみで
  **コース所有権チェックがない**（chunk_id 直指定で他教員のチャンクも書き換え可能）。
  同ファイルの settings 系は `get_viewable/editable_course_data` を通しており不一致。
- 版管理・監査なし（`chunks` を直接 UPDATE 上書き）。

### B2. コンポーネント候補生成（C層、`theory_components.py:3469-3543`）

- structured output（`CandidateGenerationResult`）+ **最も堅牢なサニタイズ**
  （component_type 語彙化・backing_claims の実在フィルタで捏造防止、
  `component_candidates.py:124-147`）。
- LLM 失敗は**空配列に縮退して 200**（`component_candidates.py:120-122`）—
  教員から「該当なし」と「例外」が区別できない点は注意。
- 権限は `_require_teacher` + `_ensure_editable(course_id)` — **B群で唯一
  コース単位の権限チェックあり**。監査も `theory_review_events` に記録
  （`theory_components.py:3529-3532`）。入口は原稿スタジオの手動貼り付け
  （学生チャットからの自動連携ではない。`admin-lecture-studio.js:5336`）。

### B3. 教員指示付き図再解析（`figure_reanalysis.py:505-744`）

- 完全同期だが内部は反復パイプライン（`APPARATUS_REANALYZE_MAX_ITERATIONS` 既定1）で
  1リクエスト複数 LLM コール。guidance（hint_text/focus_bbox/unresolved_item_ids）の
  検証は `_normalize_guidance` に一元化（GF1〜GF7）。
- コスト: CostGate（1 figure × 1 user で session=3）+ `APPARATUS_MAX_CALLS_PER_DAY`。
  **同じ env var をバッチパイプライン側は DB 集計（`orchestrator.py:2421-2430`）という
  別実装で守っており合算保証がない**。
- 監査 entity は `figure_presentation`（deliberation ではない）。生成注釈は
  `session_id=None` — **同じモーダル内の対話セッションと接続しない**。
- 失敗は 422/429 で丸ごと失敗（W層対話の「本文だけ縮退」と対照的）。

---

## 4. 非対話型・提案型の機構要点

- **共通骨格**: tension / structure_anchor / reconstruction / doubt.scope_candidates /
  doubt.assumption_mining / deliberation.standardization の **6系統**が
  `core/llm_worker/`（BaseJSONLLMClient + run_with_repair + CostGate）をフル利用。
  同期パスは非LLM（prefilter / DIFF / ルール評価）+ 非同期 LLM バッチの二段構成で
  P6 を守る。
- **candidate → confirm/dismiss**: LLM 出力は常に candidate、本人/教員の確定操作
  だけが正本（P1）。dismiss は状態遷移で保持（P4）。`theory_review_events` 監査。
- **G層 次にやること**: 完全非LLM・状態導出・ポーリング禁止を明文化
  （`admin-next-steps.js:12-14`）。
- **通知インボックス**: 決定論 fan-out（非LLM）。ただし
  **フロントは 60秒 `setInterval` でポーリング**（`versioning.js:297`）— G層の
  「ポーリング禁止」と対照的（他者行動由来のため意図的な差と解釈できるが、
  方針文書上の整理はない）。
- **anchor_confirm（経路C）**: 学習チャット応答に非LLM 同期判定で1タップ選択肢を
  添付（`learning.py:2076-2089`）。提示回数は `ANCHOR_CONFIRM_MAX_PER_SESSION` で抑制。

---

## 5. 横断比較マトリクス

### 5-1. チャット型4者

| 観点 | A1 学習チャット | A2 Admin Copilot | A3 コースビルダー | A4 W層対話 |
|---|---|---|---|---|
| 履歴の正本 | `learning_chat_history`（UPSERT 上書き） | なし（クライアントのみ） | `course_builder_sessions` | `deliberation_sessions`（追記のみ） |
| LLM へ渡す履歴 | 全件・無制限 | 直近10件送信→8件/500字整形（2026-07-20 修正） | 全件・無制限 | サーバ全件（8コール上限で暗黙抑制） |
| セッション復元 | トピック単位で自動 | なし | 手動選択 | なし（v1） |
| 応答生成 | LLM 自由文 | KB + テンプレ（LLM は intent 分類のみ） | LLM 自由文 + 区切り JSON | LLM structured（reply + 注釈候補） |
| repair | なし | なし（heuristic 縮退） | なし | 意図的不使用（degraded 縮退） |
| コスト上限 | **なし** | 20/日（独自 dict） | **なし** | 8/session・40/日（CostGate） |
| モデル | 暗黙 `LLM_ANALYSIS_MODEL` + tier 混在 | `ASSISTANT_LLM_MODEL`（resolve_model 再実装） | 暗黙 `LLM_ANALYSIS_MODEL` | `DELIBERATION_LLM_MODEL`（resolve_model） |
| 失敗時 | 本体 500 即死 / 周辺は縮退（非対称） | heuristic 縮退 | 500 即死 | degraded 固定文で継続 |
| 会話本文の監査 | なし | なし | なし | なし |
| llm_worker 利用 | なし | なし | なし | CostGate + resolve_model のみ |

### 5-2. コスト上限ゲートの実装並存（4通り + 無制限）

| 実装 | 利用箇所 |
|---|---|
| `core/llm_worker/cost_gate.py` CostGate（in-memory） | worker 6系統・W層対話・figure_reanalysis・contextual_explanation |
| 独自 dict（`_reserve_llm_quota`） | Admin Copilot（`admin_assistant.py:92,120-134`） |
| 独自 DB 集計 | Field Atlas assist（`atlas.py:578-592`）、apparatus バッチ（`orchestrator.py:2421-2430`） |
| **上限なし** | **学習チャット本体・コースビルダー・原稿スタジオ rewrite/generate** |

- CostGate は in-memory・プロセスローカル（マルチワーカーで実効上限が倍増、
  再起動でリセット）。
- `APPARATUS_MAX_CALLS_PER_DAY` は CostGate と DB 集計の**二重実装**で合算保証なし。

### 5-3. モデル解決

- 共通関数 `resolve_model(key)`（`llm_worker/client.py:20`）: env var 空なら fast へ。
- **再実装が2箇所**: Admin Copilot `_assistant_model()`、Field Atlas
  （`atlas_generator.py:370`。こちらは fast でなく analysis へフォールバックする意図的差分あり）。
- 学習チャット本体・コースビルダーは専用 env var 自体がなく
  `LLM_ANALYSIS_MODEL` に暗黙依存。

### 5-4. usage_context（U層計測）

- feature 語彙の正本は `core/llm_usage/schema.py:23-88`。チャット系は
  `learning:chat(_casual)` / `admin:assistant` / `admin:course_builder` /
  `admin:lecture_rewrite` / `deliberation:chat|vision` 等でカバー。
- **欠落**: `check_topic_understanding`（確認問題採点、`learning.py:1344-1400`）の
  `generate_text` が未ラップ → `unattributed` に漏れる。

---

## 6. 共通化すべきところ（推奨順）

1. **コスト上限ゲートの CostGate 一本化 + 無制限3経路への導入**
   最も高頻度の経路（全学生が使う学習チャット、ハンズフリー音声は VAD で自動連投）が
   唯一無制限という逆転状態。Admin Copilot の独自 dict・atlas の DB 集計も
   CostGate へ寄せる。あわせて CostGate 自体のマルチプロセス弱点
   （in-memory）を解消するか、許容するなら明文化する。
2. **会話履歴ウィンドウ化の共通ユーティリティ**
   「直近Nターン・M文字・role 検証・現在発話の重複除去」は Admin Copilot 用に実装した
   `intent.py::_normalize_history` と同型の処理が全チャットに必要。
   学習チャット（全件）・コースビルダー（全件）は会話が伸びるほどコスト・レイテンシ・
   コンテキスト超過が線形悪化。`core/llm_worker/` か `core/llm.py` 付近に正本を置き、
   4者から使う。
3. **モデル解決の `resolve_model()` 統一**
   `_assistant_model()` は機械的に置換可能。学習チャット本体・コースビルダーにも
   専用 env var（例 `LEARNING_CHAT_LLM_MODEL` / `COURSE_BUILDER_LLM_MODEL`）+
   resolve_model を導入すると暗黙依存が消える。
4. **縮退規約の統一**
   「メイン応答失敗 = 500 即死」（A1/A3）と「degraded 固定文で会話継続」（A4）と
   「空配列で 200」（B2）が並存。チャット型は A4 方式（会話は死なせない）に寄せる。
   B2 は「0件」と「失敗」を応答で区別できるようにする。
5. **candidate → confirm/dismiss + `theory_review_events` 監査の語彙維持**
   非対話型で確立し W層も採用済み。チャット型が「確定を伴う提案」を返すときの
   共通規約として徹底する（新機能でもこのパターンを使う）。
6. **usage_context の徹底**（`check_topic_understanding` の未ラップ解消）。

## 7. 意図的に分けたままにすべきところ

1. **同期チャット vs 非同期 worker の二段構成**: 非対話型の「同期は非LLM・LLM は
   非同期バッチ」（P6）はチャットに適用不能。W層が run_with_repair を使わない判断は
   正しい。共通化対象は「ゲート・モデル解決・履歴整形」であって repair ループではない。
2. **権限モデル**: 学習者系は本人のみ・k-匿名・評価利用禁止（P1/P3）、教員系は
   capability registry / document 権限。統一すべきでない非対称。
3. **コンテキスト注入の中身**: RAG / capability カタログ / 要素 grounding /
   教材コンテキストはドメイン固有。注入「機構」（初回のみ注入 vs 毎ターン等）は
   揃える余地があるが、内容は分けたまま。
4. **監査の非対称**: 教員の状態変更は監査、学習者の会話は監査しない（監視にしない）は
   思想的判断として維持。ただし §8 の「会話本文の不変ログ不在」は別軸の論点。
5. **コスト上限 env var の名前空間**: ドメイン別（`TENSION_*` 等）は llm_worker の
   設計方針どおり各層の責務として残す（ゲート実装だけを共有）。
6. **ポーリング方針**: G層（自己状態由来・禁止）と通知インボックス（他者行動由来・
   60秒間隔）の差は妥当だが、設計文書に「どちらの型か」の判断基準を明文化すると良い。

## 8. 足りない機能・発見された穴

### セキュリティ / 権限

- 🔴 **原稿スタジオ rewrite・手動保存にコース所有権チェックがない**
  （`scripts.py:845` / `scripts.py:615` — `_require_teacher` のみ。chunk_id 直指定で
  他教員のチャンクを書き換え可能。同ファイルの settings 系とは権限モデル不一致）。

### 信頼性

- 🔴 **コースビルダーの `save_cb_session` がサイレント失敗**（`services.py:4306-4308`
  — DB 書き込み失敗でもチャット API は 200。再読込で履歴消失に見える）。
- 🟡 CostGate の in-memory・プロセスローカル問題（複数ワーカーで上限が実質倍増）。
- 🟡 `APPARATUS_MAX_CALLS_PER_DAY` の二重ガード（CostGate と DB 集計の合算保証なし）。

### UX / 機能

- 🟡 **casual モードのテキスト UI が存在しない**（サーバは `intent_mode="casual"` を
  完全サポート、入口がハンズフリー音声のみ。`app.js:3155`）。
- 🟡 Admin Copilot の画面コンテキストが lecture-studio 以外「タブ名のみ」、
  `capabilities_for_screen` 未配線（`capabilities.py:594` 定義のみ）。
- 🟡 Admin Copilot の応答文がテンプレのみ（履歴修正は intent ルーティングに効くが、
  応答が文脈を受けた自然文にはならない。踏み込むなら応答生成側の設計変更が必要）。
- 🟡 W層: figure 対話由来の meaning/interpretation/decomposition 注釈が
  **黙って破棄**される（`annotations.py:188-199`、LLM にも教員にも制約が伝わらない）。
  図再解析が対話セッションと非接続（`session_id=None`）。コスト上限の残数が
  ユーザーに見えない（8回/3回の別上限が同一モーダルに並存）。
- 🟡 誤解検出がキーワード一致（「訂正」等、`learning.py:2018`）依存で、
  システムプロンプトの「『訂正：』という冷たい表現は避け」（`learning.py:731`）と矛盾。
- 🟡 学習チャットの `selection_text` が anchor 記録専用で回答生成プロンプトに
  入らない（選択箇所を踏まえた回答にならない）。

### 説明責任

- 🟡 **チャット4者とも会話本文の不変ログがない**（`learning_chat_history` は上書き・
  削除で正本が消える。`interest_traces` は質問側のみ）。P3「監視にしない」との
  緊張関係があるため、導入するなら学習者系は本人同意 / インシデント限定等の
  設計判断が前提。

### デッドコード

- ⚪ レガシー `chat_sessions` / `chat_messages`（`db/init.sql:180-199`、
  `core/models.py:261,274` — どこからも参照なし）。
- ⚪ `core/chat.py::search_chunks`（学習チャットは `services.search_chunks_with_metadata`
  を使用しており未 import）。
- ⚪ `theory_components.py:353-380` の `_llm_retry_policy` / `_is_resource_exhausted` /
  `_backoff_seconds`（定義のみ・呼び出しゼロ — リトライ機構の配線忘れの可能性）。

---

## 9. 総括

非同期 worker 側は 2026-07 の整理（consolidation_survey）で `core/llm_worker/` に
きれいに集約済みなのに対し、**チャット型4者は「履歴・コスト上限・モデル解決・縮退」の
4点がすべてバラバラ**であり、次の整理対象として最も効果が大きい。

推奨着手順:

1. 無制限3経路（学習チャット本体・コースビルダー・rewrite）へのコスト上限導入
   （CostGate 一本化と同時に）
2. 原稿スタジオ rewrite / save の権限ゲート追加（🔴）
3. `save_cb_session` のサイレント失敗解消（🔴）
4. 会話履歴ウィンドウ化の共通ユーティリティ新設と4者への適用
5. モデル解決の `resolve_model()` 統一 + 専用 env var 追加
6. 縮退規約の統一（メイン応答は degraded 縮退へ）
