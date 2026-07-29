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
