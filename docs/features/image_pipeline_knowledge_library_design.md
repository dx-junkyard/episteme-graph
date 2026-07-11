# 画像読み取りパイプライン + 分野別ナレッジライブラリ（L層）設計書（migration 041 / 042）

物理の実験機器の設計図・装置図など **PDF 内の画像** を解析パイプラインに取り込み、
「この画像はこういう装置を表す」という **分野別のナレッジライブラリ** を参照しながら
装置・構成パーツを候補として抽出できるようにする。あわせて、現在 document 単位に
閉じている理論コンポーネントを分野ごとの **共通ライブラリ（L層）** として蓄積・精錬
できる器を作り、教員コミュニティで知識コンポーネントを育てるループを成立させる。

- 対象: `src/episteme_graph/agents/`（新規 agent 追加のみ・既存 agent 非改変）、
  `backend/core/document_pipeline/orchestrator.py`（新ステージ配線）、
  `backend/core/llm.py`（vision 対応関数の追加）、`backend/core/library/`（新設）、
  `backend/api/routes/admin.py` / `library.py`（新設）、`frontend/public/js/admin.js`
- 非対象（変更しない）: 既存 agent のロジック（`document_structure/parser.py` の
  画像ブロックスキップも触らない — 画像抽出は新ステージが独自に PDF を読む）、
  B/C/D/G/R/V 層のコード、学習者向け UI（v1 は教員向け機能のみ）

---

## 0. 背景 — 現状

### 0-1. 画像はパイプラインに一切入っていない

- `document_structure/parser.py:74` が PyMuPDF の画像ブロックを明示的に捨てており、
  画像バイナリの抽出・MinIO 保存・vision LLM 呼び出しはリポジトリのどこにも存在しない。
- `FigureTableSemanticsAgent` は caption 文字列 + 近傍本文テキストのみで図表を意味付け
  する caption-first 設計。LLM enricher フック（`agent.py:34-41`）はあるが未配線。
- `backend/core/llm.py` に画像入力（マルチモーダル）関数は無い。

### 0-2. アップロード時のオプション伝搬経路が無い

- フロントは `FormData` に `file` のみを積み（`admin.js:910`）、
  `upload_material`（`admin.py:267`）は `cartridge_id=None` 固定でパイプラインを呼ぶ。
- `document_analysis_runs` にオプション用の列は無い（`stage_outputs JSONB` のみ）。

### 0-3. コンポーネントは論文単位に閉じている

- `theory_components` は migration 015 で `document_id` に紐づき、共有も document 単位
  （migration 035）。**分野を跨いだ共通知識が住む場所が無い。**
- カートリッジ（`backend/cartridges/<id>/`）は読み取り専用のファイル固定で、
  運用中に育てられない。唯一 DB 正本化されているのは Field Atlas の骨格
  （migration 027 `atlas_skeletons` + `core/atlas_store.py`）で、
  「同梱ファイルをシードとして冪等取込 → DB 正本 → 楽観ロック draft → 凍結版履歴」
  という再利用可能なテンプレートになっている。本設計はこのパターンを踏襲する。

### 0-4. 決定済み事項（2026-07-11 合意）

1. **昇格時の例示画像は既定で含めない**（テキスト記述のみ昇格。画像の含有は
   元 document 所有者による明示オプトイン。§6-4）。
2. **非LLM の画像抽出ステージは常時実行**（チェックボックスは vision 工程のみを制御）。
3. **ライブラリは汎用の器を先に作り、apparatus（実験装置）を最初のエントリ型として入れる。**

---

## 1. 設計原則（不変条項）

1. **既存 agent 非改変**: 新工程は独立モジュール（新ステージ + 新 agent）として追加する。
   `parser.py` の画像スキップ、`FigureTableSemanticsAgent` のロジックは変更しない。
2. **画像の意味解釈は candidate 止まり、確定は人間**: vision LLM の出力
   （装置同定・パーツ分解）は常に `review_status='review_required'` 系で保持し、
   教員の確定なしに source_backed / ライブラリ入りしない。
   **LLM がライブラリへ直接書き込む経路を作らない**（昇格 API は人間の操作のみ）。
3. **情報を落とさない (P4)**: 同定できない装置は `match_status='unknown'` で保持。
   チェックボックス off でスキップした工程は `skipped_by_option: true` を
   `stage_outputs` に正直に記録する（無言スキップ・エラー化のどちらもしない）。
   ライブラリエントリの削除は行わず `status='retired'` 遷移で保持する。
4. **evidence-based**: 装置候補・パーツ・接続の各出力に `evidence_quote`（caption 等の
   逐語）/ `reason` / `confidence` / `source_backing_status` を必ず付与する
   （TheoryOperationGraph と同じ語彙: `source_backed / partially_source_backed /
   inferred / review_required`）。
5. **domain-independent**: 装置語彙（「真空チャンバー」「シンチレータ」等）を agent に
   ハードコードしない。語彙はカートリッジ（`component_types.json` 拡張）と
   ライブラリ凍結版から読む。ライブラリが空でも agent は単独動作する（縮退: 同定なしの
   `unknown` 記述のみ出力）。
6. **コスト fail-closed**: vision 呼び出しはアップロード時チェックボックスの明示
   オプトイン + 環境変数の上限（§10）で二重に守る。自動有効化はしない。
7. **画像の権利 fail-closed**: 抽出画像は元 document の権限（所有者 / visibility /
   `document_group_permissions`）を継承し、閲覧できない者に配信しない。
   ライブラリ昇格は既定でテキストのみ。例示画像の含有は**元 document の所有者だけが**
   明示確認を経て実行でき、含有 = 教員全体への開示許諾とみなす（§6-4）。
8. **ファイルはシード・DB が正本**: ライブラリはカートリッジ同梱シードを起動時に
   冪等取込し、以後 DB を正本とする（atlas_skeletons パターン。§6-3）。
   パイプラインが参照するのは**凍結版のみ**（draft を解析に使わない）。

---

## 2. 全体像 — 精錬ループ

```
教材アップロード（☑ 図面・画像を解析する）
    │
    ├─ figure_image_extraction（非LLM・常時実行）
    │      PDF → 画像抽出 → MinIO + document_figures
    │
    └─ apparatus_semantics（vision LLM・☑ のときのみ）
           画像 + caption + 近傍本文
           + ライブラリ凍結版から retrieve した装置知識（few-shot）  ←──┐
              ↓                                                        │
           装置・パーツ候補（candidate / review_required）              │
              ↓ 教員レビュー・確定                                      │
           「ライブラリへ昇格」（人間の操作のみ）                        │
              ↓                                                        │
        分野別ナレッジライブラリ（L層: draft → 凍結版）  ──────────────┘
        （教員共同財として編集・凍結・監査。次の解析が賢くなる）
```

3 つの部品（オプトイン配線 / 画像 2 ステージ / L層ライブラリ）は独立して価値を持ち、
揃うと「解析 → 教員確定 → ライブラリ → 次の解析」の精錬ループが閉じる。

---

## 3. アップロードオプション（チェックボックス → options）

### 3-1. UI（admin.html / admin.js）

- アップロードゾーン（`admin.html` `#upload-zone` の section）にチェックボックスを追加:
  `「図面・画像を解析する（装置図の同定に vision AI を使用）」`。既定 **off**。
- `uploadFile()`（`admin.js:900`）で `formData.append('analyze_images', 'true'|'false')`。
- 再解析ボタンのフロー（教材詳細）にも同じチェックボックスを出す。

### 3-2. API と保存（migration 041）

- `upload_material` に `analyze_images: bool = Form(False)` を追加し、
  `process_material_background` → `run_document_pipeline(..., options={...})` へ受け渡す。
  `reanalyze_document` も body で同フラグを受ける。
- `document_analysis_runs` に **`options JSONB NOT NULL DEFAULT '{}'`** 列を追加
  （migration 041）。`upsert_analysis_run` が run 作成時に保存する。
  `stage_outputs` への相乗りはしない（resume 用 artifact と意味が混ざるため）。
  run ごとに「このときは画像解析あり/なし」が残り、再現性・監査に使える。

### 3-3. ステージスキップの意味論

- `analyze_images` が false のとき `apparatus_semantics` ステージは実行せず、
  空結果 + `{"skipped_by_option": true}` を `stage_outputs` に記録して次へ進む
  （既存の figure_table_semantics 非致命フォールバックと同じ流儀）。
- `figure_image_extraction` はフラグに関係なく**常時実行**（決定 0-4-2）。
  コストはストレージのみで、抽出画像は将来の図表示・再解析にも使える。

---

## 4. ステージ1: `figure_image_extraction`（非LLM・決定論的・常時実行）

### 4-1. パイプライン上の位置と実装場所

- `PIPELINE_STAGES` の `document_structure` の直後に挿入
  （caption ブロックの分類結果を使うため）。
- 実装は `backend/core/document_pipeline/figure_images.py`（新設・非LLM）。
  agent ディレクトリは作らない（LLM を使わない決定論的工程のため。
  evidence_registry / derivation_chain と同じ扱い）。

### 4-2. 抽出方式 — 埋め込み画像 + 領域レンダリングの2段構え

物理の装置図・設計図は**ベクター描画**が多く、埋め込み画像抽出だけでは取れないため:

1. **埋め込み画像**: PyMuPDF `page.get_images()` + `doc.extract_image(xref)` で
   XObject を抽出（xref で重複排除）。ページ内の配置 bbox を取得し caption と対応付ける。
2. **領域レンダリング（fallback）**: `figure_caption` ブロックの近傍に埋め込み画像が
   見つからない場合、caption の bbox から図領域を推定（caption 直上〜前のテキスト
   ブロック/ページ余白まで・カラム幅）し、`page.get_pixmap(clip=...)` で PNG に
   ラスタライズする。`extraction_method='region_render'` として区別し、
   領域推定の確からしさを `region_confidence` に残す。

caption との対応付けは **page + bbox 近接**（caption の直上優先）。対応が取れない画像も
捨てずに `caption_block_id=NULL` で保存する（P4）。

### 4-3. 保存先

- **MinIO**: 新バケット `figure-images`、キーは `figures/{document_id}/{figure_id}.png`。
- **PostgreSQL**（migration 041）: 図画像レジストリ `document_figures`

```sql
CREATE TABLE document_figures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id TEXT NOT NULL,
    run_id UUID,                        -- 抽出した解析 run
    figure_key TEXT NOT NULL,           -- 正規化ラベル（例 'fig_3'）または 'p{page}_i{n}'
    figure_label TEXT,                  -- caption 由来の表示ラベル（'Figure 3' 等）
    page INT,
    bbox JSONB,
    caption_block_id TEXT,
    caption_text TEXT,
    minio_key TEXT NOT NULL,
    extraction_method TEXT NOT NULL CHECK (extraction_method IN ('embedded','region_render')),
    region_confidence REAL,
    status TEXT NOT NULL DEFAULT 'extracted' CHECK (status IN ('extracted','failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(document_id, figure_key)     -- 再解析は upsert（画像は最新 run で置換）
);
```

- 画像配信 API は `GET /api/admin/documents/{document_id}/figures` /
  `GET .../figures/{figure_id}/image`（`_ensure_document_viewable` を必ず通す。原則 7）。

### 4-4. 失敗時

ステージ全体は非致命。PDF 破損・レンダリング失敗は figure 単位で `status='failed'` を
記録し、成功分だけで次ステージへ進む。

---

## 5. ステージ2: `apparatus_semantics`（vision LLM・チェックボックス配下）

### 5-1. パイプライン上の位置と agent 構成

- `PIPELINE_STAGES` の `figure_table_semantics` の直後・`thesis_reconstruction` の前に
  挿入（FigureRecord と caption 意味付けを入力に使い、出力を component_assembly が
  下流で消費できる位置）。
- 実装は `src/episteme_graph/agents/apparatus_semantics/` に**標準ファイルセット**で新設:
  `agent.py` / `cartridge_loader.py` / `input_builder.py` / `prompt.py` /
  `llm_client.py` / `schema.py` / `validator.py` / `repair.py` / `examples/`。
  LLM-first（同定・パーツ分解の高次判断は LLM、入力整形・validation・repair は非LLM）。

### 5-2. 入力と出力スキーマ

**入力**（`input_builder.py` が組み立て）: 図ごとに
- `document_figures` の画像（MinIO から取得）
- caption + `FigureTableSemanticsAgent` の FigureRecord（figure_type 等）
- caption 近傍の body_paragraph（radius=3、figure_table_semantics と同じ流儀）
- **ライブラリ retrieval 結果**（§5-3。0 件でも動作する）

**出力** `ApparatusSemanticsResult`（`schema.py`、JSON シリアライズ可能な dataclass）:

```
apparatus_records: [
  {
    figure_id, figure_key,
    apparatus_name_candidate,            # LLM の同定候補（自由記述可）
    matched_library_entry_id,            # retrieval 候補に同定できた場合のみ
    matched_library_version_no,
    match_status,                        # 'matched' | 'novel' | 'unknown'
    parts: [ { name, role, evidence_quote, reason, confidence } ],
    connections: [ { from_part, to_part, relation, reason, confidence } ],
    evidence_quote,                      # caption 等の逐語
    reason, confidence,
    source_backing_status,               # 語彙は TheoryOperationGraph と共通
    review_status                        # 常に review_required 系（原則 2）
  }
],
validation_issues: [...]
```

- validator は語彙・必須フィールド・推量できない断定を検査し、2 回修復失敗した図は
  `match_status='unknown'` / `confidence=0.0` / `payload.repair_failed=true` で 1 レコード
  保持する（tension と同じ縮退規約）。

### 5-3. ライブラリ retrieval の注入（画像埋め込みモデルを導入しない）

1. **事前検索（非 vision・安い）**: caption + 近傍本文のテキストを
   `text-embedding-3-large` で埋め込み、当該 run の `domain_key`（= cartridge_id）に
   属するライブラリ**凍結版**の apparatus エントリを pgvector cosine で top-k
   （既定 5）検索する。
2. **vision 1 コール**: 画像 + 候補エントリの `name / typical_parts / visual_cues`
   （テキスト記述）を渡し、「候補のどれに該当するか・該当しないなら novel/unknown」
   「構成パーツと接続」を structured output で答えさせる。既定は **1 図 = 1 コール**。
3. **例示画像の few-shot は任意**: `APPARATUS_FEWSHOT_IMAGES=true` のときのみ、
   含有承認済み（§6-4）の例示画像を候補ごと最大 1 枚添付する。既定 off（コスト・
   権利の両面で保守的に）。

ライブラリが空・retrieval 0 件のときは候補なしで vision を呼び、出力は
`match_status ∈ {novel, unknown}` に縮退する（原則 5）。
参照したエントリ版は `stage_outputs` に `referenced_library_versions` として記録する
（どの知識で解析したかの再現性）。

### 5-4. `core/llm.py` の vision 対応

- 新関数 `generate_structured_with_images(prompt, images, schema, model=None)` を追加。
  `images` は bytes（MinIO 取得物）を base64 で `image_url` パーツ化。
- v1 は **OpenAI 経路のみ**対応（既定モデル `gpt-4o`、`APPARATUS_LLM_MODEL` で上書き）。
  他プロバイダは `NotImplementedError` で明示。
- 開発ルール 4 を踏襲（system ロール・temperature/max_tokens を避ける）。

### 5-5. 下流接続

- `ComponentAssemblyAgent` の入力に `ApparatusSemanticsResult` を追加し、装置候補を
  `theory_components` の候補（`status='candidate'`）として組み立てる。
- **component_type 語彙の拡張**: `theory_components.component_type` の CHECK 制約
  （migration 013: theory/concept/law/mechanism/operator/observation）に
  `apparatus` / `instrument` / `part` を追加（migration 041）。カートリッジ側は
  `component_types.json` に同語彙を追記する（agent へのハードコード禁止。原則 5）。
- **TheoryOperationGraph には組み込まない**（v1）。装置には式 backing が無いため、
  main/equation_detail 層の語彙を汚さない。graph への装置レイヤー追加は将来課題。

---

## 6. ナレッジライブラリ（L層）

### 6-1. 位置づけと共有ガバナンス

- L層は「**分野ごとの教員共同財**」。document に縛られない知識エントリ
  （まず apparatus、次いで theory_component）を、教員コミュニティで編集・凍結・参照する。
- **閲覧・編集は TEACHER 以上の全員**（wiki 型）。draft 編集は楽観ロックで衝突検知し、
  全書き込みを監査する（§11）。グループ限定ライブラリは v1 では作らない
  （限定共有の需要が出たら migration 035 同型で追加する。§13）。
  学習者への開示は v1 スコープ外。
- **例外は例示画像**（原則 7）: エントリ本文（教員が書き直した記述）は教員全体に開示
  できるが、元 PDF 由来の画像は元 document の権限を継承する。ライブラリに含まれた
  画像 = 所有者が教員全体への開示を許諾したもの、のみ。

### 6-2. データモデル（migration 042）

実装は `backend/core/library/`（`store.py` / `schema.py` / `seed.py` / `search.py`。
FastAPI 非 import — `core/` 共通ルール）。

```sql
-- エントリ本体（draft が正本、atlas_skeletons パターン）
CREATE TABLE library_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain_key TEXT NOT NULL,            -- cartridge_id と同一名前空間（atlas と同じ）
    entry_type TEXT NOT NULL CHECK (entry_type IN ('apparatus','theory_component')),
    name TEXT NOT NULL,
    aliases JSONB NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    body JSONB NOT NULL DEFAULT '{}',    -- 型別ペイロード（下記）
    exemplar_images JSONB NOT NULL DEFAULT '[]',  -- §6-4 の含有承認済み参照のみ
    source_component_ids JSONB NOT NULL DEFAULT '[]',   -- provenance（複数可 = 統合）
    source_document_ids JSONB NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    revision INT NOT NULL DEFAULT 1,     -- 楽観ロック
    latest_version_no INT NOT NULL DEFAULT 0,
    created_by TEXT, updated_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_library_entries_domain ON library_entries(domain_key, entry_type, status);

-- 凍結版（不変・履歴保持。パイプラインはここだけを読む）
CREATE TABLE library_entry_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entry_id UUID NOT NULL REFERENCES library_entries(id) ON DELETE CASCADE,
    version_no INT NOT NULL,
    content JSONB NOT NULL,              -- 凍結時点のエントリ全体スナップショット
    embedding vector(3072),              -- name+aliases+summary+visual_cues から凍結時に計算
    note TEXT,
    published_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(entry_id, version_no)
);
```

`body` の型別ペイロード:
- `apparatus`: `typical_parts`（パーツ名・役割）/ `visual_cues`（見た目の特徴の
  テキスト記述。「円筒形で放射状にフランジ」等 — retrieval と vision プロンプトの主材料）/
  `typical_configurations` / `measurement_targets`
- `theory_component`: `theory_components` 行のスナップショット
  （inputs/outputs/preconditions/constraints/dependencies 等）

### 6-3. draft / 凍結 / シード（atlas_skeletons パターンの踏襲）

- **draft 編集**: `PUT` に `expected_revision` を必須で付け、`revision` 照合
  UPDATE（`rowcount != 1` → 409。フロントはリロードを促す）。
- **凍結（バージョン発行）**: `latest_version_no + 1` を採番して
  `library_entry_versions` に append し、embedding を計算。凍結は取り消さない
  （修正は次版として発行）。
- **シード**: `backend/cartridges/<id>/library/*.json`（任意）を起動時に
  `import_bundled_library()` で「無い (domain_key, name, entry_type) のみ」冪等取込。
  カートリッジファイルの無い新分野でも DB だけでライブラリが成立する
  （atlas の `domain_key` と同じ流儀。デプロイ不要で新分野を育てられる）。
- **削除しない**: `status='retired'` 遷移のみ（P4）。retired エントリは retrieval
  対象から外れるが、履歴・provenance・監査は残る。

### 6-4. 昇格フロー（人間の操作のみ）

- **起点**: (a) 解析結果の装置候補（`apparatus_records`）、(b) 既存の
  `theory_components` 行、(c) 白紙から手動作成 — の 3 経路。いずれも管理画面の
  「ライブラリへ昇格」操作で、**LLM が自動昇格する経路は存在しない**（原則 2）。
- **昇格モーダル**: domain_key（既定 = 解析 run の cartridge_id）、entry_type、
  name/summary/body の編集（教員が自分の言葉に書き直すことを推奨する文言を添える）、
  類似既存エントリの提示（embedding 検索。新規作成 or 既存エントリへの
  provenance 追記＝統合を選べる）。
- **例示画像（決定 0-4-1）**: 既定は**含めない**（テキスト記述のみ昇格）。
  「例示画像を含める」チェックは**元 document の所有者にのみ**表示し、オンにすると
  確認文（「この画像はライブラリを閲覧できる全教員に表示されます」）を経て
  `exemplar_images` に `{figure_id, minio_key, source_document_id, approved_by,
  approved_at}` を記録する。所有者以外が昇格する場合、画像の選択肢自体を出さない
  （fail-closed）。
- provenance（`source_component_ids` / `source_document_ids`）は昇格時に必ず記録。
  既存の `duplicate_candidates` 列は統合候補の提示ヒントに使う。

### 6-5. パイプラインからの参照

- retrieval（§5-3）は **各エントリの最新凍結版のみ**を読む（draft は解析に使わない。
  原則 8）。domain_key は解析 run の cartridge_id と突合する。
- 解析 run は参照したエントリ版を `stage_outputs.referenced_library_versions` に記録
  し、後から「どの知識状態で解析したか」を追える（V層の Release ピンと同じ思想。
  V層のテーブル自体は流用しない — ライブラリは版を自前で持つため）。

### 6-6. CartridgeContext への合成（Phase 3）

- `CartridgeContext.extraction_hints`（既存の Optional フィールド）に、DB ライブラリ
  凍結版由来の語彙・エントリ要約を合成して agent に渡す口を
  `cartridge_paths.py` 系の共通ヘルパに追加する。カートリッジ＝静的な骨格、
  ライブラリ＝動的に育つ知識、という役割分担。apparatus_semantics 以外の agent
  （component_assembly 等）が分野語彙を利用できるようになる。
  v1 では apparatus_semantics の retrieval 注入（§5-3）のみ実装し、
  汎用合成は Phase 3 とする。

---

## 7. API 一覧

**図画像（migration 041、`routes/admin.py` 追記）** — すべて
`_ensure_document_viewable` を通す:
- `GET /api/admin/documents/{document_id}/figures` — 図一覧（caption・抽出方式つき）
- `GET /api/admin/documents/{document_id}/figures/{figure_id}/image` — 画像配信

**アップロード拡張**:
- `POST /admin/materials/upload` — `analyze_images: bool = Form(False)` 追加
- `POST /admin/documents/{document_id}/reanalyze` — body に `analyze_images?` 追加

**ライブラリ（`backend/api/routes/library.py` 新設、実パス `/api/admin/library/...`、
`_require_teacher`）**:
- `GET /entries?domain_key=&entry_type=&q=&include_retired=` — 一覧・検索
- `POST /entries` — 作成（昇格・手動。source refs / 画像含有は §6-4 の制約で検証）
- `GET /entries/{id}` / `GET /entries/{id}/versions` — 取得・版履歴
- `PUT /entries/{id}` — draft 編集（`expected_revision` 必須、衝突 409）
- `POST /entries/{id}/freeze` — 凍結版発行（note 任意）
- `POST /entries/{id}/retire` / `POST /entries/{id}/restore` — 状態遷移（行削除 API は無い）
- `GET /domains` — domain 別エントリ数サマリ

---

## 8. DB 変更まとめ

**migration 041（画像パイプライン）** — 実適用は `_run_migrations()`:
1. `document_analysis_runs` に `options JSONB NOT NULL DEFAULT '{}'`
2. `document_figures` テーブル（§4-3）
3. `theory_components.component_type` CHECK に `apparatus` / `instrument` / `part` を追加

**migration 042（L層ライブラリ）**:
1. `library_entries` / `library_entry_versions`（§6-2）

MinIO: 新バケット `figure-images`（起動時 ensure、`raw-papers` 等と同じ流儀）。

---

## 9. フロントエンド（admin.js、ES5）

- **アップロードゾーン**: チェックボックス追加（§3-1）。
- **教材詳細**: 「図・画像」セクション（抽出画像のサムネイル一覧 + caption +
  装置候補があれば candidate バッジ）。装置候補行に「ライブラリへ昇格」ボタン。
- **ナレッジライブラリタブ**（新設）: domain 選択 → エントリ一覧（entry_type /
  status フィルタ）→ 詳細ペイン（body 編集・版履歴・凍結ボタン・provenance 表示・
  例示画像は権限がある場合のみ表示）。編集保存は `expected_revision` を送り、
  409 なら「他の教員が更新しました。再読込してください」を表示。
- 学習者画面（app.js）は変更しない（v1）。

---

## 10. コスト・環境変数

| 変数 | 既定 | 意味 |
|---|---|---|
| `APPARATUS_LLM_MODEL` | `gpt-4o` | vision 同定モデル |
| `APPARATUS_MAX_IMAGES_PER_DOCUMENT` | 20 | 1 document あたり vision 対象にする図の上限（超過分は `skipped_by_limit` で記録、P4） |
| `APPARATUS_MAX_CALLS_PER_DAY` | 30 | vision 呼び出しの日次上限（他機能と独立） |
| `APPARATUS_FEWSHOT_IMAGES` | `false` | 例示画像の few-shot 添付（含有承認済みのみ） |
| `APPARATUS_RETRIEVAL_TOP_K` | 5 | ライブラリ retrieval の候補数 |

上限到達時は残りの図を `unknown` + `skipped_by_limit` で保持し、ステージは正常完了する。

---

## 11. 監査・ガードレール

**監査**（既存 `_record_review_event` を再利用、`theory_review_events`）:
- `entity_type='library_entry'`: 作成・draft 更新・凍結・retire/restore・画像含有承認
- 昇格元（source_component_ids）と昇格先 entry_id を payload に記録

**ガードレールテスト** `backend/tests/test_image_library_guardrails.py`:
1. LLM がライブラリへ書き込む経路が存在しない（昇格 API は認証済み教員のみ・
   pipeline コードから `core/library` の書き込み関数を import していない）
2. apparatus_semantics の全出力が `review_required` 系で、`source_backed` を
   自動付与しない
3. 例示画像は既定で昇格に含まれない・所有者以外の含有リクエストは 403
4. 図画像配信 API が `_ensure_document_viewable` を通る（権限なしは 404/403）
5. ライブラリに行削除 API が無い（retire のみ）・retired が retrieval に出ない
6. `analyze_images=false` の run に `skipped_by_option` が記録される
7. `backend/core/library/` が FastAPI を import しない
8. retrieval が draft を読まない（凍結版のみ）

---

## 12. 段階的実装

- **Phase 0（配線 + 画像抽出）**: migration 041 / チェックボックス /
  `options JSONB` / `figure_image_extraction`（常時実行）/ 図画像 API・教材詳細の
  図一覧。vision なしでも図の実体が残り、単独で価値がある。
- **Phase 1（vision 同定・ライブラリなし）**: `core/llm.py` vision 関数 /
  `apparatus_semantics` agent（retrieval 0 件縮退で動作）/ component_assembly 接続 /
  カートリッジ `component_types.json` 拡張。出力は novel/unknown 中心の candidate。
- **Phase 2（ライブラリの器 + apparatus）**: migration 042 / `core/library/` /
  ライブラリ API・管理タブ / 昇格フロー（画像含有ゲート込み）/ retrieval 注入 /
  シード取込。精錬ループがここで閉じる。
- **Phase 3（theory_component 型 + 汎用合成）**: theory_components からの昇格 /
  統合（複数論文 provenance）/ `CartridgeContext.extraction_hints` への凍結版合成 /
  C層同型の endorsement（`library_entry_endorsements`、説明単位承認の移植）。

各 Phase は単独でデプロイ可能・後方互換（フラグ off の既存アップロードは挙動不変）。

---

## 13. 非スコープ（v1 では作らない）

- 学習者向けの図・ライブラリ表示（教員向けのみ。開示設計は別 issue）
- TheoryOperationGraph への装置ノード組み込み（式 backing が無いため語彙を汚さない）
- 画像埋め込みモデル（CLIP 等）の導入 — retrieval はテキスト記述の embedding で行う。
  将来足す場合も `library_entry_versions` への列追加で済む構造にしてある
- グループ限定ライブラリ（v1 は教員全体の共同財。需要が出たら migration 035 同型で追加）
- vision の自動有効化（画像枚数による推奨表示を含め、まずは明示チェックボックスのみ）
- 表（table）の画像解析（v1 は figure のみ。table は既存 caption-first を維持）
