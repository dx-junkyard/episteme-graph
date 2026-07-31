# 教材図スタジオ（Teaching Figure Studio）設計書

**ステータス: 設計（未実装）**
作成: 2026-07-31（Fable 5 指揮 / Opus 5・Sonnet 5 調査チームによる現状調査に基づく。
Opus 5 による反証レビュー1巡を反映済み — CRITICAL 2 / MAJOR 6 / MINOR 14 を §12 の裁定と
本文修正で解消）
migration: **063** を想定（062 = discuss_opening_explanations まで消費済み）

---

## 0. 目的と要求

話（テキスト）や数式だけではわかりづらい箇所——理解のギャップが生じやすい箇所——に、
説明図（概念図・プロセス図・構造図・模式グラフ等）を挿入することで学習者の理解を滑らかにする。

要求される機能:

| # | 要求 | 本設計での対応 |
|---|---|---|
| R1 | わかりづらい箇所・ギャップが生じやすい箇所の検出 | §5 ギャップ検出（学習者信号の再利用 + LLM 静的解析） |
| R2 | どのような図を入れると理解が促進されるかのサジェスト | §5 図タイプ語彙付きの候補提案（candidate-only） |
| R3 | 教員が指定した箇所に図を入れる機能 | §7 カーソル位置挿入（既存 `![[figure:id]]` 記法へ相乗り） |
| R4 | 図の描画をインタラクティブなやりとりで結果を見ながら調整する UI | §6 図スタジオモーダル（対話 → SVG 差分修正 → 即時プレビュー） |
| R5 | 作成した図を教材に取り込む | §4 採用保存（sanitize → MinIO → `![[figure:id]]` 挿入 → 既存配信経路） |

## 0.1 現状調査の要点（設計の前提事実）

- **図の新規生成経路は現状ゼロ**。画像生成 API（gpt-image-1 / dall-e）・SVG 動的生成・
  mermaid 等の描画ライブラリはリポジトリに一切存在しない。図はすべて PDF からの抽出
  （`extraction_method ∈ {embedded, region_render}`）のみ。
- **`![[figure:id]]` の埋め込み解決・学習者配信は既に完備**。
  `core/lecture.py::resolve_figure_embeds`（→ `[[FIGURE_N]]`）、
  `routes/lecture.py::_load_course_figures_by_id`（figure_id → `{caption, image_url, document_id}`）、
  `app.js::renderMaterialChunk` + `hydrateMaterialFigures`（blob fetch。Authorization 必須のため
  `<img src>` 直指定不可）、学習者向け画像配信
  `GET /api/learning/courses/{course_id}/figures/{figure_id}/image`（受講ゲート + document 帰属 +
  本文参照の 3 条件 AND・fail-closed）。**この資産に相乗りするのが最小摩擦**。
- **スライド分割は図と両立済み**。`_display_length` は `[[FIGURE_N]]` も未解決
  `![[figure:...]]` も一律 200 字換算（figures_by_id の有無でページ境界がズレない意図的設計）。
  読み上げ原稿からは `strip_figure_embeds` が図を常に除去（図は読み上げない）。
- **教材本文の編集 UI**（原稿スタジオ course_topic スコープ）はカーソル位置挿入の既存
  ユーティリティ `lsInsertSlideMarkerIntoTextarea`（`admin-lecture-studio.js:6644`）と
  最終フォーカス記憶 `courseSlideLastFocus` を持つ。「ここに図を入れる」はこの仕組みを流用できる。
- **「わかりづらさ」の既存信号は学習者行動由来の事後集計のみ**:
  claim 単位つまづきサマリー（4 軸・k-匿名）、structure_anchor の doubt_type 集約、
  naive signals（anchor 単位 k-匿名レンジ）。教材テキスト自体の静的解析資産は無い（新規実装）。
- **対話調整 UI の前例が 2 つある**: atlas-assist-panel（interpret → propose →
  サーバ検証済みプレビュー → 教員の明示適用、409 楽観ロック、429 事実文）と、
  deliberation.js の都度生成 2 ペインモーダル（`generate_conversation_turn` による
  マルチターン + structured output、モデルチップ、degraded 縮退）。
- **原稿スタジオプレビューと学習画面の図解決に非対称がある**（既知ギャップ）:
  学習画面はコース source document の全抽出図を解決できるが、studio プレビューは
  `evidence_links` 由来の図しか解決できない（`document_id` が引けない）。本設計の
  Phase 0 でこの非対称も解消する。

---

## 1. 不変条項（FG1〜FG9）

既存レイヤー群（W層・L層・R層等）の設計文化を継承する。

- **FG1 A層非改変**: `document_figures` / 解析パイプラインのテーブル・コードに列を足さない。
  生成図は新テーブル `course_teaching_figures` に積む。`src/episteme_graph/agents/` は読みもしない
  （本機能はコース教材レイヤーで完結する）。
- **FG2 確定は人間**: AI のギャップ候補・図タイプ提案・生成 SVG はすべて candidate / draft。
  学習者に配信されるのは、教員が明示的に「採用して挿入」し、かつトピックドラフトを
  **保存**した図のみ（二重ゲート）。自動挿入はしない。
- **FG3 SVG-first・サニタイズ必須**: v1 の生成図は **SVG のみ**（ラスター画像生成 API は
  使わない）。理由: ①対話による差分修正が成立する（コードなので）②日本語・数式ラベルが
  ブラウザレンダで正確 ③既存 blob → `<img>` 表示経路に乗る（`<img>` コンテキストでは
  SVG 内 script は実行されないが、それに依存せず）**サーバ側サニタイザを唯一の入口**とする
  （詳細 §4.1: lxml 安全パーサ + 許可リスト方式 + DOCTYPE/ENTITY 無条件拒否）。
  サニタイズを通らない SVG は保存しない（除去ではなく 422 の fail-closed）。
  配信レスポンスには `X-Content-Type-Options: nosniff` +
  `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox` を付ける
  （blob/直開きでトップレベル文書化された場合の第3防御。§7.2）。
- **FG4 学習者配信は fail-closed**: 生成図の配信は「受講ゲート ∧ 図の course_id 一致 ∧
  トピック本文が実際に参照（既存 `_course_references_figure` と同型）∧ `status='adopted'`」の
  4 条件 AND。draft / retired は学習者に出ない。
- **FG5 データを捏造しない**: `data_plot`（グラフ）系の図は**定性的な模式図に限定**する
  （軸ラベル + 概形 + 注目点のみ）。実測値・具体的な数値目盛りの捏造をプロンプトで禁止し、
  caption に「模式図」表記をサーバ側で強制付与する。教材の根拠（claim / equation）に無い
  関係を図に描かせない（プロンプト制約 + 教員レビューが最終ゲート）。
- **FG6 同期パスに重い処理を入れない**: 生成・提案はすべて教員の明示操作による単発
  LLM コール（rewrite と同型）。学習チャット・受講表示の同期パスには一切 LLM を足さない。
  CostGate（day-only）で上限管理し、超過は 429 + 事実文（数値を出さない）。
- **FG7 情報を落とさない**: 図は削除せず `draft → adopted → retired` の状態遷移。
  ギャップ候補の却下は `dismissed` 遷移で保持。SVG の改訂は旧版を `revisions` に append
  （上限あり・古い順に間引く場合は正直にその旨を記録）。行削除 API は作らない。
- **FG8 数値を見せない・学習者データを漏らさない**: 提案の confidence は段階ラベルのみ。
  ギャップ検出の学習者信号は既存の k-匿名集約（`core/privacy.py` 正本、k=3・レンジ表示）の
  **読み出しだけ**を使い、個人単位の行には触れない。**外部 LLM に渡してよいのも k-匿名
  通過後のレンジ・段階ラベルのみ**（生値・個人行・逐語質問文は渡さない。学習者由来
  データの LLM への egress はこの機能が最初の事例になるため明文化する）。
- **FG9 既存の埋め込み・配信規約に相乗りし、並行実装を作らない**: 記法は既存の
  `![[figure:id]]` をそのまま使う（新記法を作らない）。解決は
  `_load_course_figures_by_id` への合流、レンダは `renderMaterialChunk` 無変更、
  スライド分割は `_display_length` の 200 字換算のまま。分割ロジック・解決ロジックを
  クライアントに再実装しない（既存開発ルールの継承）。

---

## 2. 全体像

```
【ギャップ検出】(R1/R2)
  学習者信号（既存 k-匿名集約の読み出し）      LLM 静的解析（明示操作・単発）
  ・claim つまづき 4軸                        ・トピック本文 + 数式/claim リスト
  ・anchor doubt_type 集約                    ・つまづき集約の要約を入力に添付
        └──────────────┬──────────────┘
                       ▼
        figure_suggestions（candidate、図タイプ + 対象箇所 + 理由）
                       │  原稿スタジオ右ペイン「図の提案」
                       ▼
【図スタジオ】(R4)  ── 教員がカーソル位置を決めて起動 (R3)
  2ペインモーダル: 左 = SVGプレビュー / 右 = 対話チャット
  「もっと簡略に」「ラベルを日本語に」→ LLM が SVG を差分修正 → 即プレビュー
                       │ 教員「採用して挿入」
                       ▼
【取り込み】(R5)
  sanitize → MinIO(figure-images) + course_teaching_figures 行(draft→adopted)
  → カーソル位置に ![[figure:<id>]] 挿入 → トピック保存（既存 PUT）
                       ▼
【配信】: 既存経路に合流（figures_by_id マージ → [[FIGURE_N]] → renderMaterialChunk
  → hydrateMaterialFigures → 学習者向け画像配信 API（4条件 fail-closed））
```

---

## 3. DB（migration 063）

### 3.1 `course_teaching_figures` — 生成図の正本

```sql
CREATE TABLE IF NOT EXISTS course_teaching_figures (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id       TEXT NOT NULL,                -- learning_courses.id（TEXT 正規化、FK なし=既存慣例）
    topic_id        TEXT,                          -- 作成時の対象トピック（provenance。移動可のため制約にしない）
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    title           TEXT NOT NULL DEFAULT '',
    caption         TEXT NOT NULL DEFAULT '',      -- 学習者に見えるキャプション
    figure_kind     TEXT NOT NULL DEFAULT 'concept_map'
                    CHECK (figure_kind IN ('concept_map','process_flow','structure_diagram',
                                           'comparison','timeline','coordinate','data_plot_schematic',
                                           'other')),
    svg_source      TEXT NOT NULL,                 -- サニタイズ済み SVG（正本）
    minio_key       TEXT NOT NULL,                 -- figure-images バケット内キー（配信スナップショット）
    content_type    TEXT NOT NULL DEFAULT 'image/svg+xml',
    status          TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','adopted','retired')),
    source_suggestion_id UUID,                     -- 提案起点の場合の由来（FK なし・provenance のみ）
    revisions       JSONB NOT NULL DEFAULT '[]',   -- [{svg_source, caption, updated_at, updated_by}] 旧版
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_teaching_figures_course
    ON course_teaching_figures (course_id, status);
```

- `svg_source` が正本、MinIO 側は配信スナップショット（更新時は再アップロードで同期。
  ずれても正本は DB）。MinIO キーは `teaching/{course_id}/{id}.svg`（既存の抽出図
  `figures/{document_id}/{figure_id}.png` と prefix で衝突させない）。
- `status` の意味: `draft` = 保存済みだが未採用（教員のストック。挿入タブに出る・学習者非配信）/
  `adopted` = 配信対象 / `retired` = 回収（FG7、行削除しない）。保存 API の `adopt` フラグ
  （既定 true）で draft 保存も可能（§8）。
- **`document_figures` とは独立**（FG1）。ID は両方 UUID なので `figures_by_id` マップ上で
  衝突しない。
- 削除 API は作らない。孤児掃除はコース物理削除の **3 経路すべて**に明示 DELETE を同乗させる:
  ①`services.py::delete_course_data`（即時削除）②`core/versioning/deletion.py::_purge_course`
  （V層スイーパ）③`_purge_document` 内の独立したコース削除ループ。MinIO オブジェクト削除の
  ため **`StorageManager.remove_object(bucket, key)` を新設**する（現状 storage.py に削除
  メソッドは存在しない。best-effort・失敗は WARN ログで DB 削除は止めない）。

### 3.2 `teaching_figure_suggestions` — ギャップ候補（candidate 層）

```sql
CREATE TABLE IF NOT EXISTS teaching_figure_suggestions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id       TEXT NOT NULL,
    topic_id        TEXT NOT NULL,
    anchor_excerpt  TEXT NOT NULL DEFAULT '',      -- 本文からの逐語引用（挿入位置の手がかり。捏造ガード）
    gap_reason      TEXT NOT NULL DEFAULT '',      -- 「なぜここが分かりづらいか」の事実文
    signal_basis    TEXT NOT NULL DEFAULT 'text_analysis'
                    CHECK (signal_basis IN ('text_analysis','learner_signals','both')),
    figure_kind     TEXT NOT NULL,                 -- 3.1 と同語彙
    figure_brief    TEXT NOT NULL DEFAULT '',      -- 「何をどう描くか」の一文
    confidence      REAL,                          -- DB のみ。API は段階ラベル（FG8）
    status          TEXT NOT NULL DEFAULT 'candidate'
                    CHECK (status IN ('candidate','accepted','dismissed','superseded')),
    created_by      UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_teaching_figure_suggestions_topic
    ON teaching_figure_suggestions (course_id, topic_id, status);
```

- 再生成時は既存 candidate を `superseded` に遷移（#496 / discuss_opening と同じ原則。
  accepted / dismissed は不変）。
- `anchor_excerpt` は本文の**逐語部分文字列であることを validator で強制**
  （discuss_opening の evidence_quote verbatim 検査と同型の捏造ガード）。

### 3.3 監査

`backend/core/schema.py` に `AUDIT_ENTITY_TEACHING_FIGURE = "teaching_figure"` を追加し
`AUDIT_ENTITY_TYPES` に登録（33 語彙目）。記帳イベント: 図の採用（draft→adopted）/
retire / 提案の accept / dismiss / 提案生成の実行。`services.record_review_event` に委譲。

---

## 4. core 実装（`backend/core/teaching_figures/`、FastAPI 非 import）

tension / structure_anchor と同型の独立モジュール構成:

```
backend/core/teaching_figures/
  __init__.py
  schema.py        → 語彙の正本（FIGURE_KINDS + 表示ラベル、status 語彙、段階ラベル変換）
  sanitizer.py     → SVG サニタイザ（唯一の保存入口。§4.1）
  prompt.py        → 生成・修正・提案のプロンプト定義
  generator.py     → 対話生成（generate_conversation_turn + structured output。§4.2）
  suggest.py       → ギャップ検出 + 図タイプ提案（§5）
  store.py         → CRUD + 状態遷移 + MinIO 同期 + revisions append
  signals.py       → 既存 k-匿名集約（stumble / naive_signal）の読み出しアダプタ（読むだけ）
```

### 4.1 sanitizer.py

- `sanitize_svg(source: str) -> SanitizedSvg`（拒否は例外 `SvgRejected(reason)`）。
- **パーサは既存依存の lxml を使う**（`lxml>=5.0` は requirements 済み。defusedxml は
  依存に無く追加しない）: `lxml.etree.XMLParser(resolve_entities=False, load_dtd=False,
  no_network=True, huge_tree=False)`。さらに**パース前に `<!DOCTYPE` / `<!ENTITY` を含む
  入力を無条件 422**（billion laughs / XXE の入口を二重に塞ぐ）。stdlib `ElementTree` への
  フォールバックは**書かない**（エンティティ攻撃に脆弱なため。lxml が無い環境は起動時に
  気づかせる）。
- ルートが `svg` / `viewBox` 必須 / 要素・属性の**許可リスト方式**
  （`svg,g,path,rect,circle,ellipse,line,polyline,polygon,text,tspan,
  defs,marker,title,desc,linearGradient,radialGradient,stop,clipPath,use(#内部参照のみ)`）。
  属性も許可リスト（幾何・表示属性 + `style` **属性**は `url(`・`expression(`・`@import` を
  含む値を拒否した上で許可。`<style>` **要素**は v1 では不許可 — 属性で足りる）。
- `<script>`・`<foreignObject>`・`<image>`・on* 属性・外部 URL（`href`/`xlink:href` の
  非 `#` 値）は**拒否**（除去ではなく保存自体を 422 にする。LLM 出力なら修正リトライの
  契機になる）。
- ルート `<svg>` に `width`/`height` が無ければ viewBox から**正規化付与**する
  （`<img>` の intrinsic size 欠落で 300px 既定レンダになるのを防ぐ。既存 CSS
  `.material-figure-img { max-width:100%; height:auto }` はそのまま効く）。
- 出力サイズ上限（既定 200KB、`TEACHING_FIGURE_MAX_SVG_BYTES`）。
- サニタイズ済み文字列の決定論 sha256 を返し、**クライアント提出原文の sha256 と併せて**
  監査 metadata に載せる（保存 API は教員クライアント由来の svg_source を受けるため、
  「AI 生成物である」ことは検証できない。FG5 の最終担保が教員レビューである事実を
  ここに明記する）。

### 4.2 generator.py — 対話による生成・調整

- `run_figure_turn(*, history, user_instruction, current_svg, grounding, model, user_id, course_id) -> FigureTurnResult`
- LLM は `core/llm.py::generate_conversation_turn`（マルチターン + structured output。
  OpenAI 経路）を使う。出力スキーマ:
  ```python
  class _FigureTurnOutput(BaseModel):
      reply: str = ""               # 教員への一言（何をどう変えたか）
      svg_source: str = ""          # 完全な SVG（空 = 変更なしターン）
      title: str = ""
      caption: str = ""
      figure_kind: str = ""
  ```
  （`str | None` にしない — `client.beta.chat.completions.parse` の strict schema では
  全フィールドが required 化されるため、deliberation の `_AnnotationCandidateOut` と同じ
  「非 nullable + 空既定」慣例に従う。）
- **毎ターン完全な SVG を出させる**（差分パッチ形式にしない。パッチ適用の失敗モードを
  避け、サニタイザを毎回全量通せる）。`current_svg` は grounding としてプロンプトに注入。
- grounding にはトピック本文（該当箇所の抜粋）・関係する数式（`content_blocks` の
  equations）・claim テキスト・提案の `figure_brief`（提案起点の場合）を入れる。
  **図に描いてよいのは grounding に現れる関係のみ**とプロンプトで制約（FG5）。
- 履歴は `core/llm_worker/history.py::window_history(history, max_messages=12,
  max_chars=6000, head_keep=1)`（先頭 = grounding 注入ターンを保護。deliberation と同型）。
  会話履歴の永続化はしない（ブラウザ内のみ。atlas-assist 前例）。
- LLM 失敗・サニタイズ 2 回連続失敗は degraded 事実文で返す（500 にしない。
  チャット型の共通規約に従う）。
- サニタイズは**このターン応答の時点で実行**し、失敗時は同一コール内で 1 回だけ
  修復リトライ（エラー理由をフィードバック）。それでも失敗なら svg 無しの reply のみ返す
  （プレビューは前回版のまま）。

### 4.3 モデル・コスト・計測（M層 / U層 / CostGate）

- U層 feature 追加（`core/llm_usage/schema.py::KNOWN_FEATURES`）:
  `admin:figure_studio`（対話生成）/ `admin:figure_suggest`（ギャップ提案）。
- M層 scene 追加（実装チェックリスト 4 点）: ① `SCENE_FIGURE_STUDIO = "figure_studio"`
  定数 + `_SCENE_LABELS` に「教材図スタジオ」 ② `scene_for_feature` の分岐 2 行
  （両 feature → 同一 scene）③ `llm_policy.py` の `__all__` に定数追加
  ④ vision 不要（SVG はテキスト生成）なので `_VISION_REQUIRED_SCENE_KEYS` には入れない。
- **`core/config.py` の Settings に Field を追加**（`_FEATURE_ENV_SETTINGS` の値は Settings
  属性名なので必須）: `figure_studio_llm_model` / `figure_studio_max_calls_per_day`（既定 60。
  1 図あたり数ターンの往復を想定）/ `figure_suggest_max_calls_per_day`（既定 20）/
  `teaching_figure_max_svg_bytes`（既定 200_000）/ `teaching_figure_max_suggestions`（既定 4）。
  `.env.example` にも追記（開発ルール 1）。
- CostGate（day-only、`_shared.py` の rewrite ゲートと同型）。超過 429 + 事実文・数値非表示。
- 既定モデルは fast tier（`_FEATURE_ENV_SETTINGS` に
  `("figure_studio_llm_model", "fast")` を両 feature 分登録。同一設定キーの共有は
  atlas / deliberation に前例あり）。

---

## 5. ギャップ検出とサジェスト（R1 / R2）

### 5.1 入力（2 系統の合成）

**(a) 学習者信号（既存資産の読み出しのみ・新規収集なし）** — `signals.py`:
- claim 単位つまづきサマリー（`core/reconstruction/stumble.py::get_stumble_summary`）の
  うち `has_data=true` の claim（4 軸の段階ラベルごと）。
- naive signals（`core/doubt/naive_signal.py::aggregate_naive_signals`）の anchor 単位
  doubt_type 集計（k≥3 セルのみ返る既存仕様のまま）。
- これらを「トピックに紐づく claim / anchor」でフィルタ（`evidence_links` kind='claim' の
  交差。原稿スタジオの `lsStumbleClaimIdsForTopic` と同じ対応規則をサーバ側で実装）。

**(b) LLM 静的解析（新規・明示操作の単発）** — `suggest.py`:
- 入力: トピック本文（student_material）+ 数式リスト + claim テキスト +（あれば）(a) の
  集約要約（「この claim は誤り率:高・質問:6-10 人」という事実文。生値は渡らない）。
- 1 コールの structured output で最大 `TEACHING_FIGURE_MAX_SUGGESTIONS`（既定 4）件:
  `{anchor_excerpt(逐語), gap_reason, figure_kind, figure_brief, confidence}`。
- validator: `anchor_excerpt` の verbatim 包含検査（不一致は repair 1 回 → 失敗行は破棄し
  正直に `dropped` 件数を返す）。`figure_kind` は語彙外を拒否。
- 学習者信号ゼロでも本文のみで動く（`signal_basis='text_analysis'` に縮退）。

### 5.2 図タイプ語彙（domain-neutral、`schema.py` 正本）

| figure_kind | 表示 | 想定ギャップ |
|---|---|---|
| `concept_map` | 概念関係図 | 用語間の関係が文章で追いづらい |
| `process_flow` | プロセス図 | 手順・因果の連鎖が長い |
| `structure_diagram` | 構造・構成図 | 系の構成要素と接続が想像しづらい |
| `comparison` | 対比図 | 2 つ以上の場合分け・比較が入り組む |
| `timeline` | 時系列図 | 順序・発展段階の把握 |
| `coordinate` | 座標・幾何図 | 空間配置・ベクトル・角度 |
| `data_plot_schematic` | 模式グラフ | 傾向・依存関係の直観（**実データ禁止**, FG5） |
| `other` | その他 | 上記に収まらない提案（理由必須） |

※ この語彙は**生成図の意図分類**であり、既存の `FIGURE_MODES`
（`figure_modes.py`: 抽出図の提示モード `functional_diagram / data_plot / ...`）とは
別レイヤーの別体系（意図的に語を重ねない）。生成図は `figure_presentation` /
W層「深く検討」/ #496 モード分類の**対象外**（非スコープ、§9 Phase 3 参照）。

### 5.3 提案の提示（フロント）

原稿スタジオ右ペインのトグルを 3 値に拡張: `根拠リンク | つまづき | 図の提案`
（`lsState.rightPaneMode` に `"figures"` を追加。既存 2 値の挙動は不変）。

- 「図の提案」ペイン: 保存済み candidate のカード一覧 + `[提案を生成/再生成]` ボタン
  （再生成は既存 candidate を superseded にする旨の事実文 confirm）。
- 提案カード = `gap_reason`（事実文）+ figure_kind ラベル + `figure_brief` +
  `anchor_excerpt`（クリックで左ドラフトの該当箇所へスクロール —
  既存 `lsFocusDraftEvidence` と同型のテキスト検索ジャンプ）+ 段階ラベル
  + アクション `[この図を作る]`（→ §6 スタジオをプリセット起動 + accepted 遷移）
  `[却下]`（dismissed 遷移・保持）。
- つまづきトグル側のカードにも、対応する提案があれば「図で補う」導線を 1 行足す（任意）。

---

## 6. 図スタジオ（R4）— インタラクティブ生成・調整 UI

### 6.1 フロント: `admin-figure-studio.js`（新規、ES5・`window.FigureStudio`）

- deliberation.js と同じ**都度生成モーダル**パターン（`document.createElement` +
  `innerHTML` 一括構築 + `appendChild` 後にイベント配線、z-index は 9500 帯、
  背景クリック / × で閉じる、閉じたら ObjectURL revoke + 状態リセット）。
- DI 注入: `init({apiFetch, apiFetchRaw, escHtml})`。読み込み順の制約は無い
  （呼び出しはクリックハンドラ内なので、deliberation.js が admin-lecture-studio.js より
  後に読まれつつ呼ばれている既存前例と同じ）。admin.html の script 群に追加し、
  **`test_admin_help_inspect_ui_static.py` の `_ADMIN_FRONTEND_SOURCES` にも登録する**
  （登録しないと data-ui-anchor 網羅検査の対象外になり規約が空洞化する）。
- **2 ペイン構成**:
  - 左 = プレビュー: 返ってきた `svg_source` を `Blob(["<svg…"], {type:"image/svg+xml"})`
    → `URL.createObjectURL` → `<img>`。**innerHTML に SVG を直接挿入しない**
    （`<img>` コンテキストで script 不実行 + サーバサニタイズ済みの二重防御）。
    下部に title / caption の編集欄（テキストは教員が直接直せる。LLM を介さない微修正）。
  - 右 = チャット: 指示入力 + 送信、往復ログ、モデルチップ
    （`AdminLlmModels.createModelChip({sceneKey:"figure_studio", …})`）。
    「送信中…」表示、429/degraded はサーバの事実文をそのまま表示（deliberation と同型）。
- **状態はブラウザ内のみ**: `{history: [], currentSvg, title, caption, figureKind,
  insertTarget: {textareaId, selectionStart, selectionEnd}, suggestionId}`。
  リロードで消える（atlas-assist 前例。採用済みの図は DB に残るので損失は会話だけ）。
- フッター: `[採用して挿入]`（currentSvg があるときのみ活性）/ `[下書きとして保存]`
  （挿入せず `status='draft'` でストック）/ `[破棄]`。
  採用 = `POST /teaching-figures`（§8）→ 返った id で
  `![[figure:<id>]]` を記憶済みカーソル位置に挿入（`lsInsertSlideMarkerIntoTextarea` を
  汎用化した `lsInsertTextAtCursor(el, text)` を admin-lecture-studio.js に切り出し）→
  `el.dispatchEvent(new Event("input"))` で既存の `updateTopic` リスナに届ける
  （`updateTopic` / `courseSlideLastFocus` は `lsBindCourseDraftControls` のクロージャ
  ローカルなので外部から直接呼べない。挿入位置の記憶とボタン配線は同クロージャ内に置く）
  → プレビュー再描画。**トピック保存（既存の保存ボタン）までは
  学習者に出ない**旨をモーダル閉時に一行表示（FG2 の二重ゲートを教員に見せる）。

### 6.2 起動導線（R3）

1. **ドラフト編集ツールバー**（スライド区切りボタンの隣）に `[🖼 図を挿入]`:
   押下時点の `courseSlideLastFocus`（教材 textarea）の selectionStart を挿入位置として
   記憶してスタジオを開く。spoken_script 側にフォーカスがある場合はボタンを無効化
   （図は読み上げ原稿に入れても strip される — 無意味な操作を入口で塞ぐ）。
2. **「図の提案」カードの `[この図を作る]`**: `figure_brief` / `figure_kind` /
   `anchor_excerpt` をプリセットとして渡し、初回ターンを自動送信（「この brief で
   初版を描いて」）。挿入位置は `anchor_excerpt` の本文内位置を既定値にする。
3. **既存図の挿入（タブ）**: モーダル内に「既存の図から選ぶ」タブを併設し、
   コース source document の抽出図（`GET /admin/documents/{id}/figures`）+ このコースの
   採用済み生成図を一覧 → 選択で `![[figure:<id>]]` 挿入のみ行う（生成なし・LLM 0 回）。
   これが §0.1 の studio プレビュー非対称の解消と対になる（Phase 0）。

### 6.3 調整ループの UX

```
教員: 「熱平衡に達するまでの過程を3段階のプロセス図で」
 AI : SVG 初版 → 左ペインに即表示
教員: 「段階2と3の間に『境界条件の適用』を挟んで。ラベルは日本語で」
 AI : SVG 改訂版 → 差し替え表示（reply に変更点の一言）
教員: caption 欄を直接編集 → [採用して挿入]
```

- 各ターンは 1 LLM コール（FG6）。プレビューは常に「サニタイズを通過した最新版」。
- 生成に失敗したターンは前回版を保持（プレビューが壊れない）。

---

## 7. 教材への埋め込みと配信（R3 / R5）

### 7.1 記法と解決（FG9: 既存資産へ相乗り）

- 記法は既存の `![[figure:<uuid>]]`。**新記法を作らない**。
- `routes/lecture.py::_load_course_figures_by_id(course_id, course_data)` を拡張し、
  `course_teaching_figures` の `status='adopted'` 行を
  `figure_id -> {caption, image_url, document_id: None, teaching: True}` としてマージ
  （抽出図と同一マップ。UUID 衝突なし。`image_url` は §7.2 の学習者エンドポイント）。
  **マージは document 走査より前に行う** — 現実装は `_course_document_ids` が空だと
  早期 `return {}` するため、後置するとソース教材の無いコースで生成図が沈黙して
  解決不能になる。DB 例外時の `return {}` フォールバックも生成図マージ結果は保持する。
- これにより `resolve_figure_embeds` → `[[FIGURE_N]]` 化、スライド分割（200 字換算）、
  `strip_figure_embeds`（読み上げ除去）、`renderMaterialChunk` + `hydrateMaterialFigures`
  （blob 表示)、`_attach_figure_explanations`（対象外はスキップされるだけ）まで
  **既存コード無変更または最小変更**で流れる。

### 7.1b 採用時の参照登録（本設計の要石 — 消失・露出問題の一手解決）

図の参照を本文テキスト（ミュータブルな器）にだけ置くと、①AI 書き換え
（`rewrite_lecture_studio_course_topic` は本文を丸ごと再生成する）で embed が黙って消える
②retired・未解決時に学習者へ生の `figure:<uuid>` が露出する（`evidence_items` に無い図は
`renderMaterialMissingEmbed` に落ちる）、という 2 つの壊れ方をする。そこで**採用（adopted
遷移）時に、本文挿入と同時にトピックの構造フィールドへも登録する**:

1. `topic.linked_figure_ids` に figure_id を追記（既存フィールド。現状書き込み実装が無い
   「空いている枠」。学習者配信ゲート条件 3 = `_course_references_figure` は既に
   `linked_figure_ids` を受けるため、本文から embed が消えても配信ゲートは壊れない）。
2. `topic.evidence_links` に `{kind:"figure", target_id, extra:{figure_id, caption,
   teaching:true}}` を追記。これで `build_topic_evidence_items` が図を evidence_items に
   投影し、**retired / 画像未配信時は既存の「この図の画像は現在配信対象ではありません。」
   読み取りカードに正しく落ちる**（生 UUID を出さない）。studio 右ペインの根拠リンクにも
   図カードとして現れる。
3. AI 書き換えの保全: `_topic_figures_for_prompt` は evidence_links 由来なので、2. の登録に
   より rewrite プロンプトの `figures`（「発明禁止・列挙されたもののみ使用可」制約）にも
   生成図が自動的に載る。さらに `_required_figure_items` を「evidence_links ∪
   linked_figure_ids」で引くよう拡張し、rewrite 応答で embed が消えた場合は
   `_ensure_required_figures_in_material` と同じ決定論注入で末尾復元 + studio に
   「AI 書き換えで図の位置がリセットされました」の事実文を表示する。
4. これらの登録・解除は保存 API（`PUT .../course-topics/{topic_id}`）の受理フィールドに
   `linked_figure_ids` / 図種 evidence_links を**サーバ側で管理**する形にし、クライアントの
   自由書き込みにはしない（採用 API・retire API が同一トランザクションで topic 側を更新）。

### 7.2 学習者向け配信

既存 `GET /api/learning/courses/{course_id}/figures/{figure_id}/image` を拡張:
`document_figures` に無ければ `course_teaching_figures` を引く。条件（FG4）:
1. 受講ゲート（`get_accessible_course_data`）
2. `figure.course_id == course_id`
3. トピック本文が参照（既存 `_course_references_figure` をそのまま流用 — 本文の
   `find_figure_embed_ids` / `linked_figure_ids` 判定は図の出自を問わない。
   なお同関数は現状 `course_topics()`（フラット形のみ）を走査しており章ネスト形
   `chapters[].topics[]` を取りこぼす既存バグがあるため、Phase 0 で `iter_all_topics` へ
   移行する — 抽出図側もこの修正の恩恵を受ける）
4. `status = 'adopted'`
- レスポンス `media_type` は `row.get("content_type") or "image/png"`（`document_figures` に
  content_type 列は無く抽出図は常に PNG。生成図は `image/svg+xml`）。既存テスト
  `test_learner_figure_delivery.py` の PNG 固定アサーションは抽出図経路として不変。
- **SVG 配信時のレスポンスヘッダ**（FG3 第 3 防御）: `X-Content-Type-Options: nosniff` +
  `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; sandbox`。
  blob → `<img>` 経路では script は元々実行されないが、学習者が blob URL や API 応答を
  トップレベルで開いた場合にも実行コンテキストを封じる（JWT が localStorage にある
  現行構成では必須の防御）。教員向けエンドポイント（§7.3）も同一ヘッダ。

### 7.3 原稿スタジオプレビューの非対称解消（Phase 0）

- `GET /admin/courses/{course_id}/lecture-studio/course-structure` のレスポンスに
  コース単位の `figures_index`（学習画面と同じ `figure_id -> {caption, image_url(admin 経路),
  document_id, teaching}` マップ）を追加。
- `lsRenderCourseMaterialPreview` の figure 分岐を「`evidence_links` 由来 item →
  無ければ `figures_index`」の 2 段フォールバックにする。これで教員が手書き・挿入した
  任意の figure_id が studio プレビューでも画像表示される（学習画面との整合）。
- 管理画面向け生成図の画像配信は `GET /api/admin/courses/{course_id}/teaching-figures/{fid}/image`
  （`_require_teacher` + コース編集権限。draft も見える — 教員のプレビュー用）。

### 7.4 versioning（V層）との整合と retired 時の見え方

コースの発行版スナップショット（`shared_versions.snapshot`）は `learning_courses.data` を
凍結するが、図の実体は参照（figure_id）である。**採用済み図の retire は「参照が残る版」を
壊す**ため、retire 時に「この図を参照中のトピックが N 件あります」の事実文 confirm を出す。

retired 後の学習者側の見え方は §7.1b の evidence_links 登録が前提:
- 画像配信は 404（FG4 条件 4）。
- 本文側は、retire 時に本文の embed は消さない（版スナップショットを壊さない）が、
  `figures_by_id` から外れるため `resolve_figure_embeds` は原文
  `![[figure:uuid]]` のまま残す → フロントの `![[kind:id]]` 分岐が evidence_items
  （§7.1b で登録済み）を引き、**「この図の画像は現在配信対象ではありません。」の
  読み取りカード**に落ちる。evidence_links 登録が無いと `renderMaterialMissingEmbed` で
  生 UUID が露出するため、**登録は retire 後も削除しない**（FG7 とも整合）。
v1 では版ピン中の学習者への図スナップショット配信は行わない（既知の限界として明記。
document 成果物のピン凍結ブラウズ未実装と同格）。

### 7.5 保存の副作用の扱い（音声・スライド分割）

図の挿入は本文変更なので、既存挙動として次の副作用がある。隠さず UI で事実文提示する:

- **トピック音声の無効化**: `save_lecture_studio_course_topic` は変更内容を問わず当該
  トピックの `topic_lecture_audio_cache` を DELETE する。図は読み上げに入らない
  （`strip_figure_embeds`）ため理論上は温存可能だが、v1 では既存挙動を変えず、
  スタジオの採用完了時に「保存するとこのトピックの生成済み音声は作り直しになります」を
  一行表示するに留める（条件付き DELETE の最適化は v2 検討。安全側 = 消す方に倒す）。
- **自動ページ分割の境界移動**: 図 1 個 = 200 字換算のため、`===` 明示分割の無いトピックに
  図を 2〜3 個入れると `auto_paginate_slides` の境界が動き、読み上げ原稿が同数ページに
  割れない場合は spoken 空縮退（タイマー送り）になる。既存の `slide_mismatch` 警告に加え、
  スタジオ採用後にスライド枚数インジケータを即時更新し、縮退が起きる場合は
  「表示 N 枚に対し読み上げが分割できないため、このトピックの読み上げはタイマー送りに
  なります。`===` で明示分割すると解消できます」の事実文を出す。
  なお `preview-split` API は `split_slides` 直呼びで自動ページ分割を反映しない既知差が
  あるため、インジケータ用に `auto_paginate` オプションを追加する（既存呼び出しは不変）。

---

## 8. API（`backend/api/routes/teaching_figures.py`、実パス `/api/admin/...`、全て `_require_teacher` + コース編集権限）

| メソッド/パス | 役割 |
|---|---|
| `POST /courses/{course_id}/topics/{topic_id}/figure-suggestions/generate` | LLM 提案生成（単発・CostGate・既存 candidate は superseded）|
| `GET /courses/{course_id}/topics/{topic_id}/figure-suggestions` | candidate + accepted 一覧（段階ラベルのみ）|
| `POST /figure-suggestions/{sid}/dismiss` / `/accept` | 状態遷移（監査記帳）|
| `POST /courses/{course_id}/figure-studio/turn` | 対話 1 ターン。body: `{history, instruction, current_svg, topic_id, suggestion_id?, model?}` → `{reply, svg_source?, title?, caption?, figure_kind?, degraded}` |
| `POST /courses/{course_id}/teaching-figures` | 保存。body: `{svg_source, title, caption, figure_kind, topic_id, suggestion_id?, adopt: bool=true}` → sanitize → MinIO → 行作成（adopt=true なら `status='adopted'` + §7.1b の topic 側登録を同一トランザクションで実行）→ `{figure_id}` |
| `PATCH /courses/{course_id}/teaching-figures/{fid}` | caption/title 修正・svg 差し替え（revisions append）・`status` 遷移（draft→adopted / adopted↔retired。confirm はフロント責務。遷移時に §7.1b の topic 側登録も同期）|
| `GET /courses/{course_id}/teaching-figures` | このコースの生成図一覧（挿入タブ用）|
| `GET /courses/{course_id}/teaching-figures/{fid}/image` | 教員向け画像配信（draft 含む）|

- model パラメータは `llm_policy.validate_model_for_scene("figure_studio", model)` で
  fail-closed 検証（rewrite と同型の 3 行パターン）。
- turn API はステートレス（履歴はクライアント持ち・サーバで window_history）。
- エラー写像: sanitize 拒否 422（理由の事実文）/ 上限 429 / 権限 404 統一。
- ルータは `backend/api/main.py` から `prefix="/api/admin"` で**直接登録**（Tier 3-17c。
  admin.router に include しない）。コース編集権限ヘルパは lecture_studio 側 module-private の
  `_course_data_for_studio_editable` を `_shared.py` へ移して共有する。
- **権限の非対称に注意**: トピック本文の保存はコース所有者 / SYSTEM_ADMIN のみ（既存挙動）。
  よって v1 の図の保存・採用も**同じゲート**にする（editor 教員が図だけ作れて本文に反映
  できない中途半端を作らない。editor への開放はトピック保存の権限拡張と同時に検討）。

---

## 9. 実装フェーズ

**Phase 0 — 配信・解決の下地（LLM 0 回、単独で価値あり）**
migration 063 / `_load_course_figures_by_id` 合流（早期 return より前にマージ）/
学習者配信の拡張（4 条件 + content_type + SVG レスポンスヘッダ）/
`_course_references_figure` の `iter_all_topics` 移行（章ネスト取りこぼしの既存バグ修正）/
`StorageManager.remove_object` 新設 + コース削除 3 経路の孤児掃除 /
studio プレビュー非対称の解消（figures_index）/ 既存図の挿入タブ +
`lsInsertTextAtCursor` 切り出し + ツールバー `[🖼 図を挿入]`（既存図選択のみ）/
§7.1b の topic 側登録プリミティブ。
→ この時点で「教員が抽出図を任意箇所に挿入する」(R3/R5 の既存図版) が完成する。

**Phase 1 — 図スタジオ（R4/R5）**
`core/teaching_figures/`（sanitizer / generator / store）/ turn・保存 API /
`admin-figure-studio.js`（2 ペインモーダル + モデルチップ + 採用挿入）/
M層 scene・U層 feature・CostGate・監査。

**Phase 2 — ギャップ検出 + サジェスト（R1/R2）**
`suggest.py` + `signals.py` / suggestions テーブル運用 / 右ペイン第 3 トグル「図の提案」/
提案 → スタジオのプリセット起動 / （任意）G層 optional ルール
`topic.figure_gap_pending`（accepted 提案があり対応図が未挿入のトピック）。

**Phase 3（v2 候補・本設計の非スコープ）**
ラスター画像生成 API の併用 / 学習者起点の図リクエスト / 版ピン学習者への図スナップ
ショット / data_plot への実データ接続（検証済みデータソース限定で FG5 を緩和する場合は
専用設計を切る）/ mermaid 等の宣言的図 DSL / 図のアニメーション。

---

## 10. ガードレールテスト

`backend/tests/test_teaching_figures_guardrails.py`（`guardrail_helpers.py` 使用）:
- `core/teaching_figures/` が FastAPI を import しない
- sanitizer: `<script>` / on* / `<foreignObject>` / 外部 `href` / `<image>` /
  `<!DOCTYPE` / `<!ENTITY` を含む SVG が**保存 API を通らない**（除去でなく 422）。
  viewBox 欠落拒否。サイズ上限。stdlib `xml.etree` へのフォールバックが存在しない
  （lxml 安全パーサのみ）
- 配信 fail-closed: 非受講者 404 / 他コース図 404 / 本文非参照 404 / draft・retired 404
- **SVG 配信レスポンスに nosniff + CSP sandbox ヘッダが付く**（学習者・教員両エンドポイント）
- candidate-only: 提案が教員操作なしに accepted にならない / 図が保存・PATCH API 以外で
  adopted にならない
- **AI 書き換え耐性: rewrite 応答の適用後も採用済み図の参照（linked_figure_ids +
  evidence_links + 本文 embed の決定論復元）が失われない**
- **retired 図が学習者本文に生 UUID / 生マークアップを出さない**（evidence_links 経由の
  読み取りカードに落ちる）
- 行削除 API が存在しない（ルータの METHOD 検査）。コース削除 3 経路すべてに
  teaching_figures の孤児 DELETE がある
- confidence 生値が学習者・教員向け API レスポンスに現れない（段階ラベルのみ）
- `anchor_excerpt` の verbatim 検査が validator に存在する
- data_plot_schematic のプロンプトに実データ禁止の制約文が含まれる
- k-匿名は `core/privacy.py` 正本に委譲している（リテラル k=3 の再定義なし）。
  LLM への入力にレンジ・段階ラベル以外の学習者データが渡らない（suggest 入力の構造検査）

UI static: `test_figure_studio_ui_static.py`（ES5 準拠 / `innerHTML` への SVG 直挿入が
無いこと（blob+img 経由のみ）/ モデルチップ scene / 右ペイン 3 値トグルの後方互換 /
既存 `test_figure_course_flow_ui_static.py` の契約を壊していないこと）。

**管理UIの 3 点セット**（CLAUDE.md 明文規約。忘れると網羅テストが落ちる — ただし新規 js は
走査対象への登録が先）:
- `docs/manual/teacher/1x-admin-*.md` に操作要素 1 つ = 1 節（`###` + 明示 anchor、
  無効化され得る要素は理由 + 解消方法）でスタジオ・挿入ボタン・図の提案トグルを追加
- `core/help_kb/admin_ui_anchors.py::ADMIN_UI_ANCHORS` へ登録
- 各 UI 要素に `data-ui-anchor` 付与
- `test_admin_help_inspect_ui_static.py::_ADMIN_FRONTEND_SOURCES` に
  `admin-figure-studio.js` を追加（これを忘れると双方向網羅検査の対象外になる）

---

## 11. 主要な設計判断の理由（裁定記録）

1. **SVG 生成 / ラスター生成 API 不採用（v1）** — 対話調整（R4）はコードの差分修正で
   しか実用にならない。ラスターは「少し直す」ができず毎回別画像になり、テキスト・数式の
   正確性も保証できない。SVG はブラウザレンダで日本語も数式ラベルも正確、既存 blob 表示
   経路に乗る。
2. **`document_figures` に相乗りしない** — 041 系テーブルはパイプライン成果のレジストリで、
   review 系列（presentation_mode / iterative_analysis）のガードレールが密結合している。
   生成図は「コース教材の付属物」でライフサイクルが違う（FG1）。
3. **記法・解決・配信は既存 `![[figure:id]]` 資産へ合流** — 新記法や並行レンダラを作ると
   スライド分割・読み上げ除去・fail-closed 配信の再実装が必要になり、FG9 に反する。
4. **会話履歴を永続化しない** — atlas-assist の前例に従う。永続化するのは成果物
   （SVG + revisions）だけで十分。course_builder_sessions のような永続化は要望が
   出てから（YAGNI）。
5. **提案は superseded 方式** — #496 / discuss_opening の確立済み原則（再生成は candidate
   のみ置換・教員の判断済み行は不変）。
6. **図は読み上げない（v1 継続）** — `strip_figure_embeds` の既存設計を維持。図の音声解説は
   caption を読む案もあるが、スライド同期の複雑化に見合わないため非スコープ。
7. **採用時に topic 構造フィールドへも登録する（§7.1b、レビュー C1/M1 の裁定）** —
   本文テキストだけに参照を置くと「AI 書き換えで消える」「retired 時に生 UUID 露出」の
   2 つの壊れ方をする。`linked_figure_ids`（書き込み実装が無かった空き枠）+
   `evidence_links` への登録一手で、配信ゲート・evidence_items 投影・rewrite プロンプトの
   図列挙の 3 系統が同時に守られる。
8. **SVG パーサは lxml 固定・stdlib フォールバック禁止（レビュー C2 の裁定）** —
   defusedxml は依存に無く、stdlib ElementTree はエンティティ攻撃に脆弱。既存依存の
   lxml（`resolve_entities=False, load_dtd=False, no_network=True`）+ DOCTYPE/ENTITY の
   入力前拒否 + 配信ヘッダ（nosniff / CSP sandbox）の三層で守る。本機能は「外部生成
   マークアップを同一オリジンで配信する」リポジトリ初のケースであることを認識しておく。
9. **音声キャッシュの条件付き温存はしない（レビュー M3 の裁定）** — 図は読み上げに
   入らないため理論上は温存可能だが、v1 は既存の「保存 = 音声無効化」を変えず事実文の
   事前告知で対応（安全側 = 消す方に倒す。差分検知の最適化は v2）。
