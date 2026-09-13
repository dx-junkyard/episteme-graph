# 論文レーダー（教材起点の類似論文探索と比較分析）

> **状態: 実装済み（正本）**（2026-08-28 起票・同日実装。migration **なし** —
> 新テーブル・新列ゼロ。実装記録は §10、出所の後付け登録の追補は §11、
> 重なり・差分提示の追補（2026-08-29）は §12）

**正本**: 本ドキュメント。
**関連**: [論文ディスカバリー層](paper_discovery_design.md)（PD1〜PD8 — 本層はその
教材起点の姉妹機能で、取得・取り込み・候補注釈の全機構を再利用する）/
[URL指定による教材取得](url_material_upload_design.md)（UF1〜UF6 — 取り込み経路）/
[コーパス回遊層](corpus_roaming_design.md)（CR7 — 学習者起点で外部 API を呼ばない）/
[知識ランドスケープ](knowledge_landscape_design.md)（LS5 — 数値非表示の規律）/
[LLM トークン使用量推計](llm_usage_metering_design.md)（U層）/
[場面別 LLM モデル選択](llm_model_selection_design.md)（M層）。

---

## 1. 目的 — 「この論文の周辺」を距離を選んで探す

既存のディスカバリー層（migration 071〜073）は**分野**を単位とした発見を提供する
（分野購読 → arXiv 検索 → 教員の承認 → 既存取り込み経路）。しかし教員の実際の関心は
しばしば**1本の論文**を起点にする — 「この論文と同じ問題を別のアプローチで解いた論文は
ないか」「同じ分野だが全く別のテーマの論文でコーパスの幅を広げたい」。

本層は教材管理の各行に**レーダー（📡）**の入口を追加し、その教材（論文）を起点（seed）に
arXiv から候補を探索する。教員は**距離**を選べる:

| 距離 | 意味 | 想定用途 |
|---|---|---|
| **近い**（`near`） | 分野もテーマも類似。アプローチ・結論が異なる論文が主な収穫 | 比較・反証・手法の異同の教材化 |
| **中間**（`mid`） | 同じ分野で、テーマが部分的に重なる | コーパスの連続的な拡張 |
| **同じ分野の別テーマ**（`far`） | 同じ arXiv カテゴリだがテーマが離れている | 分野の地図の未踏領域を埋める |

さらに候補一覧に対して、教員の明示操作で **AI 比較分析**を実行できる。seed 論文の要旨・
中心命題と各候補のアブストラクトを1回の LLM コールで突き合わせ、「起点論文との共通点 /
違い（アプローチ・結論・対象の異同）」を候補ごとの短い事実文 + 仮説文体で提示する。

分野購読（既存）が「網を張って待つ」発見であるのに対し、本層は「1点から照らす」発見である。
両者は同じ取り込みの弁（PD1）・同じ取得経路（PD2）に合流し、ディスカバリー層の
コーパス成長ループ（発見 → 承認 → 解析 → 地図 → 語彙供給）をそのまま太くする。

---

## 2. 不変条項（PR1〜PR8）

PD1〜PD8 をすべて継承したうえで、本層固有の条項を足す。

| ID | 条項 | 意味 |
|---|---|---|
| **PR1** | **起点は教材1件・候補は読み時導出** | seed の解決（arXiv ID・カテゴリ・要旨・重心）も候補一覧も毎回導出し、保存しない（PD5 継承）。レーダーのための新テーブル・新列を作らない（migration 0）。 |
| **PR2** | **距離は段階ラベルのみ・測れないものにラベルを付けない** | cosine の生値は関数の外へ出さない（PD4 継承）。距離帯ラベルの正本は `core/label_vocab.py`。seed ベクトルが作れない・埋め込みに失敗した候補には距離ラベルを**付けず**、「距離を判定できませんでした」の区画へ正直に分離する（未測定を最遠帯に化けさせない — GradedScale の慎重側フォールバックをここでは使わない）。 |
| **PR3** | **取り込みは既存の弁のみ** | レーダーは候補提示まで。取り込みは既存 `POST /api/admin/discovery/ingest`（≦5・同期）/ `POST /ingest-batch`（キュー）を教員が明示的に叩く（PD1/PD2 継承）。レーダー専用の取得・取り込みエンドポイントを作らない。 |
| **PR4** | **比較文は AI の推定・非保存・出所明示** | 比較分析の出力は候補カード上の一時的な注釈で、DB に保存しない。各違いの記述は候補アブストラクトからの **verbatim `evidence_quote`** を必須とし（不一致はその項目のみ drop）、断定せず仮説文体で書く（W2/W3 同族）。「アブストラクトの比較に基づく AI の推定です。本文は確認されていません」の注意書きは**サーバ側の固定文**として必ず同梱する（LLM に書かせない）。 |
| **PR5** | **教員の明示操作のみ** | 検索も比較分析もボタン押下時だけ実行する。自動検索・自動比較・ポーリング・バッジ・G層ルールなし（PD8 継承）。worker / cron からレーダーを呼ぶ経路を作らない。 |
| **PR6** | **外部 API は既存クライアント経由** | arXiv への到達は `arxiv_client.py` のみ（宛先定数 `export.arxiv.org`・モジュールレベル3秒スロットル — PD7 継承）。本層が追加する ID 指定取得（`id_list`）も同じスロットルを通る。 |
| **PR7** | **閉世界の正直さ** | 候補一覧は「この検索条件で arXiv を検索した結果」であって「この論文に近い論文の全体」ではない。検索条件（query）と `closed_world_note` を常時表示し、fail-soft は事実文で degrade する（PD6 継承）。比較文は「アブストラクトの範囲で言えること」しか言わない。 |
| **PR8** | **教員専用（v1）** | 入口・API とも TEACHER 以上 + **document 可視性ゲート**（seed のチャンク・解析成果を読むため）。学習者向けの表示・API を作らない（学習者起点で外部 API を呼ばない — CR7 と同じ理由）。 |

---

## 3. 全体像 — 距離の実現方式

分野購読の4層のじょうご（網 / 絞り / 並べ替え / 人間の弁）を教材起点に読み替える。

```
第1層【網】     seed のカテゴリで arXiv を検索
                 - near: カテゴリ AND キーフレーズ（seed 由来・編集可）
                 - mid / far: カテゴリのみ（キーフレーズで絞ると「近い」に寄るため）
第2層【帯分け】  seed ベクトル（教材チャンク重心）と候補アブストの cosine を
                 3帯の段階ラベル（近い / 中間 / 遠い）に変換（PR2）
第3層【提示】    選択した距離の帯を展開表示。他の帯は折りたたみで保持（捨てない — PR7）
第4層【人間の弁】教員が候補を選んで既存 ingest へ（PR3）
```

- **「同じ分野の別テーマ」を検索クエリだけで表現しない**。arXiv API にはテーマの否定検索が
  実質無く、キーフレーズの NOT 結合は漏れが大きい。代わりに「カテゴリで網を張り、
  embedding 距離で帯に分ける」— 遠い帯 = 同カテゴリかつ seed から意味的に遠い候補、が
  「同じ分野の別テーマ」の実装になる。
- **帯分けは並べ替え（Phase 3 `order=relevance`）と同じ機構の別投影**である。分野重心の
  代わりに seed 教材の重心を使い、順位の代わりに帯ラベルを付ける。embedding 呼び出しは
  既存どおり `ranking.py` に閉じる（§5.2）。
- 帯の中の並び順: `near` は類似度降順（Phase 3 の relevance 並びと同じ扱い — 生値は
  出さない）、`mid` / `far` は新着順（遠い順に並べるのは疑似精度になるためやらない）。

---

## 4. UI（教材管理タブ）

### 4.1 入口

教材管理「アップロード済み教材」の各行 `⋯` メニュー（`material-more-menu`）に1項目追加:

- **`📡 近い論文を探す…`**（`class="ls-menu-item admin-radar-doc-btn"`、
  `data-ui-anchor="materials.row-radar"`、`data-document-id` / `data-title` を持ち、
  `m.document_id` が無い行には出さない — `materials.row-landscape` と同じガード・同じ
  ハンドラ配線パターン）。配置は `landscapeBtn` の直後。

> **2026-09-06 追補（オーナー指示）**: 入口を `⋯` メニュー項目から**行のアイコンボタン**へ
> 昇格した。`class="admin-action-btn material-row-icon-btn admin-radar-doc-btn"`、アイコンは
> 📡（従来と同じ）、ラベルは `title` / `aria-label`（「近い論文を探す」）。配置は
> 「パイプラインを実行 ▼」→ グラフレビューアイコン → 📡 → `⋯` の順。`data-ui-anchor` /
> ハンドラ / document_id ガードは不変。Copilot 道案内の論理アンカーは
> `paper_radar_row_menu`（`⋯` トリガー点灯）から `paper_radar_row_button`（ボタン直接点灯）に
> 改名。決定の記録は `docs/architecture/admin_ux_issues_2026-08-01.md` §2.3 追補。

### 4.2 モーダル（`frontend/public/js/admin-paper-radar.js`、ES5・`window.PaperRadar`・DI 注入）

`admin-paper-discovery.js` と同じ構造（IIFE + `init(deps)` / `openModal(documentId, title)` /
`close()`。deps = `apiFetch` / `escHtml` / `onUploadAccepted`）。区画:

1. **seed ヘッダ** — 教材タイトル + arXiv ID（`abs_url` リンク）。arXiv ID が引けない教材
   （手動アップロード）は「この教材は arXiv 由来として登録されていないため、カテゴリを
   指定してください」の事実文（PD6 — 判定不能を偽装しない）。
2. **距離セレクタ**（`data-ui-anchor="materials.radar-distance"`）— 3択セグメント
   「近い（テーマも近い） / 中間 / 同じ分野の別テーマ」。既定は「近い」。
3. **検索条件** — カテゴリチップ（seed の arXiv メタデータから自動供給。引けなければ
   seed の分野購読から供給、それも無ければ手入力）+ キーフレーズチップ（**near のみ**表示。
   供給は seed 教材由来 — §5.1。外す/足すは自由・供給元ツールチップ付き — PD3 の流儀）。
   [この条件で検索]（`materials.radar-search`）。**検索条件と `closed_world_note` を候補一覧の
   上に常時表示する**（PR7）。この画面から分野購読は書き換えない（保存ボタンを置かない）。
4. **候補一覧** — 分野購読モーダルの候補カードと同型（タイトル / 著者 / 日付 / カテゴリ /
   一致キーフレーズ / 要旨折りたたみ / 取り込み済みラベル）+ **距離帯チップ**
   （`距離: 近い / 中間 / 遠い`）。選択した距離の帯を展開し、他の帯は「他の距離の候補
   N件」の折りたたみで保持する。帯を判定できなかった候補は「距離を判定できませんでした」
   区画に新着順で置く（PR2）。seed 自身（同一正規化 ID）は一覧から除外する。
5. **比較分析** — チェック選択した候補（上限 `RADAR_COMPARE_MAX_CANDIDATES` 件）に対し
   [違いを分析]（`materials.radar-compare`）。結果は各候補カード内に
   「**起点論文との違い（AI 推定）**」ブロックとして挿入: 共通点1行 + 違いの箇条書き
   （各項目に候補要旨からの逐語引用）+ サーバ固定の注意書き（PR4）。失敗・上限到達は
   カード外の事実文1行（数値なし）。
6. **取り込み** — 分野購読モーダルの選択・確認 UI をそのまま踏襲（選択≦5 = 同期 `/ingest`、
   6件以上 = `/ingest-batch`。境界述語・確認事実文・許可ドメイン未設定時の案内文言も同一。
   `materials.radar-ingest`）。受理後は `onUploadAccepted` へ合流（PD2）。

**管理UI 3点セット**: teacher マニュアル節（`docs/manual/teacher/11-admin-materials.md` に
`{#radar}` 系。無効化され得る取り込みボタン・比較ボタンは「無効になっている場合: 理由 +
解消方法」の節を必ず持つ）+ `ADMIN_UI_ANCHORS` 登録 + `data-ui-anchor` 付与。アンカーの
設計値は `materials.row-radar` / `materials.radar-modal` / `materials.radar-distance` /
`materials.radar-search` / `materials.radar-compare` / `materials.radar-ingest` の6件
（正確な件数は実装時の `test_admin_help_ui_anchors.py` が正）。

**Admin Copilot capability**: `materials.paper_radar`（`kind=guidance_only`・
`required_role=TEACHER`・locate_steps で行メニューを点灯）を1件登録。比較分析・取り込みの
action capability は登録しない（LLM コストを伴う操作を Copilot 代行に載せない —
ディスカバリー §4.4 と同じ判断）。

**G層 next_steps**: 追加しない（PR5 / PD8）。

---

## 5. バックエンド設計

### 5.1 core — `backend/core/paper_discovery/radar.py`（FastAPI 非 import・LLM 非 import）

seed 解決とクエリ組み立て・帯適用の合成。**このファイル自身は LLM に触れない**
（embedding は §5.2 の ranking.py 経由、比較文は §5.3 の compare.py）。

- **seed の解決** `resolve_seed(session, document_id) -> RadarSeed`:
  - `arxiv_id`: `normalize_arxiv_id(documents.source_url)`（無ければ `None` — 手動
    アップロード分は判定不能と正直に返す。PD6/§8 の既存規約）。
  - `categories` の供給順: ① arXiv ID があれば `arxiv_client.fetch_by_ids([id])`（§5.4）の
    メタデータ（`categories` / `primary_category`）② 無ければ seed の分野
    （`corpus.document_domain_keys` — 下記）の購読 `arxiv_categories` ③ どちらも無ければ
    空（教員の手入力待ち。**条件ゼロでは arXiv を呼ばない** — 既存 `run_search` と同じ
    PD6 の規律）。供給元は `categories_source ∈ {arxiv, subscription, manual}` で DTO に明示。
  - `summary`: arXiv メタデータの要旨（引ければ）。比較分析と重心フォールバックの材料。
  - **キーフレーズ供給**（near 用）: 当該 document の `theory_components` のうち承認済み
    review_status のラベル（`vocab.py` ③の document スコープ版。同じ承認語彙定数を使う）。
    `source` は既存語彙 `component` を再利用し、教員の追加分は `manual`（語彙を増やさない）。
- **分野の逆引き** `corpus.document_domain_keys(session, document_ref) -> list[str]`
  （corpus.py へ追加）: `domain_material_ids` の逆方向 — この document の `source_path` を
  sources に含むコースの `cartridge_id` 群。カテゴリ供給②と、取り込み時に既存 API へ渡す
  `domain_key`（監査の帰属）に使う。複数分野に属する場合は先頭（コース作成日の新しい順）。
  ゼロは正常な状態（`domain_key=""` で既存 fallback `arxiv` に落ちる）。
- **クエリ組み立て** `build_radar_query(seed, distance, categories, keyphrases)`:
  既存 `build_search_query` を呼ぶだけ（`near` = カテゴリ + enabled キーフレーズ、
  `mid` / `far` = カテゴリのみ。著者項は使わない）。距離語彙の正本は
  `schema.py` に `RADAR_DISTANCES = ("near", "mid", "far")` として置く（語彙の正本は
  schema 規約）。
- **検索の実行** `run_radar_search(session, ...)`: `arxiv_client.search` → 既存の注釈
  （`ingested_arxiv_ids` による status。**見送り注釈は付けない** — dismissal は分野購読の
  概念で、レーダーからは書かない・読まない）→ seed 自身の除外 → §5.2 の帯適用 →
  `closed_world_note` 同梱。分野購読の `last_checked_at` / `last_search_found_new` は
  **更新しない**（購読条件による検索ではないため。地図の端の集約ビットを汚さない）。

### 5.2 帯分け — `ranking.py` への追加（embedding の接触点は既存どおりここだけ）

既存ガードレール「`core/paper_discovery/` で `core.llm`（embedding）に触れるのは
ranking.py のみ」を維持するため、以下を ranking.py に追加する:

- `document_centroid(session, document_id)` — 既存 `field_centroid` の内部にある
  document 単位の2段平均（`by_document` 中間値）を1 document に縮めた公開関数。
  チャンク上限は既存 `DEFAULT_CHUNKS_PER_DOCUMENT` を共有。
- `band_candidates(session, seed_vector, candidates, *, daily_limit=None)` —
  `rank_candidates` と同型（候補 `title + summary` を**1バッチ**で埋め込み → cosine →
  ラベル）。相違点: ①重心が分野でなく seed ベクトル ②出力が順位でなく
  `distance_label`（正本は `label_vocab.RADAR_DISTANCE_SCALE` — 3帯「近い / 中間 / 遠い」。
  閾値は `DISCOVERY_RELEVANCE_SCALE` と同じ 0.45 / 0.30 を初期発明値とし実測で見直す）
  ③**未測定（cosine が None）の候補にはラベルを付けず** `distance_label` キー自体を
  省略する（PR2。`GradedScale.label_for` の慎重側フォールバックを通さない）。
- **seed ベクトルのフォールバック**: `document_centroid` が作れない（チャンク・embedding
  ゼロ）場合、seed の arXiv 要旨があればそれを候補と**同じバッチ**の先頭に入れて埋め込み、
  疑似 seed ベクトルとする（追加コールなし）。それも無ければ
  `banding.available=false` + 事実文で全候補を新着順のまま返す（fail-soft —
  `rank_candidates` と同じ規律。検索は必ず成立させる）。
- **コスト**: 日次ゲートは既存の `DISCOVERY_RANKING_MAX_CALLS_PER_DAY`（既定100）の
  カウンタを**共有**する（発見層の embedding 予算は1本 — 用途別に env を増やさない）。
  U層 feature も `discovery:ranking` を再利用（実体が同一のため。`scene_for_feature` は
  None のまま = M5 embedding 扱い）。

### 5.3 比較分析 — `backend/core/paper_discovery/compare.py`（本層唯一のテキスト LLM）

- **入力の組み立て**（非LLM）: seed 側 = タイトル + arXiv 要旨（あれば）+
  `document_run_artifacts(document_id)` の `paper_skeleton`（`paper_goal`）/
  `thesis_reconstruction`（`central_thesis.text` / `central_question`）—
  `landscape/builder.build_placement_input` と同じ artifact 読みの流儀。候補側 =
  `arxiv_client.fetch_by_ids(arxiv_ids)` で**サーバが取り直した**メタデータのみ
  （クライアントから要旨本文を受け取らない — verbatim 検査の土台を本物にする）。
  seed 素材が1つも無ければ LLM を呼ばず 422 の事実文（`_input_has_material` と同じ
  「素材なしで創作しない」ゲート）。
- **LLM 呼び出し**: 1リクエスト = 1コール（`generate_text_with_structured_output`、
  `teaching_figures/suggest.py` と同じ同期・単発パターン）。候補上限
  `RADAR_COMPARE_MAX_CANDIDATES = 10`（定数。超過は 422 の事実文）。
  プロンプト制約（ガードレールが原文 grep で固定）:
  「アブストラクトに書かれていることだけを比較する」「断定せず推量形で書く」
  「数値スコア・優劣の評価を書かない」。
- **structured output**（候補ごと）: `common_ground`（共通点1文）/ `differences[]`
  （各要素 = `aspect ∈ {approach, conclusion, scope, method, theme, unknown}` +
  `statement`（仮説文体）+ `evidence_quote`（**候補要旨からの verbatim — validator が
  hard 検査し、不一致はその difference のみ drop・warning 保持**。discuss_opening と
  同じ捏造ガード））。confidence 生値は DTO に出さない（返させても route 手前で落とす）。
- **サーバ側固定文**: 各結果に `caveat = "アブストラクト（要旨）の比較に基づく AI の
  推定です。本文は確認されていません。"` を route 層で必ず付与（PR4。LLM 出力に依存しない）。
- **非保存**: 結果はレスポンス限り。`element_explanations` 等への格納・レビューキュー化は
  v1 非スコープ（§8）。
- **コスト・モデル**: `DISCOVERY_COMPARE_MAX_CALLS_PER_DAY`（Settings フィールド +
  `AliasChoices`、既定 20）。ゲートは route 層の `CostGate`（day-only・
  `(today_str(), user_id)` の**ユーザー別キー** — figure_suggest と同型）。モデルは
  `resolve_model("discovery_compare_llm_model", fallback="fast")`（env
  `DISCOVERY_COMPARE_LLM_MODEL`）。U層 feature `discovery:compare` を
  `KNOWN_FEATURES` + `llm_policy.scene_for_feature` + `_FEATURE_ENV_SETTINGS` に
  **3点同時登録**（SL層と同じ規約。scene ラベル例:「類似論文の比較分析」）。
- **失敗の返し方**: 上限到達 = 429・LLM 失敗 = 502（いずれも数値を含まない事実文）。
  単発の明示操作なので degraded 固定文への縮退はしない（rewrite と同じ扱い）。
  fetch_by_ids で引けなかった候補は `skipped[]` に事実文つきで返す（黙って落とさない）。

### 5.4 `arxiv_client.py` への追加 — `fetch_by_ids(ids)`

arXiv API の `id_list` パラメータによる ID 指定取得（1コールで複数件）。既存
`search()` と同じ `_throttle()` / タイムアウト / `parse_atom` を通り、宛先は定数のまま
（PR6）。上限は控えめな定数（例 20 件）。seed メタデータ取得（§5.1）と比較分析の候補
取り直し（§5.3）が使う。

### 5.5 API（`backend/api/routes/paper_discovery.py` へ追加。全て `_require_teacher`）

新ルーターは作らず既存ルーターに2本足す（`/api/admin/discovery/radar/...`）:

| エンドポイント | 内容 |
|---|---|
| `POST /radar/search` | body: `document_ref` / `distance`（`near`\|`mid`\|`far`、語彙外 422）/ `categories?` / `keyphrases?`（条件上書き — 保存しない）/ `start?` / `max_results?`。**document 可視性ゲート**（`services.resolve_document_access` の can_view。不可視と不在は同一 404 — 既存規約）→ `run_radar_search`。レスポンス: `{seed, query, distance, total, candidates, banding, closed_world_note}`。candidates 各要素は既存候補 DTO + `distance_label?`。arXiv 失敗は 502 + 既存の固定事実文 |
| `POST /radar/compare` | body: `document_ref` / `arxiv_ids`（≦10）。同じ可視性ゲート → CostGate（429）→ `compare.run_compare`。レスポンス: `{items: [{arxiv_id, common_ground, differences, caveat}], skipped, notes}` |

- **監査**: どちらも読み取り専用・副作用ゼロのため記帳しない（既存 `/search` /
  `/citation-search` と同じ扱い）。取り込みは既存の監査済みエンドポイントを使う（PR3）。
  レーダーからの ingest 呼び出しは `domain_key` に §5.1 の逆引き結果（無ければ空 →
  既存 fallback `arxiv`）を渡す。
- **学習者 API を作らない**（PR8。`/api/learning` 配下にレーダー系ルートが無いことを
  ガードレールで固定）。

### 5.6 env・設定まとめ

| 変数 | 既定 | 意味 |
|---|---|---|
| `DISCOVERY_COMPARE_MAX_CALLS_PER_DAY` | 20 | 比較分析の日次上限（ユーザー別・Settings フィールド） |
| `DISCOVERY_COMPARE_LLM_MODEL` | 空 = fast tier | 比較分析のモデル（M層 scene 経由で上書き可） |
| （共有）`DISCOVERY_RANKING_MAX_CALLS_PER_DAY` | 100 | 帯分け embedding は既存ランキングの予算を共有 |

定数（env にしない）: `RADAR_COMPARE_MAX_CANDIDATES = 10` / `fetch_by_ids` の ID 上限 /
距離帯の閾値（`label_vocab.RADAR_DISTANCE_SCALE`、発明値・実測見直し前提）。

---

## 6. コスト

- **検索 + 帯分け**: arXiv 1コール（3秒スロットル）+ embedding 1バッチ
  （`discovery:ranking` 予算に相乗り）。テキスト LLM 0回。
- **比較分析**: arXiv 1コール（id_list）+ テキスト LLM 1コール（≦10候補ぶんを1コールに
  同梱）。教員の明示ボタンでのみ発火（PR5）。
- **取り込み**: 既存パイプラインのコストそのもの（既存の見積り `GET /ingest-estimate` を
  レーダーモーダルでも再利用してよい）。

---

## 7. ガードレール（`backend/tests/test_paper_radar_{core,api,guardrails,ui_static}.py`）

- `radar.py` / `compare.py` が FastAPI を import しない。`radar.py` は `core.llm` にも
  触れない（embedding の接触点は ranking.py のまま — 既存ガードレールの allowlist を
  「ranking.py（embedding）+ compare.py（比較文）」の2ファイルに改訂し、他は LLM 0回を維持）
- cosine 生値・confidence が DTO に現れない（PR2/PD4）。`distance_label` の語彙・閾値は
  `label_vocab` 正本のみ（重複表検出の既存ガードレールに載せる）。未測定候補に
  `distance_label` が付かない
- `fetch_by_ids` が `_throttle()` を通り、宛先が定数ホストである（PR6）
- compare の evidence verbatim hard 検査・`caveat` がサーバ側定数・プロンプト制約文の
  原文存在（PR4）
- レーダー経路が `paper_discovery_subscriptions` / `paper_discovery_dismissals` に
  書き込まない（`last_checked_at` 含む）。worker / cron からの radar 呼び出し経路が無い（PR5）
- レーダー専用の取得・取り込みエンドポイントが無い（PR3 — ingest は既存2本のみ）
- `/radar/*` が document 可視性ゲートを通る・不可視と不在が同一 404（PR8）
- `/api/learning` 配下にレーダー系ルートが無い（PR8）
- migration ディレクトリに本層の採番が無い（PR1 — 新テーブル・新列ゼロの構造的確認）
- UI 静的検査: 検索条件 + `closed_world_note` の常時表示 / 距離帯の折りたたみ保持
  （候補を捨てない）/ 比較ブロックの出所ラベル / 許可ドメイン未設定時の案内文言

---

## 8. 非スコープ（v1）

- **見送り（dismiss）のレーダー対応** — dismissal は分野購読の共同財概念。教材起点の
  一時的な探索には持ち込まない（候補は毎回導出で消える）
- **比較文の保存・レビューキュー化** — C層 explanation への格納は、比較文を教材へ流用する
  需要が観測されてから（保存するなら candidate-only 原則で別途設計）
- **学習者向け表示のすべて**（PR8。コーパス回遊層の「地図の端 — 外の輪」が学習者側の窓口）
- **引用グラフとの合成**（citation-search はシード5件の分野単位のまま。seed 1件指定の
  引用照会は需要観測後）
- **本文全文の比較**（PDF を取得しての比較は取り込みの弁を迂回する — 比較はアブストの
  範囲に限る、PR7）
- **距離帯閾値の UI 調整・seed 複数指定・保存済み探索条件**
- G層ルール・通知・バッジ（PD8）

---

## 9. 実装時の確認事項

- `ADMIN_UI_ANCHORS` 追加によるアンカー総数の更新（正は `test_admin_help_ui_anchors.py`。
  マニュアル節 + アンカー + `data-ui-anchor` の3点を同時に揃える）
- 教材管理タブは SYSTEM_ADMIN から不達の既知事情（AL層実装記録）— 入口が行メニュー内の
  ため分野購読と同条件。追加対応不要の見込みだが確認する
- `_apply_relevance_order` / `rank_candidates` との共通化の程度（帯分けとランキングで
  embedding バッチ・fail-soft 構造が同型。コピペせず ranking.py 内で共有する）
- `document_domain_keys` の複数分野所属時の代表選択（コース作成日の新しい順の先頭）が
  ingest 監査の帰属として十分か
- 距離帯の初期閾値（0.45 / 0.30）の妥当性 — 実測でヒストグラムを見て見直す
  （`DISCOVERY_RELEVANCE_SCALE` と同じ「発明値・実測見直し前提」の注記を label_vocab に残す）
- `docs/architecture/layer_registry.md` / `docs/backend/api.md`（既存ルーターへのルート
  追加のため §3 節の追記のみ）/ `CLAUDE.md` の追随（実装時）

---

## 10. 実装記録（2026-08-28）

Fable 5 指揮・Opus 5 並列サブエージェント3体（backend core+API / フロントエンド /
管理UI 3点セット）で同日実装。backend フルスイート green・コミット未実施。

### 実装ファイル

- **core（新規）**: `backend/core/paper_discovery/radar.py`（seed 解決・クエリ組み立て・
  検索合成。FastAPI / `core.llm` 非 import）/ `compare.py`（比較分析 — 発見層で
  ranking.py と並ぶ2本目の LLM 接触ファイル）
- **core（追記）**: `schema.py`（`RADAR_DISTANCES`）/ `arxiv_client.py`
  （`fetch_by_ids`・`MAX_ID_LIST=20`・既存スロットル経由）/ `corpus.py`
  （`document_domain_keys` — 分野の逆引き）/ `ranking.py`（`document_centroid` /
  `band_candidates` / `NOTE_NO_SEED`。既存 `field_centroid` は `_document_means` 抽出の
  内部リファクタのみで挙動不変）/ `label_vocab.py`（`RADAR_DISTANCE_SCALE` 3帯 +
  閾値 0.45 / 0.30）/ `config.py`（env 2件）/ `llm_usage/schema.py` + `llm_policy.py`
  （`discovery:compare` の3点同時登録。scene `discovery_compare`
  「類似論文の比較分析」）
- **API**: `routes/paper_discovery.py` に3ルート追記（`GET /radar/seed` /
  `POST /radar/search` / `POST /radar/compare`。可視性ゲートは landscape の
  `_document_access_or_404` 同型・compare の CostGate は route 層でユーザー別日次キー）
- **UI**: `frontend/public/js/admin-paper-radar.js`（新規・ES5・`window.PaperRadar`・
  `pr-` プレフィックス・PaperDiscovery と同じ DI）+ `admin.js`（行に `radarBtn`（当初は `⋯`
  メニュー項目・2026-09-06 から行アイコン）・ハンドラ・DI init・論理アンカー
  `paper_radar_row_button`（旧 `paper_radar_row_menu`））+ `admin.html`
- **3点セット**: `docs/manual/teacher/11-admin-materials.md` に7節（`{#radar}` 概要 +
  6アンカー節）/ `ADMIN_UI_ANCHORS` 6件（総数 293→299。正は
  `test_admin_help_ui_anchors.py`）/ capability `materials.paper_radar`
  （guidance_only）+ `docs/admin_operations/materials.md` `{#paper-radar}` 節
- **テスト**: `test_paper_radar_{core,api,guardrails,ui_static}.py`（計 200+ 件）+
  `test_paper_discovery_guardrails.py` の LLM 接触 allowlist を
  `("ranking.py", "compare.py")` に改訂 + `test_admin_help_inspect_ui_static.py` の
  `_ADMIN_FRONTEND_SOURCES` に radar JS を追加
- **env**: `.env.example` / `docs/architecture/deployment.md` に
  `DISCOVERY_COMPARE_MAX_CALLS_PER_DAY`(20) / `DISCOVERY_COMPARE_LLM_MODEL`

### 設計からの確定事項・逸脱

1. **エンドポイントは3本**（設計 §5.5 の2本 + `GET /radar/seed` 追加）。モーダルを
   開いた時点で条件チップを prefill するには seed 解決が検索より先に要るため。
   seed 取得も読み取り専用・監査記帳なし。
   （その後 §11.8 で `POST /radar/provenance` が加わり、レーダーのエンドポイントは
   現在4本。）
2. **`banding.primary_label` を追加** — 選択距離に対応する帯ラベル（正本は
   label_vocab）を banding に同梱し、UI が展開する帯を決定論にする（帯分け不能時は
   付けない）。フロントは `primary_label` 不在時に最大の帯へ fail-soft。
3. **compare の arXiv 取得は1コールに統合** — seed の要旨を候補と同じ `id_list` に
   相乗りさせ、seed 用の追加スロットル待ちを作らない（§6 のコスト表どおり）。
4. **カテゴリ空 + キーフレーズありのときは arXiv を呼ぶ**（`build_search_query` が
   非空を返すため。「条件ゼロ」= カテゴリもフレーズも無い場合のみ非呼び出し —
   既存 `run_search` と同一の PD6 解釈）。
5. **JS 側に距離ラベル表・閾値を持たない** — 候補は `distance_label` 文字列そのもので
   グルーピングし、ラベル語彙の正本をサーバ側に一本化（PR2 の徹底。ui_static が
   直書き不在を検査）。
6. カテゴリ明示指定時は seed の arXiv メタデータ取得をスキップ（不要な外部コールを
   増やさない — PR6 の行儀）。

---

## 11. 出所の後付け登録（3段階）

> 2026-08-28 追補。migration **なし**（新テーブル・新列ゼロは §2 PR1 のまま）。
> 記帳先は既存の出所列 `documents.source_url` 1つだけ。

### 11.1 問題 — 手元のファイルからアップロードした教材でレーダーが実質使えない

`resolve_seed`（§5.1）の `arxiv_id` は `normalize_arxiv_id(documents.source_url)` からしか
引けない。`source_url` は URL 取得（UF）と ディスカバリー ingest（PD）が書く列なので、
教員が**手元のファイルからアップロードした教材では空**である。この教材でレーダーを開くと

- カテゴリ供給①（arXiv メタデータ）が使えない
- 供給②（分野購読の `arxiv_categories`）も、その分野に購読が無ければ空
- 結果として検索条件ゼロ → **arXiv を呼ばない**（PD6）

となり、レーダーが実質使えない。しかし実際の運用では、教員は arXiv からダウンロードした
ファイルを `arXiv-2407.01221v2.tar.gz` のような名前のままアップロードしている。
**出所の情報は手元にあるのに、システムがそれを知らない**という状態である。

本節はこの隙間を、**推定 → 検証 → 記帳**の3段階で埋める。段階が上がるたびに根拠が強くなり、
最後の1段（DB への記帳）だけが決定論的な検証か教員の明示操作を要求する。

### 11.2 段階1 — セッション内プリフィル（書き込みなし）

`resolve_seed` は `source_url` が空のとき、教材のファイル名から arXiv ID を
**決定論で推定**する（`schema.arxiv_id_from_filename`）。

- 新旧2形式（`2407.01221v2` / `hep-ph/0501001`）をファイル名から拾い、
  `normalize_arxiv_id` で version を落として正規化する。
- **相異なる ID が複数見つかったら推定しない**（戻り値は空文字列 `""`）。どれが本体か
  決められないものを一つ選ぶのは判定不能の偽装にあたる（PD6）。同一 ID の重複出現は1件と
  して扱う。
- 推定 ID があるときに限り `arxiv_client.fetch_by_ids([id])` を1回呼び、カテゴリ・要旨・
  タイトルを取得して検索条件にプリフィルする。**推定 ID が無ければ arXiv を呼ばない**
  （PD6 の「条件ゼロで外部 API を呼ばない」と同じ規律を、ID 経路にも適用する）。
- **`documents.source_url` には何も書かない**。この段階の成果はレスポンス限りで、
  リロードすれば消える（PR1 — seed も候補も保存しない）。

DTO は「これは推定である」ことを構造で明示する。

- `categories_source` に既存3語彙（`arxiv` / `subscription` / `manual`）と並ぶ
  **`arxiv_inferred`** を追加する。`arxiv`（`source_url` 由来の確定した出所）と
  同じラベルに畳まない（PR7 — 閉世界と推定の正直さ）。
- `seed.provenance` に推定の状態を出す:

| フィールド | 意味 |
|---|---|
| `status` | 出所の状態（未登録 / ファイル名から推定 / 登録済み） |
| `arxiv_id` | 推定 or 登録済みの正規化 ID |
| `arxiv_title` | arXiv 側のタイトル（取得できたときのみ） |
| `document_title` | 教材から抽出済みのタイトル |
| `title_match` | 正規化タイトルが一致したか（§11.3。照合不能は真偽ではなく「不能」） |
| `fetched` | arXiv メタデータを取得できたか（PR7 の fail-soft を隠さない） |
| `can_register` | 記帳可能か（編集権限あり・`source_url` 空・ID 確定の3条件） |

UI は `categories_source = arxiv_inferred` のとき、条件チップに「ファイル名からの推定」の
出所を表示する（PD3 の供給元ツールチップと同じ流儀）。§4.2 区画1 の事実文
「この教材は arXiv 由来として登録されていないため、カテゴリを指定してください」は、
推定が当たったときはこの推定表示に置き換わり、推定できなかったときは従来どおり出る。

### 11.3 段階2 — タイトル一致時の自動記帳

推定 ID で引いた arXiv のタイトルと、教材から抽出済みのタイトルを**正規化して比較**する。

- 正規化: NFKC → casefold → 英数字以外を除去。
- 比較は**完全一致**のみ。部分一致・編集距離・類似度スコアは使わない（閾値を発明すると
  「どのくらい似ていれば同じ論文か」という判定不能な問いを数値で偽装することになる）。
- 正規化後の長さが **10 未満**の側があれば照合を行わない（短すぎるタイトルは偶然一致する）。
  この場合は「照合不能」であって「不一致」ではない。

一致した場合に限り、フロントは `POST /api/admin/discovery/radar/provenance` を
**自動で1回だけ**呼ぶ。サーバは受け取った値を信用せず、**同じ推定と同じ照合をやり直したうえで**
`documents.source_url = https://arxiv.org/abs/{id}`（version 抜き）を記帳する
（`method="auto_title_match"`）。監査は `AUDIT_ENTITY_PAPER_DISCOVERY` /
`new_status="provenance_registered"`。

記帳が成立すると、以後この教材は URL 取得・ディスカバリー ingest 由来の教材と**同じ状態**に
なる — レーダーの `arxiv_id` が確定し（`categories_source` は `arxiv` に上がる）、
ディスカバリー候補一覧の「取り込み済み」判定（`documents.source_url` が正本 — PD5）にも
乗る。手動アップロード分が判定不能だった穴が、教員の作業を増やさずに1件ずつ埋まる。

### 11.4 段階3 — 不一致・照合不能時は教員が確定する

タイトルが一致しなかった場合、モーダルは**2つのタイトルを並置**し、
「この論文として登録する」ボタン（`materials.radar-provenance`）を出す。押下は
`confirm=true` の同じ POST で、`method="teacher_confirmed"` として記帳される。
判断の材料（arXiv 側タイトル・教材側タイトル・推定 ID とそのリンク）を全部見せたうえで、
確定は人間が1操作で行う — AI にも文字列一致にも肩代わりさせない。

**arXiv に到達できなかった場合（`fetched=false`）は登録できない**（422）。照合の相手が
無い状態で「たぶんこれ」を記帳するのは、判定不能の偽装そのものだからである。
教員には「arXiv に接続できなかったため確認できません」の事実文を出し、
ボタンは出さない（時間をおいて再試行すれば照合できる）。

### 11.5 権限とエラー

| 状況 | 応答 |
|---|---|
| document が不在 / 閲覧不可 | **404**（不可視と不在を区別しない — §5.5 の既存規約） |
| 閲覧はできるが編集権限が無い | **403**（他人の教材の出所を書き換えない） |
| `documents.source_url` が既に非空 | **409**（**上書きしない**。出所は先に記録されたものが正） |
| arXiv 未到達 / ID 未確定 / タイトル不一致で `confirm` なし | **422**（事実文。数値・内部情報を含めない — UF6 の流儀） |

### 11.6 不変条項との整合

- **PR1（候補・seed は保存しない）** — 本節が書くのは `documents.source_url` という
  **既存の出所列**1つだけで、seed の解決結果・候補一覧・距離帯は従来どおり保存しない。
  レーダー専用のテーブル・列は増えない（migration 0 は維持）。
- **PR7（閉世界の正直さ）** — 推定は `arxiv_inferred` / `provenance.status` で
  「推定」とラベルされたまま提示され、確定した出所（`arxiv`）と同じ顔をしない。
- **PD6（条件ゼロで arXiv を呼ばない）** — ID を推定できた場合のみ `fetch_by_ids` を1回。
  推定できなければ外部コールは発生しない。
- **PD1/PD2/PR3（取り込みの弁）** — 出所の記帳は**取り込みではない**。この POST は
  解析パイプラインを起動せず、`url_fetch` も呼ばない。既に取り込み済みの教材に
  「どこから来たか」を1行書くだけである。
- **判定不能を偽装しない** — 自動記帳は「決定論的な検証が成立した場合」に限定され、
  それ以外は必ず人間の1操作を通る。ファイル名は**推定の入口**であって記帳の根拠ではない。

### 11.7 なぜファイル名を無検証で信じないか（設計判断）

ファイル名は教員がリネームでき、ダウンロード時の連番付与や重複回避で `(1)` が付き、
複数論文をまとめた圧縮ファイルの名前にもなりうる。「ファイル名に arXiv ID が入っている」は
**その ID の論文であることの証拠にならない**。誤った `source_url` が記帳されると、
①レーダーの seed が別論文のカテゴリ・要旨で走り ②ディスカバリーの「取り込み済み」判定が
別論文に付き ③以後の教員には確定した出所として見える、という形で誤りが固定される。
記帳は取り消し API を持たない（P4 — 行削除しない）ため、**入口で強い根拠を要求する**方が
安い。タイトル完全一致は「同じ論文であること」の決定論的な証拠として十分強く、かつ
一致しなかったときに人間の判断へ落ちる自然な出口を持つ。

### 11.8 実装記録（2026-08-28）

§10 と同じ体制（Fable 5 指揮・Opus 5 並列サブエージェント: backend / frontend /
ドキュメント・アンカー）で同日追補。migration なし・新テーブル / 新列ゼロ。

- **core**: `schema.py` に `arxiv_id_from_filename`（決定論・複数 ID は `None`）と
  タイトル正規化ヘルパ / `radar.py` の `resolve_seed` に推定経路と `provenance` の組み立て
  （`categories_source` に `arxiv_inferred` を追加）
- **API**: `routes/paper_discovery.py` に `POST /api/admin/discovery/radar/provenance`
  を1本追加（レーダーの API は 3本 → **4本**）。可視性 404 / 編集権限 403 /
  既存出所 409 / 照合不能 422。監査 `AUDIT_ENTITY_PAPER_DISCOVERY`・
  `new_status="provenance_registered"`・`method ∈ {auto_title_match, teacher_confirmed}`
- **UI**: `admin-paper-radar.js` の seed ヘッダに出所区画（推定表示 / タイトル並置 /
  「この論文として登録する」）。自動記帳はモーダルを開いた1回のみ（再試行ループを作らない）
- **3点セット**: `docs/manual/teacher/11-admin-materials.md`
  [`{#radar-provenance}`](../manual/teacher/11-admin-materials.md) 節 +
  `ADMIN_UI_ANCHORS` に `materials.radar-provenance`（レーダーのアンカーは 6件 → **7件**。
  正確な総数は `test_admin_help_ui_anchors.py` が正）+ `data-ui-anchor` 付与
- **ガードレール**（`test_paper_radar_{core,api,guardrails}.py` へ追記）: 複数 ID の
  ファイル名で推定しない / 推定なしで `fetch_by_ids` を呼ばない / 自動記帳が
  サーバ側の再照合を通る / 403・404・409・422 の分岐 / `source_url` を上書きしない /
  記帳経路が取り込み（`url_fetch` / ingest）を起動しない / migration に本節の採番が無い

---

## 12. 重なり・差分提示

> 2026-08-29 追補。migration **なし**（新テーブル・新列ゼロは §2 PR1 のまま）。
> 新しい env・新しい UI アンカー・**新しい LLM 呼び出しもゼロ**（比較は既存1コールの
> 出力拡張）。VA層（[分野マップのベクトル係留層](atlas_vector_anchoring_design.md)）の
> 着地予測を radar に配線し、非LLM の関係チップを足す。

### 12.1 問題 — 候補が「近い」だけで、どう近いのかが読めない

§10 までのレーダーは候補に**距離の段階ラベル**しか付けない。教員が実際に判断したいのは
「この論文は、いま持っているコーパスのどこに入るのか」「起点論文と何が重なり、何が新しい
のか」であり、距離ラベル単独ではそこに届かない。要旨を1本ずつ読むか、AI 比較分析（§5.3）を
明示的に叩くしかなかった。

一方で、候補ベクトルは**帯分けの時点で既に作られている**（`band_candidates` の1バッチ）。
これを使い回せば、追加の embedding 呼び出しゼロで「地図のどこに落ちるか」「起点論文の
どの部品と重なりそうか」を全候補に付けられる。3つの提示はいずれも**その使い回しの上に
成り立ち、新しいコストを一切足さない**。

### 12.2 提示1 — 着地予測（非LLM・全候補・追加 embedding ゼロ）

VA層 §8 の着地予測（`atlas_vectors.query.landing_for_vector`）を radar にも配線する。
`band_candidates` が作った候補ベクトルを現行凍結版のアンカーと照合し、上位2帯のときだけ
候補 dict に付与する:

```python
"landing": {"node_label", "region_label", "nearness_label", "skeleton_version"}
```

- **import 境界は不変**: `radar.py` は `core.llm` にも `atlas_vectors` にも触れない
  （§7 のガードレール）。route 層（`routes/paper_discovery.py`）が既存の
  `_anchor_context` を**resolver として** `run_radar_search(anchor_context_resolver=...)`
  に注入し、radar 側は呼ぶだけにする。帯ラベルと同じく `_merge_distance_labels` が
  `landing` も候補行へ移す。
- **レスポンス top-level に `relation_context: {available, skeleton_version}`** を足す。
  ベクトルが引けない・骨格が凍結されていない場合は `available: false` で、UI は
  「測れなかった」ことを事実文で出す（§12.5）。
- **下位帯・アンカー不在・骨格なしはキー自体を付けない**（VA4/VA8）。生 cosine は
  関数の外へ出ない（VA2 / PR2）。
- **seed のドメイン帰属**は `corpus.document_domain_keys`（§5.1 の逆引き）で解決する。
  VA層 §11 が「radar は seed のドメイン帰属が多義」として v2 送りにしていた点は、
  この既存関数（複数所属時はコース作成日の新しい順の先頭）で決着した
  → VA層 §11 に解消注記済み。

### 12.3 提示2 — 重なり / 新しい面チップ（非LLM・全候補）

| チップ | 導出 | 上限 |
|---|---|---|
| **重なり**（`overlap_components: [str]`） | seed 教材の**承認済み** `theory_components` ラベル（`seed_keyphrase_candidates` と同じ供給源・同じ承認語彙定数）と候補タイトル+要旨の **casefold 部分一致**（`radar.overlap_component_labels`） | 6 |
| **新しい面**（`new_facets: [str]`） | 候補ベクトルが**最上位帯**（`ANCHOR_LANDING_THRESHOLD_NEAR` 以上 — 着地予測と同じ「論文テキスト×アンカー」レジーム。VA層 §9）で近いアンカーのうち、seed 教材の `landscape_placements`（`status NOT IN ('superseded','rejected')`）に**無い** node のラベル（`atlas_vectors.query.new_facet_labels`） | 2 |

- どちらも **LLM 0回**。重なりは文字列照合、新しい面は 12.2 と同じ使い回しベクトル。
- **未測定の候補にはキー自体を付けない**（PR2 継承。「重なりなし」「新しい面なし」を
  空配列で断言しない — 測れなかったことと、測って無かったことを区別する）。
- 新しい面は「起点論文が**この教材の配置としては**言及していないアンカー」であって
  「起点論文が扱っていない主題」ではない。UI 文言は前者に閉じる（PR7 の閉世界）。
- 件数は出さない（LS5）。表示上の省略は「ほか」で示し、数を書かない。

### 12.4 提示3 — AI 比較の2区画化（既存 compare の拡張・1 LLM コールのまま）

§5.3 の structured output に**もう1本の配列**を足すだけで、コールは1回のまま:

```python
"overlaps": [{"component_label", "statement", "evidence_quote"}]
```

- プロンプトに **seed の承認済み部品ラベルの閉世界リスト**を提示し、「別の表現・文脈で
  同じ内容を扱っていそうな箇所」を挙げさせる（PR4 の仮説文体は不変）。
- validator は2段:
  ①`evidence_quote` の **verbatim 検査**（候補要旨に無ければその項目のみ drop — 既存
  `differences` と同じ捏造ガード）
  ②`component_label` の**リスト実在検査**（提示した閉世界リストに無いラベルは
  **空文字化して `statement` は保持**する。項目ごと落とさない = 情報を落とさない P4。
  「AI がラベルを作った」ことは残さず、「重なりの指摘」は残す）。
- `common_ground`（共通点1文）は**後方互換で維持**する（既存 UI・既存テストが読む）。
  `overlaps` は「共通点」をラベル単位に分解した詳細で、置き換えではない。
- 日次上限 20（`DISCOVERY_COMPARE_MAX_CALLS_PER_DAY`）・**非保存**・サーバ側固定
  `caveat` はすべて不変（PR4/§5.3）。

### 12.5 UI（`admin-paper-radar.js`）— 推定であることを剥がさない

新しいアンカーは**足さない**（表示のみで、新しい操作要素が無いため）。

| 位置 | 表示 |
|---|---|
| 候補一覧の直前 | 凡例1行「いずれもタイトル・要旨からの推定です（取り込み後の解析・教員確認で確定します）」 |
| 候補行（着地1行） | 「取り込むと: {region} / {node} の近くに落ちそうです（{nearness}・骨格 版{v}）」 |
| 候補行（チップ行） | 「≒ 部品「X」と重なりそう」／「✚「Y」の近く（起点論文は未言及）」+ **〈推定〉タグ**。重なりは最大3表示 + 「ほか」 |
| 候補行（未測定時） | 「着地・重なりの近さは測定できませんでした（このまま取り込みできます）」— **`relation_context.available` が true のときだけ**出す（層ごと使えないときに全行へ言い訳を並べない） |
| 比較ブロック | 2区画「≒ 重なっていそうな要素 — 別の表現・文脈で同じ内容」/「✚ 異なっていそうな要素 — 関連するが別の知識」。逐語引用は折りたたみ |

- 段階ラベル（`nearness_label` / `distance_label`）は**サーバ提供の文字列のみ**を描画する
  （§10 逸脱5 の徹底。JS に閾値・ラベル表を持たない）。
- 未測定を最遠帯・空配列に化けさせない（PR2）。着地が無い候補は着地行ごと出さない。

### 12.6 不変条項との整合

- **PR1**（保存しない）: 着地・チップ・比較はすべてレスポンス限りの読み時導出。
  `landscape_placements` は**読むだけ**で、レーダーから配置を書かない。
- **PR2 / VA2 / LS5**（数値非表示）: cosine・件数・スコアは一切出さない。段階ラベルと
  「ほか」だけ。
- **PR3 / PR5**（弁は既存・明示操作のみ）: 取り込み経路は不変。着地・チップは検索の
  レスポンスに同乗するだけで新しいボタンを作らない。比較は従来どおりボタン押下時のみ。
- **PR4**（推定・非保存・出所明示）: 〈推定〉タグと凡例1行を全候補に付け、比較の
  `caveat` は不変。
- **PR6 / PR7**（外部到達・閉世界）: 外部 API 呼び出しは増えない（arXiv も embedding も
  従来どおり）。着地は「**現行凍結版の骨格の中で**近い」としか言わない（VA8）。
- **VA4**（fail-soft）: 着地・新しい面が引けなくても検索は必ず成立する。
  `relation_context.available=false` で degrade。
- **コストゼロの原則**: 新しい env・新しい migration・新しい UI アンカー・新しい LLM
  コールのいずれも足さない。着地と新しい面は既存バッチのベクトル使い回し、重なりは
  文字列照合、比較の `overlaps` は既存1コールの出力拡張。
