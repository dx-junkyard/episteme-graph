# LLM モデル選択の設計（M層: Model Policy）

対象: 教材解析パイプライン / コース構築 / 原稿スタジオ / 受講（学習チャット・音声） /
分野の地図・前提の地図 / 要素検討ワークスペース / Admin Copilot / 学習の裏方 worker 群。

目的: **各 LLM 使用場面で使うモデルを、実際のモデル名で、ストレスなく適切なタイミングで
選べるようにする**。同時に、選択肢を増やしたことによる UX 低下（毎回選ばされる／選択を
間違える／どのモデルで作られた成果か分からなくなる）を構造的に防ぐ。

正本: 本文書。関連 = `llm_usage_metering_design.md`（U層。model 列は既に記録されている）/
`assistant_common_infra_design.md`（`resolve_model` の既存規約）。

---

## 0. 現状（調査結果）と結論

### 0.1 現状のモデル決定経路

| 場面 | 現在のモデル決定 |
|---|---|
| 解析パイプラインの全 LLM ステージ（paper_skeleton 〜 component_graph 等） | agent が `ProviderJSONLLMClient(model=None)` → `core.llm.generate_text` の既定 = **`LLM_ANALYSIS_MODEL`**。ステージ別の選択肢は無い |
| apparatus_semantics（vision） | `APPARATUS_LLM_MODEL`（`.env.example` で `gpt-4o` を実値指定） |
| contextual_explanation | `CTXEXPL_LLM_MODEL`（空なら fast tier） |
| 学習チャット / コースビルダー | `LEARNING_CHAT_LLM_MODEL` / `COURSE_BUILDER_LLM_MODEL`（空なら analysis tier） |
| tension / structure_anchor / reconstruction / doubt×2 / deliberation / standardization / Admin Copilot / atlas assist | 各 `*_LLM_MODEL`（空なら fast または analysis tier） |
| tier 既定 | `LLM_FAST_MODEL=gpt-5.4-nano` / `LLM_STANDARD_MODEL=gpt-5.2` / `LLM_DEEP_MODEL=gpt-5.2` / `LLM_ANALYSIS_MODEL` |

つまり **すべて環境変数**で、変更にはデプロイ（再起動）が必要。UI からは1つも選べない。
パイプラインは13以上の LLM ステージがあるのに、実質 `LLM_ANALYSIS_MODEL` 一択。

### 0.2 結論（この設計の骨格）

1. **モデル決定の正本を1箇所に集約する** — `core/llm_policy.py` が
   「場面（scene）→ 実モデル名」を解決し、`core/llm.py` の各 `generate_*` が
   呼び出しの入口で1回だけ通す。呼び出し側（agent・worker・route）のコードは変えない。
   U層の `usage_context(feature=...)` が既に contextvars で場面を運んでいるので、
   **同じ feature 文字列を scene キーとして再利用する**（新しい伝搬経路を作らない）。
2. **選択の階層は4つだけ** — ①システム既定（SYSTEM_ADMIN が運用タブで1回設定）
   ②**ユーザー別の既定**（教員が画面で選ぶと本人の既定として保存される。他の教員には
   影響しない）③対象単位の既定（コース単位。教員が1回設定）④その実行だけの上書き
   （実行を起こす瞬間の1操作）。それ以外の場面では**誰にも選択を要求しない**。
3. **既定は必ず継承され、選択は常に任意** — 未選択という状態を UI に作らない
   （プレースホルダ「選択してください」を出さない。常に有効な実モデル名が入っている）。
4. **表示は常に実モデル名** — `gpt-5.2` / `gpt-5.4-mini` のように model id をそのまま出す。
   `fast` / `standard` / `deep` / `analysis` という**内部 tier 名は UI に一切出さない**。
   出所ラベル（`（システム既定）` / `（この解析のみ）`）を後置して継承関係だけ伝える。
5. **選べないものは出さない** — provider 不一致・vision 非対応・embedding 次元固定は
   選択肢に載せない（fail-closed）。
6. **成果には作成時のモデルを記録し、再利用時に明示する** — 「どのモデルで作られた成果か」
   が後から分かる（出所の正直さ）。

---

## 1. 不変条項

- **M1 モデル決定の正本は1箇所** — 解決は `core/llm_policy.py`。`core/llm.py` 以外の場所で
  「env を読んでモデルを決める」処理を新規に書かない。既存の各 `core/*/llm_client.py` の
  `resolve_model` は policy 経由に委譲し、外部シグネチャは変えない。
- **M2 既定で完結する** — どの画面も、モデルを選ばずに従来どおり実行できる。選択 UI は
  常に「現在の実効モデル名」を初期値として表示し、必須入力にしない。
- **M3 実モデル名で表示する** — ラベルは model id そのもの。tier 名（fast/standard/deep/
  analysis）と社内呼称を UI・API レスポンスの表示文字列に使わない。
- **M4 選択肢を捏造しない** — 選択肢はモデルカタログ（後述）に載っているものだけ。
  カタログが無い環境では「現在の設定値」1件だけを表示する（架空のモデル名を並べない）。
- **M5 能力・provider で fail-closed** — vision が必要な場面に非 vision モデルを出さない。
  `LLM_PROVIDER` と異なるベンダのモデルを出さない。embedding モデルは選択対象外
  （`llm_embedding_dim` = pgvector スキーマと結合しており、切替は既存ベクトルの全滅を招く）。
- **M6 選択を繰り返させない** — 直近の選択を継承する（解析は前回 run の options、
  コース構築はセッション、受講はコース設定）。同じ判断を2回要求しない。
- **M7 成果の出所を残す** — 生成物には使用モデルを記録し、artifact 再利用時は
  「前回 gpt-5.4-mini の結果を再利用」と事実で表示する（情報を落とさない）。
- **M8 数値の開示範囲は U層に従う** — 教員向け UI に金額（USD/円）を出さない。
  コスト感は相対目安（低 / 中 / 高）と U層の既存レンジ見積りのみ。単価表示は
  SYSTEM_ADMIN の運用タブに限る（U5 / U7 = 単価はハードコードせず価格表 JSON から）。
- **M9 学習者に選ばせない** — 学生 UI にモデル選択・モデル名を出さない。受講時のモデルは
  教員（コース設定）または SYSTEM_ADMIN の決定。P3/P7（監視しない・演技化させない）と同型。
- **M10 上限は変えない** — 既存の `*_MAX_CALLS_PER_*` / CostGate はモデル選択と独立。
  高価なモデルを選んでも回数上限は緩まない。

---

## 2. 場面カタログ（scene）

`core/llm_usage/schema.py::KNOWN_FEATURES` が既に全 LLM 呼び出し地点を列挙している。
これを**UI に出す粒度**へ束ねたものが scene。正本は `core/llm_policy.py::SCENES`。

| scene キー | 表示名 | 含む feature | 選択の主体 / タイミング |
|---|---|---|---|
| `pipeline` | 教材の解析 | `pipeline:*`（vision 除く） | 教員 / アップロード・再解析の直前 |
| `pipeline.vision` | 教材の図の解析 | `pipeline:apparatus_semantics` | 教員 / 同上（vision 対応モデルのみ） |
| `course_builder` | コース構築チャット | `admin:course_builder` | 教員 / セッション単位（会話中に変更可） |
| `lecture_studio` | 原稿の生成・書き換え | `admin:lecture_rewrite`, `admin:lecture_generate` | 教員 / 生成モーダル |
| `learning_chat` | 受講中のチャット | `learning:chat`, `learning:chat_casual`, `learning:chat_discuss`, `learning:understanding_check`, `learning:help_usage` | 教員 / コース設定（学生には出さない） |
| `learning_voice` | 音声会話 | `learning:voice_stt`, `learning:voice_tts` | SYSTEM_ADMIN のみ（別軸のモデル体系） |
| `learning_background` | 学習の裏方（痕跡解析） | `learning:tension`, `learning:structure_anchor`, `admin:reconstruction_authoring` | SYSTEM_ADMIN / 運用タブのみ |
| `atlas` | 分野の地図の生成 | `admin:atlas_skeleton`, `admin:atlas_assist` | 教員 / 骨格生成ボタンの隣 |
| `doubt` | 前提の地図 | `doubt:*` | SYSTEM_ADMIN / 運用タブのみ |
| `deliberation` | 要素検討ワークスペース | `deliberation:chat`, `deliberation:figure_reanalysis`, `deliberation:standardization` | 教員 / 対話パネル（`deliberation:vision` は vision 対応のみ） |
| `assistant` | 管理アシスタント | `admin:assistant` | SYSTEM_ADMIN / 運用タブのみ |

**選択の主体を場面ごとに固定するのが UX 設計の中心**。「全場面を全ロールに開放」は
選択ストレスそのものなので採らない。教員が触るのは上表の4〜6場面だけで、残りは運用タブに畳む。

`pipeline` scene だけは**ステージ別の任意上書き**を許す（`pipeline:claim_qualification` など
feature 単位）。ただし既定は scene 一括で、ステージ別は「詳細」を開いた人だけが見る。

---

## 3. 解決順序（precedence）

`core/llm_policy.py::resolve_scene_model(feature, *, requested=None, capability=None) -> ResolvedModel`

1. **呼び出し側の明示引数**（`generate_text(model=...)`。既存コードの意図を壊さない）
2. **実行時オーバーライド**（contextvar `model_override`。route / orchestrator が
   run options・コース設定・リクエスト値からセット）
3. **ユーザー別ポリシー**（`llm_model_policies` の `scope='user'` 行。**モデル選択は
   ユーザーごとに保存される** — 教員 A が教材管理で選んだモデルは A の既定になるだけで、
   教員 B の画面・実行には影響しない。resolve 時の user_id は U層
   `current_usage_context().user_id` から取る — 新しい伝搬経路を作らない。
   user_id が取れない呼び出し（無帰属の worker 等）はこの層をスキップ）
4. **システム既定**（同テーブルの `scope='system'` 行。SYSTEM_ADMIN が運用タブで管理）
5. **環境変数**（既存 `*_LLM_MODEL`。DB 行が無いときのみ効く = 完全な後方互換）
6. **tier 既定**（`LLM_FAST_MODEL` / `LLM_ANALYSIS_MODEL`。既存の fallback を維持）

**初回起動時に 5 → 4（env → `scope='system'` 行）のシード取込を行う**（冪等。atlas_skeletons / library の
「同梱ファイルをシードして以降 DB を正本」と同じパターン）。これにより
`APPARATUS_LLM_MODEL=gpt-4o` のような既存の実値指定が UI に**そのまま初期値として現れ**、
「UI で設定したのに env に負けて効かない」という最悪の混乱を避ける。シード後に env を
書き換えても DB 行が勝つため、運用タブに `env: APPARATUS_LLM_MODEL=gpt-4o（取込済み・
現在は UI 設定が有効）` と事実を併記する。

`ResolvedModel` は `{model, source, source_label, reasoning_effort}` を返し、
`source ∈ {call_argument, run_override, course_override, user_policy, system_policy,
env, tier_default}`。UI の出所ラベルはこの値から作る（推測しない）。
出所ラベル例: `（あなたの既定）` / `（システム既定）` / `（この解析のみ）` /
`（このコースの設定）`。

`reasoning_effort`: tier 経由では tier の effort が付く。モデルを直接指定した場合は
カタログの `default_effort`（無ければ `medium`）を使い、詳細 UI でのみ上書きできる。
`max_tokens` は既存 `max_tokens_for_model` に委ねる（未知モデルは analysis ティア上限）。

---

## 4. 保存先

| 層 | 保存先 | migration |
|---|---|---|
| システム既定（scene / feature 単位） | **新テーブル `llm_model_policies`**（`scope='system'`） | **061**（新規） |
| ユーザー別の既定（教員ごと） | 同テーブル（`scope='user'` + `user_id`） | 061 に同梱 |
| 解析 run の上書き | `document_analysis_runs.options.models`（既存 JSONB） | 不要 |
| コース単位の上書き | `learning_courses.data.llm_models`（`core/course_data.py` にアクセサ追加） | 不要 |
| コース構築セッションの上書き | `course_builder_sessions` の履歴 JSON に同梱 | 不要 |
| 要素検討セッションの上書き | `deliberation_sessions` の既存 JSON | 不要 |

`document_analysis_runs.options` は**明示指定が無ければ前回 run から継承される**既存仕様
（orchestrator L292-308）。ここに載せるだけで M6（選択を繰り返させない）が自動的に満たされる。
これが「解析のモデル選択を options に置く」最大の理由。

### migration 061 `llm_model_policies`

```sql
CREATE TABLE IF NOT EXISTS llm_model_policies (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope        TEXT NOT NULL CHECK (scope IN ('system', 'user')),
    user_id      UUID REFERENCES users(id) ON DELETE CASCADE,  -- scope='user' のみ非NULL
    scene_key    TEXT NOT NULL,            -- 'pipeline' / 'pipeline:claim_qualification' 等
    model        TEXT NOT NULL,
    reasoning_effort TEXT,                 -- NULL = カタログ既定
    updated_by   UUID,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note         TEXT NOT NULL DEFAULT '',
    CHECK ((scope = 'user') = (user_id IS NOT NULL))
);
-- system 行は scene_key 単位で一意、user 行は (user_id, scene_key) 単位で一意
CREATE UNIQUE INDEX IF NOT EXISTS llm_model_policies_system_uq
    ON llm_model_policies (scene_key) WHERE scope = 'system';
CREATE UNIQUE INDEX IF NOT EXISTS llm_model_policies_user_uq
    ON llm_model_policies (user_id, scene_key) WHERE scope = 'user';
```

削除 API は作らない（「既定に戻す」= 行の DELETE ではなく `model=''` ではなく、
**明示の restore 操作で行を削除**する運用にするか、`source` を辿れるよう
`model` に env 由来値を書き戻す。v1 は前者＝ restore で行削除とし、監査に残す）。
変更は `theory_review_events`（`entity_type='llm_model_policy'`、
`AUDIT_ENTITY_LLM_MODEL_POLICY` をカタログに追加）に記帳する。

---

## 5. モデルカタログ（選択肢はどこから来るか）

**モデル名をコードにハードコードしない**（開発ルール1 / U7 の価格表と同じ姿勢）。

`LLM_MODEL_CATALOG_PATH`（既定 `backend/config/llm_models.json`）の JSON:

```json
{
  "models": [
    {"id": "gpt-5.2",       "provider": "openai", "capabilities": ["text", "structured", "vision"],
     "default_effort": "medium", "cost_hint": "high",   "note": "既定の解析モデル"},
    {"id": "gpt-5.4-mini",  "provider": "openai", "capabilities": ["text", "structured"],
     "default_effort": "medium", "cost_hint": "medium", "note": ""},
    {"id": "gpt-5.4-nano",  "provider": "openai", "capabilities": ["text", "structured"],
     "default_effort": "low",    "cost_hint": "low",    "note": "軽量タスク向け"},
    {"id": "gpt-4o",        "provider": "openai", "capabilities": ["text", "structured", "vision"],
     "default_effort": null,     "cost_hint": "medium", "note": "図の解析で使用中"}
  ]
}
```

- **カタログが無い / 読めない場合**: 現在の実効モデル1件のみを選択肢として返す
  （`disabled` なプルダウン + 「選択肢が未設定です」の事実文）。架空の候補を並べない（M4）。
- **provider フィルタ**: `settings.llm_provider` と一致するものだけ返す（M5）。
- **capability フィルタ**: scene が要求する capability（`pipeline.vision` → `vision`）で絞る。
- **`cost_hint`** は `low|medium|high` の相対ラベルのみ。金額は持たせない（M8）。
  SYSTEM_ADMIN の運用タブでは価格表（`LLM_PRICE_TABLE_PATH`）が設定されていれば
  単価列を併記できる（価格表が無ければ列自体を出さない）。
- カタログは「この環境で選ばせてよいモデル」のホワイトリストでもある。新モデルの追加は
  JSON 1行（デプロイ）で、コード変更を伴わない。

---

## 6. UI 設計（提示のタイミング）

設計方針: **選択は「実行を起こす瞬間」の近傍に1つだけ置く。それ以外は表示のみ。**
プルダウンを常時開いた状態で並べると「選ばなければいけない」という負荷を生むため、
既定値が入った**1行サマリ + 変更リンク**を基本形にする。

### 6.1 教材管理（パイプライン）— ご提案への回答

ご提案の「教材管理画面にプルダウン」は場所として正しい（アップロードは投げたら戻れない
一方向の操作なので、選択の機会はその直前だけ）。ただし素のプルダウンより、次の形を推奨する。

**A. アップロードゾーン直下に1行サマリ（常時可視・既定入り）**

```
┌──────────────────────────────────────────────┐
│  PDF または TeX .tar.gz をドラッグ＆ドロップ      │
│              ファイルを選択                      │
└──────────────────────────────────────────────┘
 ☐ 図面・画像を解析する（装置図の同定に vision AI を使用）

 解析モデル: gpt-5.2（システム既定）              [変更]
```

- 常時見えるので「選べること」が分かる。既定値が入っているので**何もしなくても投げられる**。
- `[変更]` を押すと同じ位置に小パネルが開く:

```
 解析モデル
   ( ) gpt-5.2        既定 · コスト目安 高
   (•) gpt-5.4-mini        コスト目安 中
   ( ) gpt-5.4-nano        コスト目安 低
   図の解析（vision）: gpt-4o ▾     ← vision 対応のみ／画像解析 ON の時だけ表示
   ▸ ステージ別に指定する（詳細）
   [この設定で解析する]  [既定に戻す]
```

- ラジオにしてプルダウンにしないのは、選択肢が3〜5件で、コスト目安を横に併記したいから
  （プルダウンは中身を開くまで比較できない）。5件を超える環境では `<select>` に切替。
- `▸ ステージ別に指定する` は既定で閉じている。開くと13ステージの表が出て、各行は
  `継承（gpt-5.4-mini）` が初期値。ここを触るのは検証目的の少数で、通常操作の視界に入れない。
- 選択は `POST /materials/upload` の `models` フォーム値（JSON 文字列）として送り、
  `document_analysis_runs.options.models` に入る。
- **パネルで確定した選択は本人のユーザー別既定として保存される**（`scope='user'` 行の
  upsert）。次回以降、この教員のアップロード画面は `gpt-5.4-mini（あなたの既定）` から
  始まる。他の教員には影響しない。`[既定に戻す]` は本人の user 行を削除し、
  システム既定へ戻す（システム既定そのものは変更できない — それは運用タブの権限）。

**B. 再解析モーダルは前回値を引き継いで表示**

再解析モーダルは既に前回 options（`analyze_images`）を復元している。同じ場所に
`解析モデル: gpt-5.4-mini（前回と同じ）  [変更]` を出す。**選び直しを要求しない**（M6）。

**C. 教材一覧に「解析モデル」列（表示のみ）**

どのモデルで作られた成果かが後から分かるようにする。値は最新 run の
`options.models` と `llm_usage_events` の実測 model から導出（不一致なら実測を優先し
`gpt-5.4-mini（一部 gpt-5.2）` のように事実で書く）。ここにプルダウンは置かない
（一覧行から不可逆な再解析を起こせる導線を増やさないため）。

**D. resume（artifact 再利用）の正直さ**

モデルを変えて再解析すると、resume 時に再利用される artifact は前回モデルの産物。
再解析モーダルに事実文で出す:

> 前回の解析結果を再利用するステージがあります（paper_skeleton ほか5件は gpt-5.2 の結果）。
> すべてを新しいモデルで作り直すには「最初から解析」を選んでください。

各 artifact に `_meta.model` を記録して初めて言える情報なので、Phase 1 で記録を入れる。

### 6.2 コース構築チャット

チャット入力欄の右上に**モデルチップ**（表示のみ、クリックで変更）:

```
 コース構築アシスタント                          [ gpt-5.2 ▾ ]
```

- 変更は次のターンから適用。履歴は保持（既存の `window_history` 規約に影響しない）。
- チップは会話開始時にシステム既定から埋まる。セッション再開時は前回の選択を復元。
- 会話の途中でモデルが変わった場合、その位置に区切り線1本 +
  `ここから gpt-5.4-mini で応答します` の事実文を入れる（後から読み返して分かるように）。

### 6.3 原稿スタジオ

- 一括生成・AI書き換えは既に確認モーダルを持つ。そのモーダル内に1行
  `生成モデル: gpt-5.2（システム既定） [変更]`。
- 音声（TTS）は voice / language と同じモーダルだが**別軸のモデル**なので行を分ける。
  v1 では TTS モデルの選択は運用タブのみ（`learning_voice` scene）。

### 6.4 受講（学習チャット / discuss / casual / help）

- **学生 UI には出さない**（M9）。
- コース管理 → コース設定に `学習チャットのモデル: gpt-5.2（システム既定） [変更]`。
  保存先は `learning_courses.data.llm_models.learning_chat`（アクセサは
  `core/course_data.py` に追加 — 素の dict アクセス禁止, Tier 3-18）。
- コース公開（freeze）との関係: モデル選択は**運用パラメータであり学習内容ではない**。
  実行時のモデル解決は常に live（HEAD）の course データから読む — 学習者が版ピンの
  スナップショットを見ている場合でも、モデルだけは所有者の現在の設定に従う
  （V層のスナップショット生成・release 差分は変更しない。読み取り側で live を正とする）。

### 6.5 分野の地図 / 要素検討 / 前提の地図・裏方 worker

- 分野の地図: 骨格生成ボタンの隣にチップ（生成は明示操作なので同じ形）。
- 要素検討: 対話パネルのヘッダにチップ。figure の vision 再解析は vision 対応のみ。
- 前提の地図・tension/anchor/reconstruction・Admin Copilot: 非同期・自動起動で
  「実行の瞬間」がユーザーに無いため、**運用タブのみ**（教員に選択機会を作らない）。

### 6.6 運用タブ「AIモデル」（SYSTEM_ADMIN のみ）

**システム既定**の一覧表（`scope='system'`）。scene 行 × `現在のモデル / 出所 / 変更`。
ユーザー別の上書きは各教員の画面（6.1〜6.5）でのみ設定・解除する — 運用タブから
他人の user 行は見えない・触れない（本人の選択は本人のもの）。

```
 場面                  現在のモデル      出所            
 教材の解析            gpt-5.2          システム既定      [変更] [既定に戻す]
 教材の図の解析        gpt-4o           env 取込          [変更]
 コース構築チャット    gpt-5.2          tier 既定         [変更]
 受講中のチャット      gpt-5.2          tier 既定         [変更]
 学習の裏方            gpt-5.4-nano     tier 既定         [変更]
 ...
 ▸ ステージ別の指定（3件）                        ← 個別上書きがある時だけ表示
```

- 価格表がある場合のみ単価列を追加（M8）。
- 「LLM使用量」タブへのリンクを1本置く（選択の結果は既にそこで観測できる）。
- 変更は即時反映（次の呼び出しから）。再起動不要。監査記帳あり。

---

## 7. API

すべて `_require_teacher` 以上。数値開示は U層の規約に従う。

| メソッド / パス | ロール | 用途 |
|---|---|---|
| `GET /api/admin/llm-models/catalog?scene=` | TEACHER | 選択肢（provider / capability で絞り込み済み）+ 各 scene の**本人にとっての**実効モデルと出所（user 行 > system 行 > env > tier） |
| `GET /api/admin/llm-models/policies` | SYSTEM_ADMIN | システム既定（`scope='system'`）一覧（env 由来の事実併記） |
| `PUT /api/admin/llm-models/policies/{scene_key}` | SYSTEM_ADMIN | システム既定の変更（監査記帳） |
| `DELETE /api/admin/llm-models/policies/{scene_key}` | SYSTEM_ADMIN | システム既定を env / tier 既定に戻す（行削除・監査記帳） |
| `PUT /api/admin/llm-models/my-policies/{scene_key}` | TEACHER | **本人の**ユーザー別既定の upsert（`scope='user'`, user_id=本人固定。他人の user_id は指定不可） |
| `DELETE /api/admin/llm-models/my-policies/{scene_key}` | TEACHER | 本人のユーザー別既定を解除（システム既定へ戻る） |
| 既存 `POST /materials/upload` / `POST /documents/{id}/reanalyze` | TEACHER | `models` パラメータ追加（run options へ） |
| 既存コース設定 PUT | TEACHER | `data.llm_models` 追加 |

**バリデーションはサーバ側で fail-closed**: カタログに無い model id、provider 不一致、
capability 不足は 422（フロントの絞り込みを信頼しない）。Admin Copilot の P1 と同型。

---

## 8. 実装の要点

- `core/llm_policy.py`（**FastAPI 非 import**、開発ルール2）:
  `SCENES` / `scene_for_feature()` / `resolve_scene_model()` / `load_catalog()` /
  `model_override()` contextmanager（`llm_usage/context.py` と同型の contextvars）。
- `core/llm.py` の各 `generate_*` 先頭で `resolve_scene_model` を1回通す。
  既存の `model` 引数はそのまま最優先（M1・後方互換）。
- 既存 `core/llm_worker/client.py::resolve_model` と各系統の `resolve_model` は
  policy 委譲に置き換える（**外部シグネチャ不変**。help_kb が `knowledge.py` を薄い委譲に
  変えたときと同じ手口）。
- orchestrator: `effective_options.get("models")` から stage ごとに
  `model_override(...)` を張る（`report_start` の直後、既存 `usage_context` と同じ位置）。
  ステージ追加規約（`_stage_<name>` + `_PIPELINE_STEPS`）は変えない。
- artifact に `_meta.model` を記録（resume の正直さ = M7）。
- フロント: `admin.js`（教材管理の1行サマリ・一覧の列）/ `admin-lecture-studio.js`（生成モーダル）
  / 新規 `admin-llm-models.js`（ES5・`window.AdminLlmModels`。カタログ取得・チップ描画・
  運用タブの表を共有）。学習側 `app.js` は**変更しない**（M9）。

---

## 9. やらないこと（v1 非スコープ）

- 学習者向けのモデル選択・モデル名表示（M9）。
- embedding モデルの切替（pgvector 次元と結合。切替は別プロジェクト）。
- グループ単位のモデルポリシー（scope 語彙は将来拡張可。v1 は `system` / `user` のみ）。
- 自動フォールバック（「高いモデルが失敗したら安いモデルで再試行」）— 失敗の意味が
  変わるので入れない。既存の repair/retry 規約を崩さない。
- コスト上限の連動（`*_MAX_CALLS_*` はモデルと独立。M10）。
- モデル別の品質評価・推奨表示（「このモデルは精度が低い」等の煽り文言を出さない）。
- 版（V層 release）へのモデル記録 — 運用パラメータであり学習内容ではない（6.4）。

---

## 10. Phase 分割

| Phase | 内容 | 出荷単位 |
|---|---|---|
| **0** | `core/llm_policy.py` + カタログ JSON + `core/llm.py` の一点通し + 既存 `resolve_model` の委譲。**UI 無し・挙動不変**（env → 同じ結果） | 単独で安全に出せる |
| **1** | migration 061 + 運用タブ「AIモデル」（SYSTEM_ADMIN の表）+ env シード取込 + 監査 | ここで「デプロイ無しでモデルを変えられる」が成立 |
| **2** | 教材管理の1行サマリ + 変更パネル + `options.models` + artifact `_meta.model` + 一覧の列 + 再解析の再利用告知 | ご要望の中心 |
| **3** | コース構築チップ / 原稿スタジオ生成モーダル / コース設定（受講チャット）/ 地図・要素検討チップ | 場面ごとに独立して追加可 |
| **4** | ステージ別指定（詳細）と `pipeline:*` feature 単位ポリシー | 検証者向け |

---

## 11. ガードレール（`backend/tests/test_llm_model_policy_guardrails.py`）

1. `core/llm_policy.py` が FastAPI を import しない。
2. カタログに無い model id / provider 不一致 / capability 不足を `resolve_scene_model` と
   API が拒否する（fail-closed）。
3. **UI 文言・API レスポンスに tier 名（`fast` / `standard` / `deep` / `analysis`）が
   表示文字列として現れない**（M3）。
4. 学生向けエンドポイント（`/api/learning/*`）のレスポンスにモデル名が現れない（M9）。
5. 教員向けレスポンスに金額（`cost_usd` 等）が含まれない（M8）。
6. DB ポリシー行が無いとき、解決結果が**現在の env / tier 既定と完全に一致**する
   （Phase 0 の挙動不変を機械的に固定）。
7. 削除 API が `llm_usage_events` を触らない（U6 の append-only を侵さない）。
8. `KNOWN_FEATURES` の全 feature が `scene_for_feature()` で解決できる
   （新機能の feature を追加したのに scene 未定義、を検出）。
9. **ユーザー分離**: 教員 A の `scope='user'` 行が教員 B の解決結果・catalog レスポンスに
   一切影響しない。`my-policies` API が本人以外の user_id を受け付けない（fail-closed）。

---

## 12. 実装記録 — レビュー指摘 C1 / m4 / m6 / m7 の修正（2026-07-31）

Phase 0〜4 実装後のレビューで、**パイプライン系だけモデル選択が実質無効**になっていた
ことが判明した。4件をまとめて修正した（migration 不要・API 変更なし）。

### C1（Critical）contextvars がウォールタイムアウトスレッドを越えていなかった

`src/episteme_graph/agents/llm_json_client.py::_call_with_wall_timeout` が
`threading.Thread(target=target)` を **`contextvars.copy_context()` なし**で起動していた。
contextvars はスレッドをまたがないため、スレッド内では

- `core.llm_policy.model_override`（§3 解決順②）が消え、
- `core.llm_usage.context`（U層の帰属 = §3 解決順③④が読む `user_id` の出所）も消える。

結果、`core/llm.py` の入口解決は `feature='unattributed'` → scene なし → 常に
`LLM_ANALYSIS_MODEL`（tier 既定）になり、run の `options.models` / ユーザー既定 /
システム既定のどれも **paper_skeleton / rhetorical_role / claim_qualification /
equation_semantics / thesis_reconstruction / dsl_linking / component_assembly /
narrative_annotator** に届いていなかった（`apparatus_semantics` と `discuss_opening` は
モデルを事前解決して明示引数で渡すため無事だった）。さらに `_record_stage_model_if_used`
（M7）は orchestrator スレッド側で解決するため、`stage_outputs._stage_models` には
**実際には使われなかったモデルが記録される**という食い違いまで生じていた。

修正: `ctx = contextvars.copy_context()` を取り
`threading.Thread(target=lambda: ctx.run(target))` で実行する（壁時計タイムアウト・
例外伝播・戻り値の扱いは不変。コンテキストはコピーなのでスレッド内の書き込みは
呼び出し元へ漏れない）。

> **教訓（M層の一般規則）**: 「LLM 呼び出しの手前でスレッド・executor を起こす」箇所は
> すべて `contextvars.copy_context()` を通すこと。渡し忘れるとモデル選択と U層帰属が
> **静かに** tier 既定 / unattributed へ落ちる。

### m4（Medium）pipeline の `usage_context` に `user_id` が無く `scope='user'` が inert

`run_document_pipeline` の `bind_usage_context` が document_id / run_id / course_id しか
bind しておらず、§3 解決順③（`scope='user'` のポリシー行）に到達できなかった
（アップロード UI が run override を常送するため隠れていたが、`models` 未指定の再解析・
API 直呼びでは無効）。

修正: `run_document_pipeline(..., user_id=None)` を追加し `bind_usage_context` に bind する
（未指定でも従来どおり = system 行 → env → tier 既定）。呼び出し元は
`api/services.py::process_material_background`（`user_id` を受けて透過的に渡す）と
`api/routes/lecture_studio/pipeline.py` の2 worker（既に `user_id` を持っている）。

### m6（Minor）`_ctxexpl_model` の env 直読み（M1 違反）

`orchestrator._ctxexpl_model` が `os.getenv("CTXEXPL_LLM_MODEL")` を直読みし DB ポリシー層を
素通ししていた。兄弟の `_discuss_opening_model` と同型に
`resolve_scene_model("pipeline:contextual_explanation")` へ委譲した（`CTXEXPL_LLM_MODEL` は
`llm_policy._FEATURE_DIRECT_ENV` 経由で従来どおり効き、未設定なら fast tier —
env のみの環境では解決結果が完全に一致する）。run options（`pipeline:contextual_explanation`
→ `pipeline`）の先読みは従来どおり維持。

### m7（Minor）equation_semantics vision の settings 直読み

`equation_semantics/llm_client.py` が `self.model or settings.llm_analysis_model` で
env 由来値を明示引数化し、policy 層をバイパスしていた。テキスト経路は従来どおり
`model=None` のまま `core.llm` 入口へ委ね、vision 経路（provider SDK を直接叩くため具体名が
必要）は新設 `_resolve_vision_model(settings)` が
`resolve_scene_model("pipeline:equation_semantics")` へ委譲する。同 feature の env マッピングは
無いため fallback tier は `analysis` = 従来の `settings.llm_analysis_model` と一致し、
`llm_policy` が使えない環境では settings へフェイルオープンする。

### テスト

- `backend/tests/test_pipeline_model_thread_propagation.py`（新規）— **実クライアント経由**の
  統合テスト。既存 `test_pipeline_model_override.py` は fake step が orchestrator スレッド内で
  `resolve_scene_model` を呼ぶため C1 を素通りしていた。ここでは実 `ProviderJSONLLMClient` を
  `core.llm.generate_text` スタブで駆動し、(a) `model_override` が効く (b) `usage_context` の
  feature / user_id / document_id / run_id がスレッド内に伝搬する (c) `_stage_models` の記録値が
  実際に generate に渡ったモデルと一致する、を固定。m4 / m6 / m7 の解決順と挙動不変性も同ファイル。
- `src/tests/agents/test_llm_json_client.py` — `_call_with_wall_timeout` の contextvars 伝搬・
  非漏洩・例外/タイムアウト伝播（core 非依存の純ユニット）。
- `src/tests/agents/equation_semantics/test_llm_client_model.py`（新規）— `_resolve_vision_model`
  の委譲先 feature と フェイルオープン。

### 未修正（別件として記録）

`backend/core/theory_components.py` の `_EXTRACTION_EXECUTOR`
（`ThreadPoolExecutor.submit(_call_llm)`、2箇所）も同じ形で contextvars を落とすため、
原稿スタジオの理論コンポーネント抽出はモデル選択と U層帰属が効かない。C1 と同じ
`copy_context` 対応が必要（本修正のスコープ外）。

---

## 13. 実装記録 — レビュー指摘 J1〜J6 / m1〜m3 の修正（policy・routes 系, 2026-07-31）

§12（パイプライン系）と並行して、**policy 解決層と管理 API / 呼び出し側**のレビュー指摘を
修正した（migration 不要・API のパス／レスポンス形状は互換のままフィールド追加のみ）。

### J1（Major）再解析が前回 run の `options.models` を黙って捨てていた

orchestrator は run の `options` を **wholesale 置換**する（部分マージしない）。にもかかわらず
`routes/admin.py::reanalyze_document` は `analyze_images` だけを明示した場合に
`options = {"analyze_images": ...}` を組み立てていたため、前回 run のモデル指定が消えていた
（逆方向 = `models` だけ明示のケースは前回 run を読んで温存済みで、**非対称**だった）。
再解析モーダルは `解析モデル: …（前回と同じ）` と表示するので、表示と実 run が食い違う（M6 違反）。

修正: どちらか一方でも明示されたら**前回 run の options を土台にマージ**する
（`_previous_run_options()` は best-effort = 読めなければ空 dict で従来挙動へフェイルオープン。
両方未指定は従来どおり `options=None` を渡して orchestrator の継承分岐に委ねる）。

### J2（Major）env シードが feature キーで書かれ scene 設定を恒久シャドウしていた

`llm_policy_store.seed_env_policies` は env を **feature キー**（`pipeline:apparatus_semantics`
など）で `scope='system'` 行に書いていた。一方 UI / API が編集するのは **scene キー**
（`pipeline.vision`）で、`_pick_priority_row` の優先順は system+feature > system+scene。
その結果、**運用タブで保存しても効かず、表示も古いまま**になっていた。
`APPARATUS_LLM_MODEL` は既定 `gpt-4o`（非空）なので、全新規環境で必ず発生する。

修正: **シードの書き込みキーを「UI が編集するキー」に揃える**。計画の正本は
`core/llm_policy.py::iter_env_seeds()`（純関数・DB 非接触）:

- scene の**代表 feature**（下記 m1）が env を持つ場合は **scene キー**で1行シードする
  （その env を共有する feature 群を1行でカバー。例: `pipeline.vision` ← `APPARATUS_LLM_MODEL`、
  `learning_chat` ← `LEARNING_CHAT_LLM_MODEL`）。
- 同じ scene に**別の env 変数**を持つ feature がある場合（学習の裏方 = TENSION / ANCHOR /
  RECON、前提の地図 = DOUBT_SCOPE / DOUBT_ASSUMPTION、要素検討 = DELIBERATION / STDPART）は、
  その値を落とさないために従来どおり **feature キー**でシードする。これらは運用タブの
  「ステージ別の指定（N件）」節に現れ、そこで変更・解除できる（= 既知の限界ではなく、
  「env が feature 単位に分かれている」という事実をそのまま写している）。
- `_FEATURE_DIRECT_ENV`（`CTXEXPL_LLM_MODEL` / `DISCUSS_OPENING_LLM_MODEL`）は運用タブの
  ステージ別指定で編集できるキーそのものなので、従来どおり feature キー。
- 読み取り専用 scene（下記 J5 の `learning_voice`）はシードしない。

**既存 DB の移行**: 旧シード行は note（`seeded from env: <ENV_NAME>`）で識別できるため、
scene キー行をシードする際に「同じ note **かつ** model が現在の env 値と一致する
`scope='system'` の feature キー行」だけを冪等に削除する（`_delete_legacy_env_seed_rows`）。
人が運用タブで編集した行（note が空 / model が env と異なる）は削除しない（P4 情報を落とさない）。
削除と scene 行の追加は同じシード処理内で行われ、値が同じなので解決結果は不変。

### J3 / J4（Major）原稿スタジオ・地図生成が policy を素通し（運用タブは効いていると表示）

- `core/lecture.py::generate_spoken_text_and_formulas` と
  `routes/lecture_studio/scripts.py::rewrite_lecture_script` が、モデル未指定時に
  `get_llm_params("fast")["model"]` を **明示引数**で渡していた → 解決順①
  （`call_argument`）に化けて user / system ポリシーが一切効かない。
- `core/atlas_generator.py::generate_skeleton_draft` も `model or settings.llm_analysis_model` で同型。

修正:
- `scripts.py`（リクエストスレッド）は `model=None` のまま `generate_text` に渡し、
  `core/llm.py` 入口の `resolve_scene_model` に委ねる（feature は既存 `usage_context` の
  `admin:lecture_rewrite`）。`reasoning_effort` は従来どおり tier の値を渡す（挙動不変。
  `effort_for_call` は呼び出し側指定を常に優先する）。
- `core/lecture.py` は `llm_policy.resolve_scene_model("admin:lecture_generate")` で
  **自分で解決してから明示引数として渡す**。`generate_text(model=None)` に委ねないのは、
  学習側 `routes/lecture.py` の呼び出しが `usage_context` を張らず `unattributed` になり、
  tier が analysis に化けてしまうため（バックグラウンドスレッドで contextvar が使えない
  事情も同じ）。
- `atlas_generator` も同様に `resolve_scene_model("admin:atlas_skeleton")` で解決してから渡す
  （`generated_by: "model:<id>"` に実モデル名を残す必要があるため）。

**Phase 0 不変性の担保**: `admin:lecture_rewrite` / `admin:lecture_generate` は専用 env を
持たないため、そのままでは汎用既定（analysis tier）に落ちて従来（fast tier）と食い違う。
`llm_policy._FEATURE_TIER_ONLY`（env なし・tier だけを宣言するマップ）を新設し、両 feature を
`fast` に固定した。atlas は `admin:atlas_skeleton` → `ATLAS_ASSIST_LLM_MODEL`（既定空）→
analysis tier なので、既定環境では従来と一致する（`ATLAS_ASSIST_LLM_MODEL` を明示設定している
環境では、その値が骨格生成にも効くようになる = M層の feature→env マッピングの既定義に従う）。

### J5（Major）`learning_voice` scene は settable-but-inert だった → **読み取り専用に変更**

音声は STT（`core/llm.py::transcribe_audio` が `settings.llm_transcribe_model` を直参照）と
TTS（`core/tts.py` が provider 別にモデルを固定）で構成され、**policy 解決経路を通らない**。
カタログにも音声モデル（whisper 系 / `tts-1`）は無く、設定可能にしても選べる有効値が存在しない。
さらに運用タブの「現在のモデル」は代表 feature の解決結果（= `LLM_ANALYSIS_MODEL`）で、
**STT でも TTS でもないモデル名**を表示していた。

**v1 の裁定（§6.3 の「TTS モデルの選択は運用タブのみ」からの差分）**: 設定できるのに何も
起きない UI は M4（捏造しない）/ M5（fail-closed）の精神に反するため、`learning_voice` を
**読み取り専用**にする。

- `llm_policy.READ_ONLY_SCENE_KEYS` / `read_only_scene_reason()` / `voice_model_facts()` /
  `voice_display_model()` を新設（正本）。
- `PUT /policies/learning_voice` と `PUT /my-policies/learning_voice` は **422 + 事実文**
  （「音声モデルの変更は v1 では対応していません（表示のみ）」）。
- `GET /policies` / `GET /catalog` は当該 scene に `read_only` / `read_only_reason` を付け、
  `current`（`effective`）に**実際の値**を出す（`whisper-1（音声認識） / tts-1（読み上げ）` +
  `components: [{label, model}]`）。選択肢はその1件のみ（テキスト生成モデルを並べない）。
  provider が音声非対応の組み合わせでは事実文（`（このプロバイダでは未対応）`）を出す。
- フロント（`admin-llm-models.js`）は当該行を「変更できません」＋理由の表示のみにし、
  `[変更]` / `[既定に戻す]` を出さない。
- シード（J2）も当該 scene には行を作らない。

音声モデルを本当に選べるようにする場合は、カタログへの音声モデル区分の追加 + `core/tts.py` /
`transcribe_audio` を policy 経由にする作業が必要（v1 非スコープ）。

### J6（Major）図再解析の監査記録が env 素読みだった

`core/figure_reanalysis.py` の `IterativeConfig(model_name=settings.apparatus_llm_model)` は
`iterative_analysis.model`（監査記録）の値で、実際の生成コールは
`ApparatusSemanticsLLMClient(model=None)` → `core/llm.py` 入口解決なので、**記録と実使用が
食い違う**（orchestrator は同じ問題を `resolve_scene_model("pipeline:apparatus_semantics")` で
既に是正済み。M7）。修正して `resolve_scene_model("deliberation:figure_reanalysis")`（scene は
`pipeline.vision`）の結果を記録する。user ポリシーを拾うため `usage_context` の内側で解決し、
policy 行が無い（env / tier 由来の）ときはこのモジュールが読んでいる `settings` の値を優先する
（本番では同値。差が出るのは settings を差し替えたテストのみ）。

### m1（Minor）運用タブの代表 feature 選定が実効値と食い違っていた

`_representative_feature` が `SCENES[scene]["features"][0]`（= `KNOWN_FEATURES` の登場順の
先頭）を取っていたため、`deliberation` → `deliberation:cross_corpus`（embedding 用・env
マッピングなし）、`assistant` → `admin:component_candidates` のように **その場面の主たる操作
ではない feature** が代表になり、実際は fast tier で動く場面に analysis tier のモデル名を
表示していた。

修正: `llm_policy._SCENE_REPRESENTATIVE_FEATURE`（scene → 主たる操作の feature）を明示宣言し、
`representative_feature_for_scene()` を正本にした（routes 側は委譲のみ）。1つの scene に tier の
異なる feature が混在する場合、単一の値で全部を正しく表すことは原理的に不可能なので
「主たる操作を選ぶ」規則にしてある（宣言漏れはテストが検出する）。

### m2（Minor）非 vision の scene 既定を figure 対話が継承していた

`core/deliberation/dialogue.py::run_turn` が `resolve_model()`（feature `deliberation:chat`
固定・capability 制約なし）で解決していたため、scene `deliberation` のシステム既定に非 vision
モデルを設定すると **画像付きコールが text モデルへ行き**、非LLM フォールバックに縮退していた
（リクエストチップ経路は `routes/deliberation.py` が `deliberation:vision` で検証済みで、
継承経路だけが無防備だった）。

修正は2段:
1. `dialogue.run_turn` は feature 単位で解決する（`resolve_turn_model(feature)`。画像ありは
   `deliberation:vision`）。解決を `usage_context` の**内側**に移したので、`scope='user'` の
   ポリシー（`current_usage_context().user_id` を見る層）もようやく効く。
2. `resolve_scene_model` に **capability の fail-closed** を実装した（従来 `capability` 引数は
   予約のみで未使用）。capability は `required_capability_for_feature(feature)`
   （feature → 所属 scene の順で判定）から自動導出し、満たさない**ポリシー行はその層を
   スキップ**して下位（env → tier 既定）へ落ちる。env / tier 層は既存設定なので capability で
   無効化しない（カタログ外の既存モデルを使えなくする害が大きい）。カタログが読めない環境では
   判定不能なのでフェイルオープン（ポリシー行はカタログ検証を通ってしか作られない）。
   この結果、`pipeline.vision` / `pipeline:apparatus_semantics` / `deliberation:figure_reanalysis`
   も同じ保護を受ける。

### m3（Minor）キャッシュ無効化が commit 前だった

`llm_policy_store.upsert_policy` / `delete_policy` 内の `invalidate()` は commit 前に走るため、
「invalidate → 他スレッドが旧値を読んで 20 秒再キャッシュ → commit」の窓が残っていた。
`routes/llm_models.py` の書き込み4経路（system / my × PUT / DELETE）で **commit 後に
`invalidate()` を呼ぶ**（store 内の呼び出しは best-effort の保険として残す = 二重 invalidate）。

### m8（best-effort）再解析モーダルの再利用告知は**未実装**（既知ギャップ）

§6.1-D「前回の解析結果を再利用するステージがあります（… は `<model>` の結果）」は
`stage_outputs._stage_models`（記録済み）を読めば出せるが、**モーダルへ document / material の
id を渡す配線が無い**（`AdminLlmModels.initReanalyzePanel(containerEl, lastOpts)` は前回 options
だけを受け取り、id は `admin.js` 側にしかない）。実装には `admin.js` の 1 行変更
（id を渡す）+ 前回 run の `_stage_models` を返す小さな読み取り API が必要で、今回の担当範囲
（`admin.js` 編集不可）では入れられなかったため未実装のまま記録する。
§6.1-C「教材一覧の『解析モデル』列（表示のみ）」も同様に未実装。

### テスト

- `backend/tests/test_llm_policy.py` — 代表 feature の網羅・実効値一致（m1）/ 原稿スタジオの
  fallback tier（J3 の Phase 0 不変性）/ capability fail-closed（m2）/ 読み取り専用 scene と
  音声モデルの事実（J5。`core/tts.py` の `tts-1` リテラルとのずれ検出も）/ `iter_env_seeds`（J2）。
- `backend/tests/test_llm_policy_store.py` — シードが scene キーで書かれること・別 env を持つ
  feature は feature キーで残ること・旧シード行の移行削除・人が編集した行は削除しないこと・
  読み取り専用 scene を作らないこと（J2 / J5）。
- `backend/tests/test_llm_model_policy_api.py` — 再解析 options マージ（J1、fail-open 含む）/
  `learning_voice` の 422 と正直な表示（J5）/ 代表 feature 表示（m1）/ commit 後 invalidate（m3）。
- `backend/tests/test_llm_model_policy_guardrails.py` — 呼び出し側が tier モデルを明示引数で
  渡していないことの静的検査 + 実際の解決（J3 / J4 / J6）、figure 対話の vision 継承（m2）。
- `backend/tests/test_llm_models_ui_static.py` — 運用タブの読み取り専用行（J5）。

### 同種の残課題（今回の担当範囲外）

`routes/lecture_studio/topics.py::rewrite_lecture_studio_course_topic` も
`requested_model or params["model"]` の形で J3 と同型のバイパスをしている
（既存テストがその挙動を固定しているため、本修正では触っていない）。

## 14. 追補 — `LLM_STAGE_NAMES` の意味論と導出化（2026-08-14, 提案 §2-9）

本節は凍結済みの §1〜§13 を書き換えずに、ステージ判定の正本の所在だけを追補する
（挙動・API 応答・集合の要素は一切変えていない）。

- **`LLM_STAGE_NAMES` はリテラル定義をやめ、`_PIPELINE_STEPS` からの導出になった。**
  `core/document_pipeline/orchestrator.py` の `PipelineStageDef` が
  `llm_kind`（`none` / `text` / `vision` / `embedding`）・`model_policy`（bool）・
  `progress_unit`（`report_start(..., unit=)` の宣言）・`vision_optional`（bool）を
  宣言し、判定用の集合はすべてそこから導出する:
  - `LLM_STAGE_NAMES = {s.name for s in _PIPELINE_STEPS if s.model_policy}`（12件・不変）
  - `LLM_CALLING_STAGE_NAMES`（`llm_kind ∈ {text, vision}` = 実際に LLM を呼ぶ13件）
  - `VISION_STAGE_NAMES`（`llm_kind == vision` = `{"apparatus_semantics"}`）
- **`LLM_STAGE_NAMES` の意味論は「M層のステージ別モデル選択・使用モデル記録（M7）の
  対象」であって「LLM を呼ぶ事実」ではない。** 両者は `component_graph` の1件で食い違う
  （LLM を呼ぶが M層の対象外）。この差は `LLM_CALLING_STAGE_NAMES - LLM_STAGE_NAMES ==
  {"component_graph"}` として `backend/tests/test_pipeline_stage_registry.py` が固定して
  いるので、意図せず増えれば落ちる。`component_graph` を M層の対象へ昇格させるかは
  `GET /api/admin/llm-models/pipeline-stages` の応答と `PIPELINE_STAGE_LABELS` が変わる
  挙動変更なので、オーナー判断として保留にしてある。
- **vision 判定のリテラル `stage_name == "apparatus_semantics"` は集合参照に置換した。**
  `orchestrator._resolve_stage_override_model`（`pipeline.vision` へのフォールバック）と
  `routes/admin.py::_validate_models_option`（vision capability の必須化）の2箇所が
  `VISION_STAGE_NAMES` を参照する。`llm_policy.scene_for_feature` の
  `"pipeline:apparatus_semantics"` リテラルは**そのまま**（`llm_policy` は orchestrator を
  import しない依存方向を維持する。M1 / ガードレール
  `test_llm_model_policy_guardrails.py`）。
- **`progress_unit` は宣言のみで実行時には使わない。** `report_start` の呼び出しは各
  ステージ本体がリテラル引数で行う形を変えていない（U層の feature 語彙網羅テストが
  ソースからの正規表現抽出に依存している）。宣言と実呼び出しのズレは
  `test_pipeline_stage_registry.py` が静的に照合する。`unit` は入力の単位の意味論であって
  LLM-ness を表さない（`rhetorical_role` は LLM ステージだが `unit="blocks"`）ため、
  `llm_kind` から導出しない。
- `equation_semantics` は再構成が必要な数式候補にのみ切り出し画像を添付する条件付き
  vision で、`vision_optional=True` として宣言だけする（M層では従来どおり text 扱い =
  `pipeline.vision` ではなく `pipeline` にフォールバックする）。
