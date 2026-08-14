# LLM トークン使用量推計（U層, Usage Metering）設計書 — 実測 + 決定論的推計のハイブリッド台帳

## 0. 背景と課題

LLM 呼び出しはシステム全体に積層しており（RAG チャット / コースビルダー / 原稿スタジオ
rewrite / 解析パイプラインの agent 群 / tension / structure_anchor / Admin Copilot /
再構成ループのオーサリング / D層スコープ候補 / apparatus vision / embeddings / TTS / STT）、
運用コストの大半を占めるにもかかわらず、**どの機能が・どのモデルで・どれだけトークンを
消費しているかを把握する手段が存在しない**。具体的な症状:

1. **usage が捨てられている** — `core/llm.py` の全経路（OpenAI / Gemini / Vertex AI）は
   レスポンスの `usage` / `usage_metadata` を読まずにテキストだけ返している。
   プロバイダが正確な実測値を返しているのに記録がゼロ。
2. **コスト上限が「回数」しか見ていない** — `TENSION_MAX_CALLS_PER_DAY` /
   `ANCHOR_MAX_CALLS_PER_DAY` / `ASSISTANT_MAX_CALLS_PER_DAY` / `RECON_MAX_CALLS_PER_DAY` /
   `DOUBT_SCOPE_MAX_CALLS_PER_DAY` / `APPARATUS_MAX_CALLS_PER_DAY` はすべて in-memory の
   コール回数カウンタで、1 コールの重さ（入力数万トークンの解析 vs 数百トークンの分類）を
   区別できない。上限値の根拠となるデータもない。
3. **事前見積りができない** — `analyze_images=true` の再解析や大部の PDF 解析など重い処理を
   実行する前に「どの程度のトークンを消費しそうか」を教員・管理者に提示できない。
4. **モデルティア選定の根拠がない** — fast / standard / deep ティアの振り分け
   （`get_llm_params`）が適切かを判断する実測データがない。

本設計は **「実測（プロバイダ報告値）を正本、決定論的推計をフォールバック」** とする
使用量台帳（`llm_usage_events`）と、DB を必要としない**事前推計ユーティリティ**の二本立てで
これを解決する。

## 1. 設計原則（不変条項）

既存層の文化（P4 情報を落とさない / fail-closed / 数値の慎重な開示 / core の FastAPI 非依存）
を継承する。

- **U1 実測優先・推計は正直に**: プロバイダが usage を返す場合は必ずそれを記録し
  （`usage_source='reported'`）、返さない場合のみ推計で補う
  （`'estimated_tokenizer'` / `'estimated_heuristic'`）。**実測と推計を混ぜて単一の数値として
  見せない** — 集計 API は常に `usage_source` 別に分離して返す（出所の正直さ。Atlas /
  D層と同じ文化）。
- **U2 呼び出しを止めない・遅くしない**: 計測はテレメトリであり、記録の失敗・遅延が
  LLM 呼び出し本体を失敗させたり遅くしたりしてはならない。同期パスに DB 書込を置かず、
  in-memory バッファ + バックグラウンド flusher（`threading.Thread`、tension/anchor worker と
  同型）で書き込む。`record()` は例外を外に漏らさない（構造的にガードレールで守る）。
- **U3 計測点の一元化**: フックは `backend/core/llm.py`（+ `core/tts.py` の 1 箇所）に限定する。
  呼び出し側のコードは変更しない。機能への帰属（attribution）は `contextvars` で伝搬し、
  各 route / worker / orchestrator が文脈をセットするだけにする（§6）。
- **U4 A層非改変**: `src/episteme_graph/agents/` の agent 群は共通クライアント
  `ProviderJSONLLMClient` 経由で `core.llm.generate_text` /
  `generate_structured_with_images` を呼ぶため、**agent のコードに一切触れずに**自動的に
  計測対象になる。
- **U5 数値の開示範囲**: トークン数・コスト換算は**運用情報**であり、学習者向け API / UI には
  一切出さない。集計 API は SYSTEM_ADMIN のみ（D層 KPI `GET /api/admin/doubt/metrics` と
  同じ立場）。例外は**事前見積り**（§7-2）のみ — 人の評価ではなく資源量の見積りなので
  TEACHER に開示してよいが、点推定でなく**レンジ**（下限〜上限）で返す。
- **U6 削除 API を作らない**: `llm_usage_events` は append-only。行単位の削除・改変 API は
  作らない（P4）。保持期間による aging は将来の運用ジョブとして別途扱う（§12）。
- **U7 料金をコードにハードコードしない**: モデル単価は頻繁に変わるため、価格表は
  設定ファイル（`LLM_PRICE_TABLE_PATH` の JSON）から読む。未設定ならコスト換算欄は
  `null` を返す（捏造しない。P4/開発ルール1と同型）。
- **U8 バッファ溢れ・記録失敗を隠さない**: バッファ上限超過で捨てたイベント数
  （`dropped_events`）を集計 API で開示する。「記録できなかった」ことも観測対象。

## 2. 全体アーキテクチャ

```
呼び出し元（変更しない）
  chat / course builder / lecture rewrite / component candidates
  tension / anchor / assistant / reconstruction / doubt workers
  document pipeline agents (src/episteme_graph/agents/* → ProviderJSONLLMClient)
  embedder / extractor / atlas generate / voice
        │
        │  contextvars: usage_context(feature=..., user_id=..., document_id=..., ...)
        ▼
┌─ backend/core/llm.py（フック地点・唯一の計測点）───────────────────┐
│ generate_text / generate_text_with_structured_output /              │
│ generate_embeddings / generate_structured_with_images /             │
│ transcribe_audio        （+ core/tts.py generate_tts_audio）        │
│                                                                     │
│  provider adapter 実行 → response から usage 抽出（reported）       │
│  usage 無し/失敗時 → estimator で決定論的推計（estimated_*）        │
│  → UsageEvent を組み立て recorder.record()（非ブロッキング）        │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─ backend/core/llm_usage/（新設・FastAPI 非 import）─────────────────┐
│ recorder.py   in-memory bounded buffer + flusher daemon thread      │
│ estimator.py  決定論的トークン推計（事前見積りにも単体利用可）      │
│ context.py    contextvars による帰属伝搬                            │
│ pricing.py    価格表ロード + コスト換算（optional）                 │
│ metrics.py    集計クエリ                                            │
│ schema.py     UsageEvent dataclass・語彙の正本                      │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
              PostgreSQL llm_usage_events（migration 043, append-only）
                           ▲
              GET /api/admin/llm-usage/metrics（SYSTEM_ADMIN）
              GET /api/admin/llm-usage/estimate/...（TEACHER, レンジ表示）
```

## 3. 計測レイヤー（`backend/core/llm_usage/`）

tension / structure_anchor / reconstruction と同型の独立モジュールとして新設する。
FastAPI を import しない（開発ルール2、ガードレールで構造的に検査）。

```
backend/core/llm_usage/
  __init__.py
  schema.py      → UsageEvent dataclass、operation / usage_source / feature 語彙の正本
  context.py     → usage_context() コンテキストマネージャ（contextvars）
  estimator.py   → 決定論的トークン推計（§4）。DB・LLM 非依存で単体利用可
  recorder.py    → record() + bounded buffer + flusher thread
  pricing.py     → 価格表 JSON ロード + cost_usd 換算（未設定なら None）
  metrics.py     → 集計クエリ（metrics API から呼ばれる）
```

### 3.1 UsageEvent（schema.py）

```python
@dataclass
class UsageEvent:
    provider: str                  # 'openai' | 'gemini' | 'google' | 'gemini-vertex'
    model: str
    operation: str                 # 'chat' | 'structured' | 'embedding' | 'vision'
                                   #   | 'transcribe' | 'tts'
    feature: str                   # §6 の語彙。未設定は 'unattributed'
    usage_source: str              # 'reported' | 'estimated_tokenizer'
                                   #   | 'estimated_heuristic'
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    cached_tokens: int | None      # OpenAI prompt_tokens_details.cached_tokens
    reasoning_tokens: int | None   # OpenAI completion_tokens_details.reasoning_tokens
    input_characters: int | None   # 推計の素材（監査・推計精度検証用）
    output_characters: int | None
    image_count: int = 0           # vision の画像枚数
    audio_bytes: int | None = None     # transcribe の入力サイズ
    tts_characters: int | None = None  # TTS は文字課金のため文字数を正とする
    duration_ms: int | None = None     # 呼び出し所要時間
    success: bool = True
    error_type: str | None = None      # 失敗時の例外クラス名（本文は入れない）
    user_id: str | None = None
    document_id: str | None = None
    course_id: str | None = None
    run_id: str | None = None          # document_analysis_runs.id
    metadata: dict = field(default_factory=dict)
```

### 3.2 provider 別の usage 抽出（`core/llm.py` へのフック）

各公開関数の provider 分岐の**戻り値を返す直前**に 1 行のフックを入れる。抽出元:

| 経路 | 実測 usage の在処 | 備考 |
|---|---|---|
| OpenAI chat / structured / vision | `response.usage`（`prompt_tokens` / `completion_tokens` / `prompt_tokens_details.cached_tokens` / `completion_tokens_details.reasoning_tokens`） | 非ストリーミングなので常に取得可能 |
| messages + 単一画像の vision（`generate_json_with_image`。数式画像 OCR） | 上記と同じ（provider 別に `response.usage` / `response.usage_metadata`） | 2026-08 追加。agent が provider SDK を直接叩いていた経路の移設先（下記追補） |
| OpenAI embeddings | `resp.usage.prompt_tokens` | 出力トークンなし |
| Gemini / Vertex AI | `response.usage_metadata`（`prompt_token_count` / `candidates_token_count` / `total_token_count`） | 属性が無い SDK バージョンでは推計へフォールバック |
| Vertex embeddings | `embeddings[].statistics.token_count`（取れなければ推計） | |
| `transcribe_audio` | トークンなし → `audio_bytes` を記録 | whisper 系は分課金。秒数はデコードしないと不明なのでバイト数まで（正直に） |
| `core/tts.py generate_tts_audio` | トークンなし → `tts_characters` を記録 | TTS は文字課金 |

フックのスケッチ（OpenAI chat の例。**呼び出し失敗時も** `success=False` +
入力側の推計値で 1 行残す）:

```python
# core/llm.py generate_text() の openai 経路
import time
started = time.monotonic()
try:
    response = client.chat.completions.create(...)
except Exception as exc:
    _observe_failure("chat", model_name, messages, exc, started)  # 入力は推計で記録
    raise
_observe_openai_chat("chat", model_name, messages, response, started)
return response.choices[0].message.content or ""
```

`_observe_*` は内部で `llm_usage.recorder.record()` を呼ぶだけの薄い関数。recorder 側が
すべての例外を握りつぶす（U2）ため、`core/llm.py` 側に try/except の追加は不要。

**追補（2026-08-14）— 数式画像 OCR の計測漏れを解消**: `equation_semantics` の vision 分岐
（再構成が必要な数式だけ切り出し画像を添付する経路。orchestrator の `PipelineStageDef` では
`vision_optional`）が agent 側（`src/episteme_graph/agents/equation_semantics/llm_client.py`）
から provider SDK を直接叩いており、**このステージの vision トークンが `llm_usage_events` に
1 行も載っていなかった**（U3 の穴。text 経路と `apparatus_semantics` の vision 経路は
`core.llm` 経由なので計測済みだった）。既存 `generate_structured_with_images` は
「フラットな 1 本の prompt + JSON Schema・OpenAI 限定」で、数式 OCR が必要とする
①ロール構成の保持 ②`response_format={"type":"json_object"}` ③Gemini / Vertex AI 経路 を
満たせないため、同じ呼び出しを `core/llm.py` に **`generate_json_with_image(messages,
image_bytes, *, model, mime_type=None) -> str`** として移設し `observe_vision` を通した
（`operation='vision'` / `image_count=1` / 寸法不明のため `metadata.image_estimate='flat'`。
プロバイダが usage を返せば当然 `reported`）。パースは返さない（`str`）— agent 側の
truncate 復旧付きパーサの意味論を変えないため。

- **U4 の但し書き**: 「A層は共通クライアント経由なので触らずに計測対象になる」は
  `ProviderJSONLLMClient`（text）と `generate_structured_with_images`（apparatus vision）
  を使う限り成立する。**agent が provider SDK を直接呼んだ瞬間に成立しなくなる**ので、
  新しい呼び方が必要になったら agent 側で SDK を叩かず `core/llm.py` に観測フック付きの
  公開関数を追加する。
- モデル解決は移設対象外（M層は非改変）。vision は具体的なモデル名が必要なため
  `_resolve_vision_model`（`resolve_scene_model("pipeline:equation_semantics")`）が
  agent 側に残り、`model=` で明示的に渡す。帰属 feature は orchestrator の
  `report_start("equation_semantics")` が張る contextvar（§6）に乗るだけでよい
  → `feature='pipeline:equation_semantics'`。

### 3.3 recorder（recorder.py）

- `record(event)` は bounded な `collections.deque(maxlen=LLM_USAGE_BUFFER_MAX)` に積むだけ。
  上限超過で押し出された件数は `dropped_events` カウンタに加算（U8）。
- flusher は daemon `threading.Thread`。`LLM_USAGE_FLUSH_INTERVAL_SECONDS`（既定 10 秒）ごと、
  または `LLM_USAGE_FLUSH_BATCH`（既定 100 件）到達で `core/postgres.py get_session()` を使い
  バッチ INSERT（`try/finally` で `session.close()`、開発ルール4）。
- DB 不通時はバッファに残したままリトライ（バッファ上限が実質の背圧）。プロセス終了時の
  未 flush 分は失われうる — これは許容し、ドキュメントに明記する（テレメトリであり正本の
  学習データではない）。
- `LLM_USAGE_TRACKING_ENABLED=false` で record() を no-op にできる（テスト・ローカル用）。

## 4. 推計ロジック（estimator.py）— 決定論的ルール

推計は**事後フォールバック**（usage が取れなかった呼び出しの補完）と**事前見積り**
（呼び出す前の資源量予測）の両方で同じ関数を使う。LLM を呼ばず、乱数を使わず、同じ入力には
常に同じ値を返す（テスト可能性）。

### 4.1 優先順位

1. **reported** — プロバイダ報告値。常に最優先（推計はしない）。
2. **estimated_tokenizer** — `tiktoken` が import 可能かつ対象モデルのエンコーディングが
   解決できる場合のみ。**新規必須依存にはしない**（optional import、無ければ 3 へ）。
3. **estimated_heuristic** — 文字種ベースのヒューリスティック（依存ゼロ・常に動く）。

### 4.2 ヒューリスティック式（正本は estimator.py の定数）

```
tokens(text) = ceil( cjk_chars(text) × 1.0  +  other_chars(text) / 4.0 )
```

- `cjk_chars` = CJK 統合漢字・ひらがな・カタカナ・全角記号の文字数
- `other_chars` = それ以外（ASCII 英数・記号・空白）
- 経験則: o200k 系トークナイザで日本語は約 1〜1.5 文字/トークン、英語は約 4 文字/トークン。
  係数は**安全側（過大方向）**に丸めてある。
- メッセージリストは `Σ tokens(content) + 4 × len(messages) + 3`（メッセージ框の定数）。
- structured output（`response_format` に JSON Schema を渡す経路）は **schema の JSON 文字列も
  入力トークンに含める**（実課金対象のため）: `+ tokens(json.dumps(schema))`。
- **誤差幅**: ヒューリスティック値には ±40% の幅を持たせ、事前見積り API は
  `[floor(0.6×est), ceil(1.4×est)]` のレンジで返す（点推定を見せない。U5）。

### 4.3 vision（画像トークン）

- 寸法が既知（`document_figures` は PyMuPDF 由来で幅・高さを持てる）なら OpenAI の公式式:
  `85 + 170 × tiles`（長辺 2048px・短辺 768px に縮小後、512px タイル数）。
- 寸法不明なら**定数 765 トークン/枚**（= 85 + 4×170、detail=high の典型値）で推計し、
  `metadata.image_estimate='flat'` を付けて出所を残す。

### 4.4 出力側の推計

- 事後フォールバック: 生成テキストの文字数から §4.2 式で推計。ただし reasoning モデルの
  不可視 reasoning トークンは推計不能なので、`metadata.reasoning_excluded=true` を付けて
  過小になりうることを正直に記録する。
- 事前見積り: 出力は予測不能なので、上限 = そのティアの `max_tokens_for_model(model)`、
  下限 = 0 とし、レンジの幅として表現する（機能別の実測分布が溜まったら中央値で
  絞り込む — Phase 3）。

### 4.5 事前見積りの公開関数

```python
def estimate_messages_tokens(messages, *, model=None, schema=None) -> TokenEstimate
def estimate_vision_tokens(prompt, image_dims: list[tuple[int,int] | None]) -> TokenEstimate
def estimate_document_run(session, document_id, *, analyze_images=False) -> RunEstimate
```

`estimate_document_run` は対象ドキュメントのチャンク数・総文字数・図数
（`document_figures`）と、パイプライン各ステージの「入力に含める素材」の既知構成から
ステージ別レンジを合算する（LLM を呼ばない・DB 読みのみ）。`analyze_images=true` の場合は
`APPARATUS_MAX_IMAGES_PER_DOCUMENT` を上限とした vision 分を加算する。

## 5. DB（migration 043 `llm_usage_events`）

```sql
-- backend/db/043_llm_usage_events.sql
CREATE TABLE IF NOT EXISTS llm_usage_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN
        ('chat','structured','embedding','vision','transcribe','tts')),
    feature TEXT NOT NULL DEFAULT 'unattributed',
    usage_source TEXT NOT NULL CHECK (usage_source IN
        ('reported','estimated_tokenizer','estimated_heuristic')),
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    cached_tokens INTEGER,
    reasoning_tokens INTEGER,
    input_characters INTEGER,
    output_characters INTEGER,
    image_count INTEGER NOT NULL DEFAULT 0,
    audio_bytes BIGINT,
    tts_characters INTEGER,
    duration_ms INTEGER,
    success BOOLEAN NOT NULL DEFAULT TRUE,
    error_type TEXT,
    user_id UUID,
    document_id UUID,
    course_id TEXT,
    run_id UUID,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_events_occurred
    ON llm_usage_events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_feature
    ON llm_usage_events (feature, occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_model
    ON llm_usage_events (model, occurred_at);
CREATE INDEX IF NOT EXISTS idx_llm_usage_events_document
    ON llm_usage_events (document_id) WHERE document_id IS NOT NULL;
```

設計判断:

- **FK を張らない**: users / documents / learning_courses への外部キーは意図的に持たない。
  テレメトリ行が本体の削除を妨げたり、本体削除でカスケード消失したりしないため
  （帰属 ID は文字列として残す）。
- **コスト（金額）列を持たない**: 単価は変動するため、金額は集計時に価格表（U7）で
  都度換算する。DB には資源量（トークン・文字・バイト）だけを置く。
- **日次集計はビューで**: 専用カウンタテーブルは作らない（DX-2 と同じ立場）。必要なら
  `llm_usage_daily`（day × feature × model × usage_source の SUM）を同 migration に
  `CREATE OR REPLACE VIEW` で置く。

## 6. 帰属（attribution）— contextvars による feature 伝搬

呼び出し側のシグネチャを変えずに「どの機能の呼び出しか」を伝えるため、
`context.py` に contextvar ベースのコンテキストマネージャを置く:

```python
from core.llm_usage.context import usage_context

with usage_context(feature="pipeline:apparatus_semantics",
                   document_id=doc_id, run_id=run_id):
    result = agent.run(...)   # この中の core.llm 呼び出しはすべてこの文脈で記録される
```

- ネスト時は内側が勝つ。未設定時は `feature='unattributed'` で記録する
  （**記録自体は fail-open** — 帰属不明でも消費量は落とさない。U1/P4）。
- `contextvars` はスレッドをまたがない。パイプライン orchestrator や各 worker が
  スレッドを起こす場合は、スレッド起動側で文脈を取得しスレッド内で再セットする
  （`copy_context()`）。v1 では文脈セット地点を worker 本体に置くことでこの問題を回避する。

### 6.1 文脈セット地点と feature 語彙（v1）

| セット地点 | feature 語彙 |
|---|---|
| `core/document_pipeline/orchestrator.py`（ステージ実行部・1箇所） | `pipeline:{stage_key}`（例 `pipeline:claim_qualification`, `pipeline:apparatus_semantics`）+ `document_id` / `run_id` |
| `core/chat.py` RAG チャット | `learning:chat`（casual は `learning:chat_casual`）+ `user_id` / `course_id` |
| `main.py` コースビルダー chat | `admin:course_builder` |
| 原稿スタジオ rewrite | `admin:lecture_rewrite` |
| `core/component_candidates.py` | `admin:component_candidates` |
| `core/tension/worker.py` | `learning:tension` |
| `core/structure_anchor/worker.py` | `learning:structure_anchor` |
| `core/admin_assistant/`（chat 経路） | `admin:assistant` |
| `core/reconstruction/worker.py` | `admin:reconstruction_authoring` |
| `core/doubt/scope_candidates/` ほか | `doubt:scope_candidates` / `doubt:assumption_normalize` |
| `routes/atlas.py` 骨格生成 | `admin:atlas_skeleton` |
| `core/extractor.py` | `pipeline:extractor` |
| `core/embedder.py` ほか埋め込み呼び出し | `embedding:{用途}`（`embedding:chunks` / `embedding:library_search` 等） |
| 音声（voice transcribe / speak, lecture TTS） | `learning:voice_stt` / `learning:voice_tts` / `admin:lecture_tts` |

語彙の正本は `schema.py` の定数リスト。**未知の feature を拒否はしない**（CHECK 制約は
かけない — 新機能追加時に計測が壊れる方が害が大きい）が、ガードレールテストで
「主要な文脈セット地点が語彙表に載っていること」を検査する。

## 7. API（`backend/api/routes/llm_usage.py`、実パス `/api/admin/llm-usage/...`）

### 7-1 集計（SYSTEM_ADMIN のみ）

```
GET /api/admin/llm-usage/metrics?from=2026-07-01&to=2026-07-11&group_by=feature
```

- `group_by ∈ {day, feature, model, provider, operation}`（複合可: `day,feature`）
- レスポンスは **usage_source 別に分離**（U1）:

```json
{
  "from": "2026-07-01", "to": "2026-07-11",
  "rows": [
    {"key": {"feature": "pipeline:claim_qualification"},
     "reported":  {"prompt_tokens": 1843201, "completion_tokens": 220148, "calls": 412},
     "estimated": {"prompt_tokens": 12030,  "completion_tokens": 4400,   "calls": 6},
     "cost_usd": 14.21}
  ],
  "dropped_events": 0,
  "price_table_loaded": true
}
```

- `cost_usd` は価格表未設定なら `null`（U7）。`dropped_events` を必ず含める（U8）。
- ダッシュボード UI は v1 では作らない（DX-2 と同じ判断。JSON を直接見るか将来の管理タブ）。

### 7-2 事前見積り（TEACHER 以上・レンジのみ）

```
GET /api/admin/llm-usage/estimate/documents/{document_id}?analyze_images=true
```

- `_ensure_document_viewable` を通す（L層の図 API と同じ権限ゲート）。
- レスポンスはレンジ + ステージ別内訳。点推定・金額は返さない
  （金額換算は SYSTEM_ADMIN の metrics のみ）:

```json
{
  "document_id": "...",
  "total_tokens_range": [180000, 420000],
  "stages": [
    {"stage": "claim_qualification", "tokens_range": [40000, 90000]},
    {"stage": "apparatus_semantics", "tokens_range": [12000, 46000],
     "note": "figures=8, capped_at=APPARATUS_MAX_IMAGES_PER_DOCUMENT"}
  ],
  "usage_source": "estimated_heuristic"
}
```

- 利用想定: L層の `analyze_images` 有効化モーダルや再解析確認ダイアログに
  「およそ◯〜◯トークン」の目安として併記する（フロント実装は Phase 2）。

### 7-3 run 単位の実測内訳（Phase 2）

```
GET /api/admin/documents/{document_id}/analysis-runs/{run_id}/llm-usage
```

`run_id` 帰属の実測行を stage 別に集計して返す（`_ensure_document_viewable`）。
事前見積り（7-2）と実測（7-3）を突き合わせることで推計係数の較正材料になる。

## 8. 既存コスト上限との関係

- 既存の回数上限（`*_MAX_CALLS_PER_*`）は**変更しない**。台帳は観測レイヤーであり、
  v1 では enforcement（トークン予算による呼び出し拒否）を行わない。
- 将来トークンベースの予算制に移行する場合も、判断材料は本台帳の実測分布とする
  （根拠なく上限値を決めない）。enforcement は別 issue（§12）。

## 9. 設定（環境変数, `.env.example` に追記）

| 変数 | 既定 | 意味 |
|---|---|---|
| `LLM_USAGE_TRACKING_ENABLED` | `true` | false で record() を no-op に |
| `LLM_USAGE_BUFFER_MAX` | `1000` | in-memory バッファ上限（超過は dropped に計上） |
| `LLM_USAGE_FLUSH_INTERVAL_SECONDS` | `10` | flusher の周期 |
| `LLM_USAGE_FLUSH_BATCH` | `100` | この件数到達で即時 flush |
| `LLM_PRICE_TABLE_PATH` | （空） | モデル単価 JSON のパス。空なら cost_usd=null |

価格表 JSON の形式（1M トークンあたり USD。モデル名は前方一致で解決）:

```json
{
  "gpt-5.2":      {"input": 1.25, "cached_input": 0.125, "output": 10.0},
  "gpt-5.4-nano": {"input": 0.05, "cached_input": 0.005, "output": 0.4},
  "text-embedding-3-large": {"input": 0.13}
}
```

## 10. ガードレール（`backend/tests/test_llm_usage_guardrails.py`）

構造的に守る項目（既存ガードレールテストと同型）:

1. **recorder は例外を漏らさない** — `record()` 内部で強制的に例外を起こしても
   `generate_text` 相当のフローが成功する（U2）。
2. **core/llm_usage が FastAPI を import しない**（AST / import 走査）。
3. **削除 API 不在** — `routes/llm_usage.py` に DELETE メソッドのハンドラが存在しない（U6）。
4. **metrics は SYSTEM_ADMIN のみ** — TEACHER トークンで 403（fail-closed）。
   estimate は TEACHER 可・STUDENT 403。
5. **reported / estimated の分離** — metrics レスポンスに両者を合算した単一フィールドが
   存在しない（U1）。
6. **estimate API はレンジのみ** — レスポンスに点推定フィールド・`cost_usd` が無い（U5）。
7. **価格ハードコード禁止** — `core/llm_usage/`・`core/llm.py` のソースに USD 単価と
   解釈できる定数テーブルが無い（価格表は外部 JSON のみ。U7）。
8. **学習者 API 非漏洩** — `routes/learning.py` のレスポンスモデルに
   token / usage 系フィールドが増えていない（U5）。
9. **estimator の決定性** — 同一入力で常に同一値（乱数・時刻非依存）。
10. **（追加）migration DDL の整合** — `db/043_llm_usage_events.sql`（正本）が期待する
    テーブル / index / view / CHECK 語彙を定義し、`core/models.py` の ORM とカラム集合が一致。
11. **（追加 2026-08）計測点の一元化（U3/U4）** — `src/episteme_graph/agents/` 配下に
    provider SDK のエントリポイント（`chat.completions` / `GenerativeModel` / `vertexai` /
    `import openai` 等）と `core/llm.py` の private プロバイダヘルパー
    （`_get_openai_client` / `_get_gemini_module` / `_get_vertex_ai_client` …）が
    **1 箇所も出現しない**こと。併せて `generate_json_with_image` が成功・失敗の両経路で
    `observe_vision` を呼び、provider 分岐すべてがその try/except の内側にあること。

## 11. 段階導入

- **Phase 0（DB 不要・即日）**: `estimator.py` + 単体テスト。事前見積りロジックだけ先に
  使える状態にする（`estimate_messages_tokens` / `estimate_document_run` は Phase 0 では
  messages 版のみ）。
- **Phase 1（台帳の本体）**: migration 043 + `schema.py` / `context.py` / `recorder.py` +
  `core/llm.py`・`core/tts.py` フック + orchestrator / chat / 各 worker への
  `usage_context` セット + `GET /api/admin/llm-usage/metrics` + ガードレール。
- **Phase 2（見積りと突合）**: `estimate_document_run` の DB 読み実装 +
  `GET .../estimate/documents/{id}` + run 単位内訳 API（7-3）+ L層モーダルへの目安表示。
- **Phase 3（較正・任意）**: 実測分布に基づく推計係数・出力中央値の較正、管理タブでの
  簡易可視化、トークンベース予算の検討（別 issue 起票）。

## 12. 非スコープ / 決定事項

- **学習者向け表示は一切しない**（U5。決定）。
- **enforcement（トークン予算による呼び出し拒否・スロットリング）は v1 非スコープ** —
  観測が先、制御は実測が溜まってから。
- **ストリーミング応答の usage 計測は非スコープ** — 現行 `core/llm.py` は非ストリーミング
  のみ。ストリーミング導入時に `stream_options={"include_usage": true}` 対応を追加する。
- **プロバイダ請求書（billing API）との自動突合は非スコープ** — 台帳は概算の把握が目的。
  会計突合はしない。
- **transcribe の秒数算出は非スコープ**（バイト数まで。音声デコードを増やさない）。
- **`tiktoken` は必須依存にしない**（optional。無くてもヒューリスティックで動く。決定）。
- **保持期間 aging は非スコープ** — 行削除 API は作らない方針（U6）のため、将来必要に
  なった場合も「運用ジョブによる期間一括アーカイブ」として別 issue で扱う。
- **既存の回数上限カウンタの置き換えはしない**（§8。決定）。
