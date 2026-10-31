# 論文レーダー（教材起点の類似論文探索と比較分析）

> **状態: 実装済み（正本）**（2026-08-28 起票・同日実装。migration **なし** —
> 新テーブル・新列ゼロ。実装記録は §10、出所の後付け登録の追補は §11、
> 重なり・差分提示の追補（2026-08-29）は §12、縮退の事実文の是正（2026-09-13）は §13、
> arXiv 呼び出しの上限（2026-09-14）は §14、取得する形式（TeX / PDF）と取り込み済み
> ラベル（2026-09-16）は §15、**API の既定を TeX に変更し PDF への同期フォールバックを
> 足した改訂（2026-09-30）は §15.9** — §15.3 の「API の既定は PDF」は §15.9 で置き換えた）

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
    **2026-10-16（§19 B）**: arXiv 由来のカテゴリは**主カテゴリだけを既定で有効**にし
    （`seed.categories = [primary]`）、副カテゴリは `seed.category_candidates` に
    `enabled=false` で並べる。既定のクエリは例えば `(cat:astro-ph.CO) AND (all:"kurtosis")`
    （旧: `(cat:astro-ph.CO OR cat:gr-qc OR cat:hep-ph OR cat:hep-th) AND …`）。購読由来は全件有効のまま。
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
  （status。**2026-10-16 以降は `search.visible_ingested_documents`（閲覧者に見える教材だけ・
  §19 C）**で判定し、旧 `ingested_arxiv_ids` の全域判定は使わない。**見送り注釈は付けない** — dismissal は分野購読の
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

---

## 13. 追補 — 縮退の事実文を画面まで届ける（2026-09-13・migration / env / アンカーなし）

### 13.1 症状 — 「候補が見つかりません」が「近い論文が無い」に読める

教員の報告: seed（`arXiv-2407.01221v2`）を開くと **カテゴリもキーフレーズも空**のまま
「カテゴリが未指定です」と出て、そのまま検索しても 0 件。画面には
「検索条件: 指定されていません」「この検索条件では候補が見つかりませんでした」だけが
残る。

実際には近い論文が無いのではなく、**検索が実行されていなかった**。原因の連鎖:

1. `near` のクエリは「カテゴリ AND キーフレーズ」（§5.1）。両方空なら
   `build_radar_query` は空文字を返し、`run_radar_search` は PD6 の規律で
   **arXiv を呼ばずに** 0 件を返す。
2. カテゴリが空だったのは `resolve_seed` の arXiv メタデータ取得が失敗したため
   （当該 seed には arXiv 側に `astro-ph.CO` / `gr-qc` / `hep-ph` / `hep-th` が実在する）。
   失敗の実測原因は **arXiv の HTTP 429（`Rate exceeded.`）**。
3. core は PR7 に従い `seed["note"]` に事実文を載せていたが、
   **`admin-paper-radar.js` の `renderSeed()` が `seed.note` を描画していなかった**。
   さらに「200 だが該当項目なし」の回は note すら立たなかった（note は
   `ArxivApiError` を捕まえたときだけ）。

つまり PR7（閉世界の正直さ・黙って縮退しない）がサーバ側でだけ守られ、**最後の1段で
落ちていた**。キーフレーズが空だったのは正常（供給元は当該教材の承認済み
`theory_components` のみ）で、カテゴリさえ入れば `near` でもカテゴリのみのクエリとして
成立する。

### 13.2 是正（3点）

| # | 場所 | 内容 |
|---|---|---|
| 1 | `frontend/public/js/admin-paper-radar.js` | `renderSeed()` が `seed.note` を `#pr-seed-note` に描く。文言はサーバのものを素通し（縮退の理由をフロントで発明しない）。`applySeedMeta()` は検索レスポンスで手元の note を消さない — 検索経路は seed を取り直していないので「解消した」ではなく「確かめ直していない」（provenance と同じ規律） |
| 2 | `core/paper_discovery/radar.py` | 供給元ごとに重複していた取得ブロックを `_fetch_seed_entry(seed, arxiv_id)` に集約し、**理由ごとに別の事実文**を立てる |
| 3 | `core/paper_discovery/arxiv_client.py` + `routes/paper_discovery.py` | HTTP 429 を部分型 `ArxivRateLimitedError` で区別し、検索系 4 経路の 502 detail を `_arxiv_unavailable_detail(exc)` で出し分ける |

**なぜ理由ごとに文を分けるか** — 教員の次の一手が違う。「繋がらない」なら設定・回線を
疑い、「制限されている」なら時間をおき、「該当なし」なら ID・出所を疑う（2026-09-15 の
§14.7 で、この行の「混んでいる」＝推測の言い方を「制限されている」＝観測できた事実に改めた）。

| 定数（`radar.py`） | 状況 |
|---|---|
| `NOTE_ARXIV_RATE_LIMITED` | HTTP 429（アクセスの制限）。**2026-09-15（§14.7）に「混雑」から「arXiv からアクセスを制限されているため…」へ改訂**し、問い合わせを止めていることを併記する |
| `NOTE_ARXIV_METADATA_UNAVAILABLE` | その他の到達・解釈の失敗（既存文言のまま） |
| `NOTE_ARXIV_METADATA_NOT_FOUND` | 200 で返ったが該当 ID の項目が無い（撤回・ID 誤り） |

取得に成功した回は note を立てない（毎回出る事実文は景色になる）。

### 13.3 不変条項との整合

- **PR1**（保存しない）: note はレスポンス限りの読み時導出。書き込みは増えない。
- **PR2 / PR4**（数値非表示・推定を剥がさない）: 事実文のみ。ステータスコード・
  待ち時間・残回数などの数値は出さない。
- **PR6**（外部到達は既存クライアント経由）: 宛先もスロットルも不変。**リトライを
  足さない** — 429 で待ち直すのはクライアントの仕事ではない（PD7）。
- **PR7**（閉世界の正直さ）: 本追補の主題。サーバの事実文が画面に出ることを
  ガードレールで固定した。
- 後方互換: `ArxivRateLimitedError` は `ArxivApiError` の部分型なので、区別しない
  既存の `except` 節（`compare` / `citation_search` 等）は無改修で動く。ステータスは
  502 のまま（上流の混雑を本アプリのコスト上限 429 と同じ形にしない）。

### 13.4 ガードレール

- `test_paper_radar_ui_static.py::TestSeedScoping` — `renderSeed()` が `seed.note` を
  描くこと / 縮退理由の文言をフロントに持たないこと。
- `test_paper_radar_core.py::TestResolveSeed` — 429・該当なし・成功の3系統で note が
  それぞれ立つ／立たないこと。
- `test_paper_radar_api.py` — 429 が 502 + 制限の事実文になること（文言は §14.7 で改訂）。
- `test_paper_discovery_core.py::TestThrottle` — 429 が部分型で上がること・
  リトライしないこと。

### 13.5 残る課題（本追補の非スコープ）

- 分野購読モーダル（`admin-paper-discovery.js`）には seed の概念が無いため note の
  担体も無い。検索失敗は従来どおり 502 の事実文で出る。
- 429 が続く運用（同一 IP からの集中）への対処はモジュールレベル 3 秒スロットルのまま。
  待ち時間の自動調整・`Retry-After` の解釈は PD7（リトライを持たない）と衝突するため
  行わない。
- **seed のメタデータを取り直すのは `GET /radar/seed`（モーダルを開いたとき）だけ**。
  検索ボタンの再押下では取り直さない（`run_radar_search` は条件が明示されていれば
  `fetch_arxiv=False`）。そのため `NOTE_ARXIV_RATE_LIMITED` は「検索し直す」ではなく
  「カテゴリを直接指定する / 画面を開き直す」と書く — **効かない操作を案内しない**。
  検索押下での seed 再解決は外部コールを増やすため v1 では行わない。

---

## 14. 追補 — arXiv 呼び出しの上限（2026-09-14・migration 085）

**状態: 実装済み**（`backend/core/paper_discovery/metadata_cache.py` +
`backend/db/085_paper_discovery_arxiv_metadata_cache.sql`）。

### 14.1 オーナー指示（2026-09-14）

> 教員の一連のレーダー操作で arXiv API に出ていくのは**最大1〜2回**（seed のメタ
> データ1回・検索1回）。**モーダルを開き直すたびに取り直さない**。

§13 の追補（混雑の事実文）で「429 のとき人が操作するたびにブロック窓が延びる」
循環が見えたのを受けた指示で、対処は2系統に分かれる。

1. **呼ばずに済ませる**（本節）: 外部事実の写しを持ち、新鮮なら再取得しない。
2. **429 のあとは呼ばない**（`arxiv_client` 側・別担当）: 429 を受けたあと一定時間は
   本アプリから arXiv API を**呼ばずに**混雑の事実文で返す抑制（リトライではない）。
   env は `ARXIV_RATE_LIMIT_COOLDOWN_SECONDS`（既定 600）。抑制中の `_http_get` は
   外に出ずに `ArxivRateLimitedError` を送出するので、事実文（`NOTE_ARXIV_RATE_LIMITED`）
   と 502 の写像は §13 のまま変わらない。

### 14.2 写しは CC3 と同型の設計明示例外

発見層は候補を保存しない（PD5）。本節が保存するのは**候補ではなく、arXiv API が
公開している論文メタデータそのものの写し**で、コーパスを補う論文の参照リスト
キャッシュ（CC3 / migration 077）と同じ位置づけ — 教員の判断でも候補一覧の
スナップショットでもなく、`documents.source_url` と同じ「事実の記帳」側にある。

保存するもの・しないもの:

| | 内容 |
|---|---|
| 保存する | 正規化 arXiv ID（version 抜き・主キー）、タイトル、要旨、カテゴリ、`primary_category`、著者、公開日・更新日、abs / pdf の URL、取得時刻 |
| **保存しない** | 候補の状態（`new` / `ingested` / `dismissed`）、距離帯・着地・重なり、教員・教材への従属（`user_id` / `document_id` / FK）、**取得の失敗**、版番号 |

失敗を保存しないのは、「読めなかった記録」を「読んだ結果」と取り違えないため
（077 は `fetch_status='failed'` を持つが、あちらは1シードあたり数十〜百件の参照を
引く重い呼び出しで、再取得の抑制自体に価値がある。本節の再取得抑制は 14.1 の②が担う）。

陳腐化は TTL で抑える: `DISCOVERY_ARXIV_METADATA_TTL_DAYS`（既定 30 日）。
**この日数も取得時刻も UI には出さない**（数値を見せない — PR2 / PD4）。出るのは
`seed.metadata_source` の事実ラベル（`arxiv` = 今引いた / `cache` = 写しを読んだ）だけで、
引けなかったときはキー自体を付けない（無い事実を捏造しない）。

### 14.3 呼び出し予算（教員1人の一続きの操作）

| 操作 | arXiv 呼び出し | 内訳 |
|---|---|---|
| モーダルを開く（`GET /radar/seed`） | **0〜1回** | 写しが新鮮なら 0 回。初回・TTL 切れのみ 1 回。**開き直しは 0 回** |
| 距離を選んで検索（`POST /radar/search`） | **1回** | `arxiv_client.search`。seed のカテゴリが明示されていれば seed 解決は `fetch_arxiv=False` のまま（従来どおり） |
| 候補を比較（`POST /radar/compare`） | **0〜1回** | 検索直後なら候補の要旨は写しに在るので 0 回。不足 ID があるときだけ 1 回 |
| 出所の後付け登録（`POST /radar/provenance`） | **0〜1回**（追加 0 回） | 記帳前の検証で 1 回（写しが在れば 0 回）。**記帳後の再導出は `fetch_arxiv=False`** で、要旨・カテゴリは1回目の結果から移す（従来は 2 回引いていた） |

分野購読の検索（`POST /search`）も、返ってきたメタデータを同じ写しへ書く
（レーダー・比較がそれを読める）。**写しを読むだけの経路は arXiv を呼ばない。**

### 14.4 実装

- **core `metadata_cache.py`**（FastAPI / `core.llm` 非 import）: `get_fresh` /
  `upsert_entries`（`ON CONFLICT DO UPDATE`・行削除の SQL を持たない）と、
  呼び出し側が使う fail-soft ラッパ `read_fresh` / `remember` / `ttl_days`。
  空入力では SQL を撃たない。`commit` は呼び出し側（route）の責務。
- **`radar.py`**: `_fetch_seed_entry(session, seed, arxiv_id)` が写し → arXiv の
  read-through。成功時のみ写しへ書き、`seed["metadata_source"]` を立てる。
  縮退の事実文（`NOTE_ARXIV_*`）の意味論は §13 のまま**不変**。
  `run_radar_search` は検索結果も写しへ書く。`fetch_arxiv=False` の挙動は不変
  （写しも読まない — 条件が明示されている経路の外部依存を増やさない）。
- **`compare.py`**: `fetch_ids` をまず写しから解決し、**足りない ID だけ**
  `arxiv_client.fetch_by_ids` に渡す。写しの要旨はライブ取得と同一本文なので、
  `evidence_quote` の verbatim 検査の土台（サーバが持つ要旨）は変わらない。
- **`search.py`**: 検索成功後に写しへ書く（失敗はログに残して検索は成立させる）。
- **route**: 探索経路は読み取り専用のためセッションを `commit` せずに閉じていた。
  写しだけを確定させる `_persist_arxiv_metadata_cache(session)` を
  `/radar/seed` `/radar/search` `/radar/compare` に置く（確定できなくても応答は返す）。
  `/radar/provenance` は記帳の `commit` に相乗りする。

### 14.5 ガードレール

- `test_paper_discovery_metadata_cache.py` — store の振る舞い（正規化・空入力で SQL を
  撃たない・upsert の形・外部事実だけを束縛する・fail-soft）と migration 085 の形
  （冪等・シードしない・`DELETE` なし・FK なし）。
- `test_paper_radar_core.py::TestSeedMetadataCache` — 写しが新鮮なら `fetch_by_ids` を
  呼ばない / `metadata_source` の2値 / **失敗を写しに書かない** / 検索結果を写す /
  読み書きの失敗で探索を止めない。`TestRunCompare` に写しからの解決 0 コールも追加。
- `test_paper_radar_api.py::...test_registration_resolves_the_seed_with_arxiv_once` —
  出所登録で arXiv へ出ていく解決が1回だけであること。
- `test_paper_radar_guardrails.py` — 写しモジュールの隔離（FastAPI / LLM 非 import・
  `DELETE` 不在）/ 失敗をキャッシュしない構造 / 探索経路の書き込み先が写しだけで
  あること / DDL が候補・判断を持たないこと（`test_no_migration_stores_radar_
  candidates_or_judgements` は旧 `test_no_migration_is_added_for_the_radar` の後継）。

### 14.6 非スコープ

- 写しの TTL・取得時刻・件数の UI 表示（数値を見せない）。
- 写しの明示的な無効化 UI・管理者による一括削除（行削除の口を作らない）。
- 図・PDF 本文のキャッシュ（取り込みの弁は既存経路のまま — PR3 / PD2）。
- 分野購読モーダルでの `metadata_source` 表示（seed の概念が無い）。
- 429 のクールダウンそのもの（`arxiv_client` 側・14.1 の②）。

### 14.7 ブロックの目印（2026-09-15・migration / env / アンカーなし）

**オーナー指示（2026-09-15）**: 「429 を受けたかどうか目印をつけられるか？ その場合は
明確にブロックされているという表示にするべきではないか。」

§14 の②で 429 のあとは arXiv を呼ばない窓（クールダウン）を入れたが、その事実が
**文章の中にしか無かった**。画面は「候補が見つかりません」と「seed の note」しか持たず、
制限されているのか条件が悪いのかが読めない。ここでは①状態を**機械可読の目印**にし、
②文言を「混雑」から「**制限されている**」に改め、③**呼ぶ前から分かっている**操作は
arXiv を呼ばずに 200 の事実として返す。

#### 何を返すか（API 契約）

| 場所 | キー | 値 |
|---|---|---|
| `resolve_seed()` の seed DTO | `arxiv_blocked` | **常在**。`arxiv_client.cooldown_active()` を**取得の後**に評価（この操作で 429 を受けた回も真） |
| 同上 | `arxiv_blocked_note` | 真のときだけ。`radar.NOTE_ARXIV_BLOCKED` |
| 同上 | `metadata_failure` | 取得に失敗したときだけ。`rate_limited` / `unavailable` / `not_found`（`note` の3文と1対1） |
| `POST /radar/search` | `arxiv_blocked` / `note` / `banding.note` | 制限中は `blocked_radar_result()` が通常と同じ形 + 空候補 + 事実文。seed 側と top-level は**常に同じ値**（フロントが両方読むため） |
| `POST /radar/compare` | `arxiv_blocked` / `skipped[]` / `notes[]` | 写しに要旨が揃わないときだけ空 `items` で返す。揃っていれば通常実行（arXiv 0 コール） |
| `POST /search`（購読） | `arxiv_blocked` / `note` / `closed_world_note` | 制限中は候補を空にし、閉世界の注記の**前**に事実文を置く |

定数はすべて `core/paper_discovery/radar.py`（`__all__` に載せる）:

| 定数 | 意味 |
|---|---|
| `NOTE_ARXIV_BLOCKED` | **状態**「いま制限されている / 条件を変えても解けない」 |
| `NOTE_ARXIV_RATE_LIMITED` | **出来事**「この取得が制限で失敗した」（§13 の文言を制限の言い方へ改訂） |
| `METADATA_FAILURE_{RATE_LIMITED,UNAVAILABLE,NOT_FOUND}` | `metadata_failure` の語彙 |

#### 200 と 502 の切り分け（ここが判断の要）

- **呼ぶ前から分かっている**（クールダウン中）→ **200** + `arxiv_blocked: true` + 空候補 +
  事実文。**arXiv を呼ばない・CostGate も日次カウンタも消費しない・`last_checked_at` /
  `last_search_found_new` も更新しない**（探していないのに「確かめた」と記録しない）。
- **探して断られた**（live の 429）→ **502**（§13.3 の判断を維持）。detail は
  `NOTE_ARXIV_BLOCKED`（その時点で窓が立っているので「止めています」は事実）。

0 件を返す経路が増えるのに事実文を足さなければ、「近い論文が無い」という誤読が増えるだけ
なので、**空一覧には必ず理由が伴う**（PR7）。比較で日次上限を消費しないのは、呼べない
理由がこちら側の都合ではないから（教員の持ち分を上流の制限で削らない）。

#### 文言 — 「混雑」と言わない

観測できたのは「**制限された**」という事実だけで、向こうが混んでいるかどうかは分からない。
「混雑」は待てば直る印象を与え、教員に条件をいじらせ続ける（=窓を延ばす操作を誘う）。
新しい文は①制限されている事実 ②こちらから問い合わせを止めていること ③条件を変えても
解けないこと ④時間をおいて開き直すこと、の4点だけを言う。**残り時間・回数・ステータス
コードなどの数値は書かない**（PR2）。窓の残り秒数を返す関数は `arxiv_client` の内側の
判定材料で、`radar.py` / `routes/paper_discovery.py` からは参照しない（ガードレールが
識別子の不在で固定する）。

#### ガードレール

- `test_paper_radar_core.py::TestArxivBlockedMarker` / `TestBlockedRadarResult` /
  `TestCompareRequiresArxiv` — 目印が立つ/立たない3系統・`metadata_failure` の写像・
  事実文に数字が無いこと・制限中の結果が通常と同じ形であること・写しだけで足りる比較。
- `test_paper_radar_api.py::TestArxivBlocked` — 3ルートが 200 + `arxiv_blocked: true` +
  空候補で返り、arXiv も LLM も呼ばれず日次上限も減らないこと。live の 429 は 502 のまま。
- `test_paper_discovery_api.py::TestSearchWhileArxivBlocked` — 購読検索が
  `touch_last_checked` を呼ばないこと・関連度並べ替えの埋め込みを焚かないこと。
- `test_paper_radar_guardrails.py::TestArxivBlockedMarker` — 残り秒数の識別子の不在・
  route が文言を言い換えないこと・ゲート消費が縮退判定より後ろにあること。

#### 非スコープ

- 制限の残り時間・解除予定時刻の表示（数値を見せない）。
- 自動再試行・バックオフ（PD7 — 待つかどうかは人間が決める）。
- 引用グラフ（Semantic Scholar）側の独立したクールダウン・目印。
- 制限中に検索ボタンをサーバ都合で無効化すること（UI 側の判断 — §14.8）。

### 14.8 ブロックの表示（UI）— 2026-09-15

§14.7 でサーバが返すようになった「arXiv がこの環境からの問い合わせを制限している」と
いう事実を、画面の一等地で**制限されていると読める形**にする。実装は
`frontend/public/js/admin-paper-radar.js` と `frontend/public/js/admin-paper-discovery.js`
のみ（migration / env / `data-ui-anchor` / API いずれも増やさない）。

**症状**: 制限中は候補ゼロの 200 が返るため、画面には灰色の事実行と
「この検索条件では候補が見つかりませんでした」だけが残る。§13.1 と同じ誤読
（「向こうが止めている」が「近い論文が無い」に読める）が、別の入口から再発する。

**是正（4点）**

1. **専用バナー**: 条件欄の先頭（レーダーは距離ラジオの上、分野購読は分野入力の上）に
   `#pr-arxiv-blocked` / `#pd-arxiv-blocked` を置く。枠（`--color-text-danger`）と淡い
   地色を付け、灰色の事実行と見分けられるようにする。`state.arxivBlocked` が真のとき
   だけ表示し、それ以外は領域ごと出さない。
2. **見出しは静的・本文はサーバ**: フロントが持つのは見出し2つだけ —
   制限中は「arXiv からのアクセス制限中」、届かなかった回は
   「arXiv に問い合わせできませんでした」。**見出しに数字を書かない**（待ち時間・
   回数・カウントダウンを出さない・自動再試行もしない — PR5 / PD8）。理由の本文は
   `seed.arxiv_blocked_note` / `note` / `notes[0]` / `closed_world_note` / 502 の
   `detail` をそのまま描く（言い換えない）。
3. **状態は増える方向にだけ動く**: `arxiv_blocked` が明示的に `false` のときだけ解除し、
   キーの無いレスポンスでは解除しない（§13.2 の `seed.note` と同じ規律）。502 は
   **状態コードだけで**「届かなかった」と判定する（日本語 `detail` を照合しない）。
4. **空一覧の文言を差し替える**: 制限中は「候補が見つかりませんでした」を出さず、
   サーバの事実文を一覧の位置に出す。検索ボタンは**押せるまま**にし（サーバは 200 の
   事実文を返すだけで arXiv へは出ない＝費用ゼロ）、隣の補助文だけを
   「制限中は arXiv へ問い合わせません。」に差し替える。

**マニュアル**: `docs/manual/teacher/11-admin-materials.md` の
[モーダル: 論文レーダー](../manual/teacher/11-admin-materials.md#radar-modal) と
[この条件で検索](../manual/teacher/11-admin-materials.md#arxiv-discovery-search) に各1段落。
バナーは操作要素ではない事実の区画なので `data-ui-anchor` は付けない。

**ガードレール**: `test_paper_radar_ui_static.py::TestArxivBlockedBanner` /
`test_paper_discovery_ui_static.py::TestArxivBlockedBanner`（バナーの存在位置・既定非表示・
枠と地色・見出しに数字が無いこと・本文がサーバ由来であること・`false` のときだけ解除・
502 を状態コードで拾うこと・制限中の空一覧文言の抑止・自動再試行の不在）+ 両ファイルの
`TestCacheBuster`（`?v=` を `...-20260915-1` へ更新。**JS を直して `?v=` を上げ忘れると
教員のブラウザは古い JS のまま**という 2026-09-15 の実地の失敗を固定する）。

**非スコープ**: 制限の残り時間・再開予定の表示 / 自動再試行・再試行ボタン /
制限中に検索ボタンを無効化すること（サーバの事実文を読む機会を奪う）/
引用グラフ・コーパス補完・基盤論文（Semantic Scholar 経路）への同じバナーの適用。

---

## 15. 追補 — 取得する形式（TeX ソース / PDF）と取り込み済みラベル（2026-09-16・migration / env なし）

> **状態: 実装済み**（2026-09-16。新テーブル・新列ゼロ、新しい外部呼び出しゼロ、
> LLM 呼び出しの増加ゼロ。追加は UI アンカー1件（`materials.radar-format`）のみ）

### 15.1 教員からの指摘（2026-09-16）

1. 候補一覧で**すでに取り込んだ論文が見分けにくい**（「取り込み済み」はカードの
   末尾の灰色1行にしか出ておらず、流し読みでは新着と区別が付かない）。
2. 「選択した論文を取り込む」が**常に PDF を取ってくる**。arXiv の論文ページの
   「Access Paper」には `View PDF` のほかに `HTML` と `TeX Source` があり、
   **TeX ソースを取り込みたい**。どちらを取るかはレーダーの画面で切り替えたい。
   既定は TeX。

### 15.2 なぜ TeX を既定にするのが妥当か

解析パイプラインは以前から `source_kind="tex_archive"` を第一級の入力として持つ
（`core/document_pipeline/tex_archive.py` が節・数式・図表番号・参考文献を
**原稿の記述そのもの**から組み立てる）。PDF 経路が GROBID / PyMuPDF で組版結果から
構造を復元するのに対し、TeX 経路には復元の損失が無い。数式が主題の分野では差が大きい。
`url_fetch` も当初から gzip を受理する形（`FetchedSource.source_kind`）で作ってあり、
**不足していたのは「その URL を組み立てて渡す」1本だけ**だった。

`HTML` は語彙に入れない。解析パイプラインの入力形式ではないので、選べるようにすると
「選べるのに取り込めない形式」が生まれる。

### 15.3 形式の分岐点は1箇所（`schema.source_url_for`）

```
教員の選択（tex / pdf）
   → route: _resolved_source_format()   ← 語彙外はここで 422（取得を1件も試みない）
   → pd_schema.source_url_for(arxiv_id, fmt)   ← 唯一の分岐点
        tex → https://arxiv.org/src/<id>      （= Access Paper の "TeX Source"）
        pdf → https://arxiv.org/pdf/<id>      （= "View PDF"）
   → url_fetch.fetch_source_from_url(...)      ← 形式の**判定**は実バイトのマジック
   → _accept_material_source(source_url=...)   ← documents.source_url に出所として記帳
```

- **語彙の正本**は `core/paper_discovery/schema.py::SOURCE_FORMATS = ("tex", "pdf")`。
  （**2026-09-30 改訂: 以下の「API の既定は PDF」の理由づけは §15.9 で置き換えた。現行の
  既定は TeX**。）`DEFAULT_SOURCE_FORMAT = "pdf"` は**この API の既定**（＝形式を送らない古い
  クライアントの挙動）で、「画面の既定」ではない。画面の既定 = TeX は**フロントが
  明示的に送る**。両モーダル（レーダー / `arXivから探す`）とも既定 TeX で、
  同じ語彙・同じ事実文を使う（§15.5b）。API の既定まで TeX にしないのは、
  形式を送らない経路が黙って変わるのを避けるため。
- **語彙外は 422**（`_DETAIL_INVALID_SOURCE_FORMAT`）。既定へ黙って落とすと、教員が
  選んだつもりの形式と実際に取りに行く形式が食い違ったまま解析まで進む。
- **TeX を選んでも TeX が返るとは限らない**（著者が PDF だけを投稿した論文）。URL では
  なく**取得したバイト列のマジック**で形式を決める既存の規律（`detect_source_kind`）を
  そのまま使い、PDF が返ればそのまま PDF として解析する。この可能性は選ぶ**前に**
  事実文で言う（PD6 — 起きうることを黙らせない）。
- **キュー経路も同じ URL を通る**。`ingest_queue.enqueue_items(..., source_format=)` は
  形式を**`source_url` 列に畳んで**保存し、列を増やさない（worker が知る必要があるのは
  「どの URL を取りに行くか」だけで、形式の語彙ではない）。
- **「取り込み済み」判定は壊れない**。`normalize_arxiv_id` の `_PATH_PREFIXES` は
  `src` / `e-print` を既に含むので、`/src/<id>` も `/pdf/<id>` と同じ ID へ畳まれる
  （PD5 の読み時導出）。ガードレールで固定した。

### 15.4 単一ファイル投稿の TeX ソース（`tex_archive.py` の取りこぼし是正）

arXiv の TeX Source は、**複数ファイルの投稿なら `.tar.gz`、単一ファイルの投稿なら
`.tex` を gzip しただけのバイト列**を返す。後者は `tarfile.open` で開けないため、
従来は `invalid .tar.gz TeX archive` で解析に失敗していた（取得は成功しているので、
教員には理由の分からない失敗に見える）。`_read_archive_members` に tar 失敗時の
フォールバックを足し、gzip を解いた中身に LaTeX の骨格
（`\documentclass` / `\begin{document}` / `\documentstyle`）があるときだけ
`main.tex` 1本として読む。**gzip でありさえすれば通る、にはしない**（PDF や画像を
`.tex` 扱いしないための fail-closed）。

### 15.5 取り込み済みラベル（PD5 の判定はそのまま・出し方だけ変える）

判定（`search.ingested_arxiv_ids`）もスコープの但し書き（`SOURCE_URL_NOTICE`）も
**非改変**。変えたのは出し方だけ:

- タイトルの右に緑系のバッジ「取り込み済み」（`pr-ingested-badge`）。一覧を流し読み
  しても新着と見分けられる位置に置く。チェックボックスが外れて選べないのは従来どおり。
- カード下部の1行は「この論文はすでに教材として取り込まれています。」に変え、バッジと
  同じ語を繰り返さない。
- **取り込み済みでも「arXiv で開く」リンクを出す**（従来は else 分岐で消えていた）。
  もとの論文を確認する導線を、取り込んだことを理由に塞がない。

### 15.5b 分野購読モーダル（`arXivから探す`）にも同じスイッチ

同じ `/ingest` `/ingest-batch` に合流する以上、入口によって取り込まれる形式が違うと
「同じ論文なのに解析結果の質が違う」が起き、しかも画面からは区別が付かない。そこで
`admin-paper-discovery.js` にも**同型**のスイッチを置いた（既定 TeX・保存しない・
形式変更で検索し直さない・取り込み確認欄の先頭）。

同型を**言葉のうえでも**保つため、ガードレール
`test_paper_discovery_ui_static.py::TestSourceFormatSwitch` が2ファイルの
`DEFAULT_SOURCE_FORMAT` と `FORMAT_NOTICE` の**逐語一致**を固定する
（同じ事実を2画面で言い換えると、教員は別の挙動だと読む）。JS の相互 import は
しない流儀なので、定数はそれぞれのファイルに持ち、一致をテストで縛る
（`SYNC_INGEST_MAX` / `BATCH_INGEST_MAX` と同じやり方）。

アンカーは `materials.arxiv-discovery-format`、マニュアル節は
[取得する形式](../manual/teacher/11-admin-materials.md#arxiv-discovery-format)
（意味はレーダー側の節を参照する形にして、説明を二重管理しない）。

### 15.6 不変条項との整合

| 条項 | 本追補での扱い |
|---|---|
| PR1 候補は読み時導出・保存しない | 形式の選択も**保存しない**（モーダルを開き直すと TeX に戻る。localStorage も使わない） |
| PR2 数値非表示 | 追加した表示はラベルと事実文のみ |
| PR3 取り込みは既存の弁のみ | エンドポイントは増やさない（`/ingest` / `/ingest-batch` に optional な `source_format` を足しただけ） |
| PR5 明示操作のみ | 形式を変えても検索し直さない（取り込みのときにだけ効く） |
| PR6 外部 API は既存クライアント経由 | arXiv への**呼び出し回数は不変**（形式は取り込み時の取得 URL が変わるだけで、§14 の予算に影響しない） |
| PR7 閉世界の正直さ | TeX が無い論文で PDF になることを、選ぶ前に事実文で言う |
| UF1〜UF6（URL 取得） | 許可リスト照合・SSRF ガード・マジックによる形式判定は**非改変**。`arxiv.org` の許可が要るのも従来どおり |

### 15.7 ガードレール

- `test_paper_discovery_core.py::TestSourceFormat` — 語彙（HTML を含めない）・
  正規化・URL の分岐・既定への fail-safe・**両形式が同じ arXiv ID へ畳まれること**。
- `test_paper_discovery_api.py::TestIngestSourceFormat` — 既定が PDF であること・
  TeX が `/src/` を取りに行き `documents.source_url` に記帳されること・語彙外は 422 で
  取得を1件も試みないこと・監査に形式が残ること・バッチがキューへ形式を渡すこと。
- `test_paper_discovery_worker.py::TestEnqueueItems` — キュー行の `source_url` が
  形式で切り替わること・TeX で取り込んだ論文が「取り込み済み」と判定されること。
- `test_paper_radar_ui_static.py::TestSourceFormatSwitch` — 既定 TeX・モーダルを開く
  たびに既定へ戻すこと・`source_format` を送ること・形式変更で検索しないこと・
  アンカーの担体・**クライアントで arXiv の URL を組み立てないこと**。
- `test_paper_radar_ui_static.py::TestCandidateStates` — バッジがタイトル行にあること・
  取り込み済みでも arXiv リンクが残ること。
- `test_paper_discovery_ui_static.py::TestSourceFormatSwitch` — 分野購読モーダル側の
  同型（既定 TeX・リセット・送信・アンカー・**レーダーとの既定と事実文の逐語一致**）。
- `test_document_pipeline.py` — 単一ファイル gzip が1本の `.tex` として読めること・
  LaTeX の骨格が無い gzip は従来どおり拒否されること。
- 管理UI 3点セット: マニュアル節
  [取得する形式](../manual/teacher/11-admin-materials.md#radar-format) +
  `ADMIN_UI_ANCHORS` の `materials.radar-format` + `data-ui-anchor` の担体
  （件数の正本は `test_admin_help_ui_anchors.py`）。`admin.html` の `?v=` も更新
  （§14.8 と同じ理由）。

### 15.8 非スコープ

- **形式スイッチを持たない第3の取り込み入口** — 新しく作るなら本節と同型に揃える
  （既定 TeX・保存しない・3点セット・`FORMAT_NOTICE` の逐語一致）。`source_format` を
  送らない経路はサーバ既定の PDF になる。
- 形式の教員ごとの既定値の保存（PR1 の「保存しない」に反する）。
- 同じ論文を形式違いで取り直す導線（現状は古い教材を削除してから取り直す）。
- `e-print` / `format` など `/src/` 以外の arXiv 配信 URL の使い分け。
- HTML 配信（arXiv の `Access Paper → HTML`）の取り込み。

### 15.9 2026-09-30 既定を TeX に変更（PDF への同期フォールバックと呼び出し予算）

> **状態: 実装済み**（2026-09-30。migration / env なし。新しい外部呼び出しは「TeX が使えない
> ときの PDF 取得 1 回」だけで、1件あたりの arXiv への取得は最大 2 回。LLM 呼び出しの増加ゼロ。
> UI アンカー +2（`materials.url-upload-format` / `materials.upload-language`））

**なぜ変えたか（オーナー指摘の根因 #3）**: PDF 経路の解析品質は、表示側では隠せない**上流の根**
である。PDF は組版結果から式・導出・図表を復元するので、復元の損失が以降の全ステージに乗る。
理論モジュール層の実測（CLAUDE.md「理論モジュール層」・`theory_module_layer_design.md` §3.1〜3.3）
では、同一論文（2609.15375v1）の式チェーンが **PDF 経路で 0 本 / TeX 経路で 13 本**、PDF 経路の
「式の詳細」は全部 claim の並びだった。§15.3 は「形式を送らない経路が黙って変わるのを避ける」ため
API の既定を PDF に据えたが、その結果、形式を送らない経路（キュー・古いクライアント・汎用の
「URLから取得」）ほど質の低い経路に落ちていた。黙って変わるのを避ける理由は、下の同期
フォールバックで「TeX が無い論文でも取り込みが止まらない」ことを保証すれば消える。

**何を変えたか**:

1. `schema.DEFAULT_SOURCE_FORMAT = SOURCE_FORMAT_TEX`。`source_url_for` の分岐点・語彙外 422・
   キュー行への畳み込みは不変。
2. **同期フォールバックは API 層の `api/source_resolution.py`**（`url_fetch` にも
   `core/paper_discovery/` にも置かない — 前者は「1 URL を取る」以上の方針を持たず、後者は
   `url_fetch` を import しない規約）。`fetch_arxiv_source(arxiv_id, *, source_format,
   allowed_domains) -> ResolvedSource{fetched, source_url, effective_format, fell_back, attempts}`:
   - `/src/<id>` を取る。マジックが PDF なら終わり（PDF のみの投稿 — **1 回**）。
   - gzip なら `tex_archive.load_tex_archive`（純関数・ネットワークなし）で**受理前に**読めるか
     確かめる。解析パイプラインは 202 の後で同じ関数を呼ぶので、ここで確かめないと TeX を含まない
     gzip は PDF に倒す機会が無いまま解析が失敗していた。
   - 読めない gzip・404 等の取得失敗・サイズ超過・形式不一致のときだけ `/pdf/<id>` を取る（**2 回目**）。
   - **HTTP 429 では 2 回目を取らない**。`url_fetch.RateLimitedError`（`FetchFailedError` の部分型・
     公開面の表に 1 行追加）で区別し、事実文 `DETAIL_ARXIV_RATE_LIMITED`（先頭の文は
     `radar.NOTE_ARXIV_BLOCKED` と逐語一致・数値なし）を返す。ステータスは §13 どおり 502。
   - 許可リスト由来の拒否（未設定・不許可・内部アドレス）では倒さない（同じ理由で落ちるだけ）。
3. **出所の正直さ**: `documents.source_url` は**実際にバイト列を返した URL**（フォールバック時は
   `/pdf/`）。`/ingest` の accepted 行と `upload-from-url` のレスポンスに `effective_format`
   （実バイトの形式）/ `fell_back` を足し、監査 metadata に `effective_formats` /
   `fell_back_arxiv_ids`、429 の failed 行に `rate_limited: true`。
4. 適用先: `/ingest`（`ingest_candidates`）/ キュー worker（`ingest_worker.process_item` —
   キュー行の `source_url` のパスから形式を読む）/ **汎用の「URLから取得」**
   （`upload_material_from_url` — arXiv の論文 URL（abs / pdf / src / e-print）なら
   `fetch_url_source` が「明示の `source_format` > URL のパス > 既定（TeX）」で形式を決めて
   取り直す。arXiv 以外の URL は従来どおり 1 回そのまま取る）。モーダルは arXiv の URL を
   入力したときだけ §15.5b と同型のスイッチ（既定 TeX・保存しない・`FORMAT_NOTICE` 逐語一致）を出す。
5. `FORMAT_NOTICE` を「TeX ソースが公開されていない論文や、TeX ソースとして読めない論文では、
   PDF を取り込みます。」に改めた（3 画面で逐語一致・テストで固定）。

**呼び出し予算（P-0007）**: 1件あたり arXiv への取得は **1 回（TeX が読めた / PDF のみの投稿）
または 2 回（PDF へ倒した）**。429 のときは 1 回で止まる。`MAX_FETCH_ATTEMPTS = 2` をテストが縛る。
キュー worker のアイテム間 3 秒は不変。

**非スコープ**: 同期 `/ingest`（上限 5 件）のアイテム間 3 秒の間隔（教員が待つ同期リクエストの
ため置かない）/ `arxiv_client.cooldown_active()`（検索 API の 429 クールダウン）を取り込みの
取得にも効かせること（宛先ホストが別で、取り込みを巻き添えで止めない判断。ただし取り込み側の
429 では 2 回目を取らない）/ フォールバックの事実をキュー行に列として残すこと（migration 072 は
列を持たず、ログと `documents.source_url` から読める）。

**レビュー是正（2026-10-01・migration なし）**:

- **429 停止規則（M6）**: 1 件で 429 を受けたら、**同じ操作の残りの item でも arXiv を呼ばない**。
  同期 `/ingest` は最初の 429 でループの取得を止め、残りの item を取得せずに
  `failed[]` へ `rate_limited: true` + 同じ事実文 `DETAIL_ARXIV_RATE_LIMITED` で積む（不正な ID は
  従来どおり不正 ID の事実文）。キュー worker は 429 を受けた item を事実文で `failed` にしたうえで
  **その周の取り出しを止め**、`ARXIV_RATE_LIMIT_COOLDOWN_SECONDS`（既定 600・検索クライアントの
  抑制窓と同じ設定。worker は `arxiv_client` を import しないので既定値 600 を
  `ingest_worker.DEFAULT_RATE_LIMIT_BACKOFF_SECONDS` に同じ値で持つ — 一致はテストで固定）の間は
  次の周でも claim しない。止まっている間の行は `queued` のまま（状態を書き換えない・プロセス内の
  期限だけ）。旧実装は 429 の後も同期で最大 4 件・worker で 3 秒ごとに取得を続け、制限中に
  最大 10 回叩いていた。
- **展開量の上限（M3）**: `_is_readable_tex` は同期経路で走るため、`tex_archive` の素の gzip を
  `gzip.decompress` で丸ごと展開していた（398 KB の gzip で約 930 MB）。
  `zlib.decompressobj(16 + MAX_WBITS).decompress(data, _MAX_MEMBER_BYTES + 1)` で展開しながら
  打ち切り、超過は `TexArchiveTooLargeError`（`ValueError` の部分型）。tar 経路は `getmembers()` を
  やめて逐次走査にし、member 数（`_MAX_ARCHIVE_MEMBERS`）と読み進める展開後の総量
  （`_MAX_TAR_SCAN_BYTES`）を縛る（member 本体は従来どおり `read(limit + 1)`）。
- **ホストを保つ書き換え（m10）**: URL 指定取得で arXiv の URL を `/src/` `/pdf/` に書き換えるとき、
  **パスだけ**を書き換えてホストは貼られたものを保つ（`export.arxiv.org/abs/...` →
  `export.arxiv.org/src/...`）。許可リストが `export.arxiv.org` だけのとき、元の URL は通るのに
  書き換えた URL が不許可になる食い違いを塞いだ。ID からの取り込み（`/ingest`・キュー）は従来どおり
  `arxiv.org`。
- **生成言語（m6）**: 再解析の `language` は `""` で「指定しない」への解除（`None` = 前回 run を継承。
  `cartridge_id` と同じ約束）。モーダルは「前回と同じ / 指定しない / 日本語 / 英語」。
  分野購読・レーダーの両モーダルに「生成する言語」（既定 指定しない）を置き、同期 `/ingest` にだけ
  送る（キュー登録は言語を持たない — migration なしの非スコープ。選択の横に事実文）。アンカー +2
  （`materials.arxiv-discovery-language` / `materials.radar-language`）。

ガードレール: `test_source_resolution.py`（フォールバック・429 で 2 回目なし・PDF のみの投稿は
1 回・呼び出し ≤ 2・URL の書き換え・事実文の逐語一致）/ `test_url_fetch_api.py::
TestUploadFromUrlArxivRewriting` / `test_tex_default_language_ui_static.py` / 既定を固定していた
3 本（`test_paper_discovery_api.py` の `test_default_is_tex` / `test_batch_default_is_tex`、
`test_paper_discovery_worker.py::test_unspecified_source_format_defaults_to_tex`）。
2026-10-01 是正分: `test_tex_archive_bounds.py` / `test_source_resolution.py::TestHostPreservingRewrite` /
`test_paper_discovery_api.py::TestIngestStopsAtRateLimit` /
`test_paper_discovery_worker.py::TestRateLimitBackoff` /
`test_generation_language_option.py::TestReanalyzeLanguage::test_empty_string_resets_to_unspecified` /
`test_paper_discovery_ui_static.py::TestIngestLanguageSelect`。

## 16. 追補 — 取り込みに「解析の分野」を通す（2026-10-16・migration 090）

**状態**: 実装済み（2026-10-16）。経路・検証・キュー列の正本は
[`paper_discovery_design.md` §12](paper_discovery_design.md)（レーダーの取り込みは同じ
`/ingest` / `/ingest-batch` を再利用する — PR3）。

- **起きていたこと**: レーダーから取り込んだ宇宙物理の論文（arXiv 2610.20589）の解析に、
  env 既定の `particle_physics` の正規化語彙（HQET / SM）が提示された。取り込み経路が分野を
  受けず、`domain_key`（seed の分野・監査の帰属）は解析に効かないため。
- **UI**: 取り込み区画に「解析の分野」select（アンカー `materials.radar-ingest-domain`・
  マニュアル `docs/manual/teacher/11-admin-materials.md#radar-ingest-domain`）。
- **既定値**: seed 教材の**採用 run の分野**（教材一覧の `analysis_cartridge_id`。admin.js が
  教材行のボタンに `data-analysis-cartridge-id` として載せ、`openModal(documentId, title,
  {cartridgeId})` で渡す）が選択肢に実在すればそれ、無ければ「指定しない」。起点と同じ分野の
  論文を探すのがレーダーの主用途なので既定は起点に合わせ、分野の違う論文を取り込むときは教員が
  切り替える（推定で分野を変えない）。

### 16.1 pre-mortem

| 型 | 当たるか | 対策 |
|---|---|---|
| `condition-not-propagated` | 当たる（取り込みの入口で選べても、同期受理・キュー → worker の境界で分野が落ちていた） | `paper_discovery_design.md` §12.2 と同じ（検証関数の共有・キュー列で運ぶ・境界をまたぐテスト） |

- **ガードレール**: `test_paper_radar_ui_static.py::TestRadarIngestDomain` /
  `test_paper_radar_api.py::TestRadarIngestCarriesDomain`。

## 17. 追補 — キーフレーズ供給の段（承認前の教材でも「カテゴリの新着」に縮退させない）（2026-10-16・migration / env / アンカーなし）

**状態**: 実装済み（2026-10-16）。

- **起きていたこと**（自律周回 1 周目 `uxsim/runs/radar_loop/scan-01.json`）: 起点
  「Kurtosis consistency relation in large-scale structure as a probe of gravity theories」
  （2407.01221・解析完了・承認済み部品ゼロ）で距離「近い」を検索すると、キーフレーズ候補が空で、
  arXiv クエリはカテゴリ 4 つの OR だけだった。返ったのは 4 カテゴリの**新着**（テーマと無関係）で、
  それをコサインで帯分けしたものが「近い論文」として並んだ。原因は §5.1 の供給が承認済み部品
  （`component`）**だけ**だったこと — 承認は教員の後工程なので、解析直後の教材では必ず空になる。
- **供給の段**（`radar.seed_keyphrase_candidates`。上から詰めて `MAX_SEED_KEYPHRASES` で止める・
  同じ語は先の段が勝つ・各段 fail-soft）:
  1. 承認済み理論部品（`component`・従来どおり）
  2. seed の**確認済み**（`status='confirmed'`）配置の骨格ノードラベル（`skeleton`。現行凍結版に
     無い node は使わない・concept → region の順・AI 推定 `inferred` は使わない）
  3. seed の live 主張 `theory_claims_live.concepts` の概念名（新語彙 `concept`。
     `concept_type == "symbol"` の要素を使わない・名前は英字を含み 5 語以下・48 字以下で、数字・比較記号・
     コロン・LaTeX の制御記法（`\` `$` `^` `_` `{}`）を含まないもの（`radar.is_searchable_concept_name`）・
     さらに記号様（`learner_context_common.is_symbol_like_concept` = P0-3 / V-6 の正本）を除く・
     1 主張 1 回で数えた出現回数順）
  4. 題名と**中心命題**の名詞句（新語彙 `title` / `thesis`。`radar.title_thesis_keyphrases`・最大 6 件）。
     中心命題は採用 run の `thesis_reconstruction` artifact の `central_question` + `central_thesis.text`
     （読み口は `persistence.document_run_artifacts(policy="adopted")` — 自前 SQL で `_artifacts` を読まない）。
     句は句読点・機能語・動詞で区切った 1〜4 語の連続（ハイフン語は保持・英字必須・照合は小文字・ハイフン無視・
     素朴な単数化）。併合順は ①題名と中心命題の**両方に現れる**句（出所 `title`）→ ②中心命題に 2 回以上
     現れる句（`thesis`）→ ③題名の残りの句（連なり全体のみ・`title`）で、各群とも 2 語以上の句を先に、
     丸ごと 1 語の句（`kurtosis`・`DHOST`）を最後に採る。採った句と包含関係にある句は採らない。
     日本語の中心命題からは何も足さない（訳語を捏造しない）。
- **2026-10-16 の再是正（実データ `scan-03.json`）**: 初版は段③が `visible_concept_names` を通すだけで、
  2407.01221 では記号を除いた残りが「Conditional joint forecast: approximately 0.62 ≤ λ ≤ 1.41」のような
  文で、これが `all:"…"` に入って該当ゼロになった（`theory_components_live` は 0 行、concepts は 129 行の
  ほぼ全部が `concept_type: "symbol"`）。また題名は「①〜③が空のときだけ」だったので、壊れた段③が題名を
  締め出していた。段③の検査を厳しくし、題名を中心命題と併合した段④として常に走らせるよう改めた。
  参考: 教員が手で kurtosis / consistency relation / modified gravity / large-scale structure を足した near は
  10 件中 9 件が主題の論文だった（`scan-01b-manual-keyphrases.json`）— 段④が出す句はこれに相当する。
- **承認前の部品（candidate）は使わない**（確定は人間 — PD3 / KR2）。主張の概念は「確定した語」
  ではなく「検索語の候補」としてチップに出し、出所（主張の概念から / 題名から / 論文の中心命題から）を必ず表示する。
  教員はチップで外せる（P4 — 外した状態も保持）。
- **事実文**: seed DTO に `keyphrase_supply: {sources, note}` を additive に足す（`note` は
  承認済み部品が無いとき `NOTE_KEYPHRASE_SUPPLY_WITHOUT_APPROVED`・何も取れないとき
  `NOTE_KEYPHRASE_SUPPLY_EMPTY`・承認済み部品から取れたときは `None` = 景色にしない）。
  検索が距離 near で有効なキーフレーズ無しに走ったときは `result["keyphrase_note"]`
  （`NOTE_SEARCH_WITHOUT_KEYPHRASES`「キーフレーズが無いため、テーマでは絞り込まず、カテゴリの
  新着から取った候補です。」）を付け、`closed_world_note` は置き換えない。mid / far はもともと
  カテゴリだけの網なので付けない。数値は書かない（PR2）。
- **語彙**: `schema.KEYPHRASE_SOURCES` に `concept` / `title` / `thesis` を追加（`manual` は末尾のまま）。
  表示ラベルの正本は新設 `label_vocab.KEYPHRASE_SOURCE_LABELS`、`admin-paper-radar.js` /
  `admin-paper-discovery.js` の `SOURCE_LABELS` は逐語ミラー（従来欠けていた `alias` も揃えた）。
  分野購読の供給（`vocab._SUPPLIERS`）には 3 語彙を入れない（分野単位で主張・題名・中心命題を引く意味が無い）。
- **UI**: キーフレーズ区画に供給の事実文 1 行（`#pr-keyphrase-supply-note`・サーバ文言の素通し）、
  検索条件の注記行に `keyphrase_note`。事実表示なので `data-ui-anchor` は付けない。`admin.html` の
  `?v=` を上げた。

### 17.1 pre-mortem

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 「承認済み部品がある」を「検索条件が組める」の代理にしていた | `completion-defined-by-proxy` | 当たる。供給の段を足し、承認ゼロでも主張・題名から条件が組める。代理だけが立つ状態（承認ゼロ・主張あり）をテストで作る |
| 上流（供給）の欠落が下流で「近い論文」の並びに化けていた | `upstream-loss-masked-downstream` | 当たる（原理 `route-all-surfaces-through-one-point` の「上流の損失は上流の入力を変える」）。帯分けやラベルを下流で読み替えず、seed の供給を直した。それでも空なら `keyphrase_note` で縮退を言う |
| 語彙表を 1 つ足す（出所ラベル） | `duplicate-canonical-sources` | 当たる。正本を `label_vocab` に置き JS 2 本は逐語ミラー、一致を `test_paper_radar_ui_static.py::TestKeyphraseSupply` で固定 |
| 段③の「概念」の名前が検索語として使えるかを確かめずに条件へ入れた（文・数式片が `all:"…"` に入り該当ゼロ） | `contract-changed-one-side`（検索語としての契約を、供給する側が確かめていなかった） | 再是正で当たった。供給する側（radar）で検索語の契約（英字・語数・字数・記号なし）を検査し、実データの形（記号だらけ + 文のような概念）を写したテストで固定 |
| 後段（題名）を「前段が空のときだけ」にした | `completion-defined-by-proxy` | 再是正で当たった。「前段が何か返した」を「検索条件が組めた」の代理にしていた。題名・中心命題の段は常に走らせ、上限で切る |
| 承認前の語を条件に使う | 確定の担当（`human-decision-authority`） | candidate の部品は使わない。主張の概念は確定扱いにせず出所付きのチップ（教員が外せる） |

当たらない族: 再実行と削除（読み時導出のみ・保存しない = PR1）／外部 API の予算（arXiv 呼び出し回数は不変）。

- **ガードレール**: `test_paper_radar_core.py::TestSeedKeyphraseSupply` /
  `TestSearchWithoutKeyphrasesNote`、`test_paper_radar_api.py`（素通し 2 件）、
  `test_paper_radar_ui_static.py::TestKeyphraseSupply`、
  `test_paper_discovery_core.py`（語彙とラベル表の一致）。
- **非スコープ**: 主張の概念を分野購読の供給へ足すこと / 題名・中心命題の日本語処理（英語の句だけを取る）/ 段ごとの上限配分 / 品詞解析（機能語・動詞の表で区切る素朴な規則に留める）。

## 18. 追補 — AI 比較が「近くない」を言えるようにする（2026-10-16・migration / env / アンカー / LLM コールなし）

### 18.1 背景

uxsim の論文レーダー周回（「uxsim/runs/radar_loop/judge-scan-01.md」 §4 仮説 5・「judge-scan-02.md」 §4 の 5）で、
起点と主題が無関係な候補（B 中間子崩壊・ホログラフィー等）でも `common_ground` が「理論と観測を結ぶ」
「関係式を導く」のような一般論で毎回埋まり、`overlaps` は全件空だった。ペルソナは「論文に書かれていない近さを
書いているように見える」と読んだ。原因は §12.4 の structured output が共通点 1 文を事実上必須とし、
「目立った共通点は要旨から読み取れない」と書く出口が無かったこと（型 `fallback-fabricates-missing-link`）。
仮説文体と `caveat` は守られていた（PR4）が、空を空と言わないのは PR7 の精神に反する。

### 18.2 是正（`backend/core/paper_discovery/compare.py`）

- structured output の各 item に **`relation_kind`** を追加。語彙は `shared_subject`（同じ対象）/
  `shared_method`（同じ方法）/ `adjacent`（隣り合う話題）/ `no_notable_common_ground`（目立った共通点なし）。
  語彙と日本語ラベル `RELATION_LABELS` の正本は当面 compare.py（`label_vocab` は別作業が編集中だったため。
  移すときは逐語で移し二重に持たない）。
- プロンプト: 「要旨から読み取れる具体的な共通点が無ければ `no_notable_common_ground` を選び `common_ground` は
  空文字にする」「一般論（『理論と観測を結ぶ』等）で埋めない」「書くなら要旨に出てくる具体的な対象・手法の名前を
  1 つ以上含める」を明示（PR4 の既存制約文は逐語のまま）。
- validator（`_normalize_relation` / `common_ground_is_grounded`、純関数）:
  - 語彙外の `relation_kind` は `adjacent`（分類できなかった関係として残す）。
  - `no_notable_common_ground` のとき `common_ground` を空に正規化。
  - それ以外で `common_ground` が **soft 検査**に落ちたら共通点を落として `adjacent` へ降格（**item は保持** = P4。
    差分・重なりの逐語検査は従来どおり）。soft 検査 = ①供給した部品ラベルが共通点に現れる、または
    ②共通点の「具体的な語」（ラテン・ギリシャ文字・数字・記号の 2 文字以上 / カタカナ 3 文字以上、汎用語を除く）が
    候補要旨か seed 素材に casefold の部分一致で現れる。
- DTO: 各 item に `relation_kind` と `relation_label`（日本語）を additive 追加。`common_ground` キーは残す
  （後方互換）。`caveat`・LLM 回数（1 コール）・CostGate・arXiv 予算（§14）は不変。数値は足さない（PR2 / PE10）。

### 18.3 限界

- 漢字だけで書かれた共通点（「状態方程式を扱う」等）は、部品ラベルに一致しない限り具体語を拾えず降格する
  （漢字の一般語「理論」「観測」と区別する決定論の手がかりが無いため）。誤って落とす側に倒しており、
  item と差分は残るので情報は失われない。
- soft 検査は語の出所を見るだけで、共通点の主張が正しいかは検査しない（正しさの権威は要旨の原文 = PR4）。

### 18.4 フロント（2026-10-16 に §19 D で実装済み）

`admin-paper-radar.js` は別作業が編集中のため、描画は次の作業に回す。予定:
①各候補の比較見出しに `relation_label` を非操作チップで出す ②`relation_kind === "no_notable_common_ground"` のときは
共通点欄に `relation_label` の事実文を出し、空欄や「共通点なし」の警告色にしない ③`common_ground` が空で
`overlaps` も空の `adjacent` は共通点欄を描かない（現行の `!overlaps.length && !item.common_ground` 分岐で足りるかを
確認）。`test_paper_radar_ui_static.py` に契約を足すのもこのときに行う。`admin.html` の `?v=` を上げること。

### 18.5 pre-mortem

| 型 | 当たるか | 見立て | 確かめ方 |
|---|---|---|---|
| `fallback-fabricates-missing-link` | 当たる（本追補の主型） | 対応（共通点）が無いときに一般論で代替し、「共通点あり」と読まれる | 無関係な候補で `no_notable_common_ground` → 空、一般論 → `adjacent` 降格をテストで固定（`test_paper_radar_compare.py`） |
| `duplicate-canonical-sources` | 当たり得る | 関係語彙・ラベルを compare.py に置いたため、将来 `label_vocab` / JS に二重定義が生まれる | 移設時は逐語移動。現時点で JS はラベルを持たず DTO の `relation_label` を素通しする方針（§18.4） |
| `contract-changed-one-side` | 当たり得る | DTO にキーを足したのでフロント・ハーネスの投影が追随しないと新キーが見えない | additive のみ・既存キー不変。フロント追随は §18.4 の TODO |
| `untrusted-input-reaches-instruction` | 該当なし | 要旨の扱い（`UNTRUSTED_SOURCE_NOTICE`・区画隔離）は不変 | — |

### 18.6 テスト

`backend/tests/test_paper_radar_compare.py`（no_notable → 空・一般論の降格・具体的共通点は保持・部品ラベル一致は保持・
語彙外は adjacent・語彙とラベルの一致・プロンプトの出口文）。`test_paper_radar_core.py::TestCompareValidator` の
2 件（共通点の期待値・DTO キー集合）を新挙動に追随させた。
- 2026-10-16（IK-0709）: 起点の承認済み部品がゼロのときはプロンプトから「下の部品リスト」への言及を外し、「`overlaps` は空配列・
  `component_label` を作らない」を明示する（部品があるときの契約フレーズは逐語不変）。
- 同（IK-0709）: 教材名が `arXiv-NNNN.NNNNN(vN)` 形なら LLM へ渡す題名は `display_title` → `arxiv_title`（写し）を優先する
  （`compare.seed_prompt_title`・追加取得なし・写しが無ければ教材名のまま）。テストは `test_paper_radar_compare.py` に 2 件。

## 19. 追補 — 第 2 波: 縮退の事実文・カテゴリの既定・取り込み後の導線（2026-10-16・migration / env / LLM コールなし）

**状態**: 実装済み（2026-10-16）。課題 IK-0700 / IK-0701 と審判 「uxsim/runs/radar_loop/judge-scan-01.md」 §4・
「judge-scan-02.md」 §4 の仮説を、PR1〜PR8 / PD1〜PD8 の範囲内（新テーブル・新 LLM コール・arXiv 追加呼び出しなし）で
解く。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| A 縮退の事実文 | 検索結果に `facts[]`（事実文・数値なし）を additive に足す。①起点の論文を含むコースに分野の地図が無い（`FACT_RELATION_NO_DOMAIN`）②分野はあるが凍結版のベクトル索引が無い（`FACT_RELATION_NO_ANCHORS`）③承認済み部品ゼロで重なりチップが出ない（`FACT_NO_APPROVED_COMPONENTS`）。何も欠けていなければキーを付けない（景色にしない）。UI は候補一覧の上の `#pr-facts` に素通し | `radar.relation_facts` |
| B カテゴリの既定 | arXiv 由来は主カテゴリ（`primary_category`・無ければ先頭）だけ `enabled`、副カテゴリは `enabled=false` のチップ（打ち消し表示・クリックで足す — PD3）。`seed.categories` は既定で有効な集合、`seed.category_candidates` が全件。購読由来は全件有効。検索は有効なチップだけを送る | `radar.category_candidates` / `admin-paper-radar.js::enabledCategories` |
| C 取り込み済みの導線 | 「取り込み済み」を `search.visible_ingested_documents(session, *, visible_document_ids)` で判定（可視性は必須引数・`ANY(:visible_ids)` で問い合わせの内側に強制・空は SQL 非発行）。route が `services.list_visible_document_ids` を注入。見える教材には `document_id` / `material_id` / `analysis_status` を additive。見えない他教員の取り込みは `new` のまま（本人が取り込める・存在を漏らさない）。JS は取り込み済み・受理直後のカードに「この教材の行へ」「グラフレビュー」「位置づけ（分野マップ）」「この論文を起点に探す」（`openModal` で seed を切替 = 連鎖）。解析未完了・失敗は理由付きで無効。解析状態は教材一覧の再読込（`renderMaterials` → `PaperRadar.onMaterialsUpdated`）で引き直し、ポーリングしない（PR5） | `search.visible_ingested_documents` / `admin-paper-radar.js::ingestedActionsHtml` |
| D 関係の種類 | compare の `relation_label` を共通点ブロックの先頭にチップで出す。`no_notable_common_ground` は label（「要旨からは目立った共通点が読み取れません」）を事実文 1 行で出し、共通点欄・空の注記を出さない。JS に日本語ラベルを持たない（§18.4 を解消） | `compare.RELATION_LABELS` |
| E 題名がファイル名 | 推定 ID の arXiv 題名の照合相手に、教材名に加えて採用 run の `document_structure` artifact の論文題名（`persistence.document_run_artifacts(policy="adopted")`）を使う。一致すれば `title_match=true` + `title_match_via="paper_title"`（自動記帳の条件 = 完全一致は不変）。教材名がファイル名様（`arXiv-NNNN.NNNNNvN`）で arXiv 題名が引けたら `seed.display_title` + `seed.title_note`（`NOTE_SEED_TITLE_IS_FILENAME`）を返し、見出しは arXiv の題名・教材名は添える（書き換えない — PR1） | `radar.resolve_seed` / `radar.title_looks_like_filename` |

- **UI アンカー**: `materials.radar-ingested-open-material` / `materials.radar-ingested-graph-review` /
  `materials.radar-ingested-landscape` / `materials.radar-chain-seed`（マニュアル
  `docs/manual/teacher/11-admin-materials.md` の同名 anchor・件数の正本は `test_admin_help_ui_anchors.py`）。
  事実文（`#pr-facts` / `#pr-seed-title-note` / 受理直後の一行）にはアンカーを付けない。
- **グラフレビュー・位置づけは、レーダーのモーダルを閉じてから開く**（全画面モーダルを重ねない）。
  連鎖は同じモーダルを新しい起点で開き直す（前の候補・比較結果は保存されていないので消える — PR1 / PR4）。

### 19.1 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 縮退の事実は DTO にあるが画面に届いていなかった | `available-but-unwired` | 当たる（A の主型）。サーバが文を持ち、UI は素通し。条件ごとに出る／出ないをテストで固定（`TestRelationFacts`） |
| 取り込みで操作列が途切れる | `last-mile-missing` | 当たる（C・IK-0700）。受理応答の `material_id` と見える教材の `document_id` を画面まで運び、既存の入口（教材行・グラフレビュー・位置づけ・`openModal`）へつなぐ。新しい API なし |
| 「取り込み済み」の判定が全域 | `scope-widened-silently` / `condition-not-propagated` | 当たる（C・IK-0701）。原理 `enforce-condition-in-query-with-required-argument` を consulted: 可視性を必須引数で受け問い合わせ内で強制・判定不能は閉じる（見えない対象を取り込み済みにしない）。分野購読・基盤論文・引用検索の `ingested_arxiv_ids` は v1 では従来どおり（非スコープ・下記） |
| 副カテゴリの等価 OR が網を黙って広げていた | `scope-widened-silently` | 当たる（B）。既定を主カテゴリに狭め、広げるのは教員のクリック（PD3） |
| 「教材のタイトル」が表示名と論文題名の 2 つを指していた | `same-name-different-referents` | 当たる（E）。照合相手を論文題名にも広げ、表示の見出しも分けた。教材名は書き換えない |
| 関係ラベルの語彙を JS にも持つ | `duplicate-canonical-sources` | 当たり得る（D）。JS は `relation_label` を素通しし、語彙値 `no_notable_common_ground` の比較だけを持つ。ラベル文字列が JS に無いことをガードレールで固定 |
| DTO に additive なキー（`facts` / `category_candidates` / `display_title` 等） | `contract-changed-one-side` | 当たり得る。既存キーは不変・新キーは optional でフロントは欠落で例外を出さない。記帳後の seed（`_merge_fetched_metadata`）にも新キーを移す |

当たらない族: 再実行と削除（読み時導出のみ・保存しない）／外部 API の予算（arXiv の呼び出し回数は不変 — 起点の切替は
モーダルを開くのと同じ ≤1 回で、写しがあれば 0）。

### 19.2 非スコープ・残り

- 分野購読（`arXivから探す`）・基盤論文（レンズ C）・引用検索の「取り込み済み」は全域判定のまま（IK-0701 の
  レーダー以外の面）。同じ `visible_ingested_documents` に寄せるのは次の波。
- 取り込み済みの教材の部品・配置・理論モジュールの要約をカードに出すこと（UX_BASELINE §6 の 5）— 導線だけ足し、
  中身は各画面で読む。
- 帯分けの母集団が「網の中の新着の先頭」である性質（judge-scan-02 §4 の 4）は不変（取得件数を増やすと §14 の予算に触れる）。

### 19.3 ガードレール

`test_paper_radar_core.py::{TestVisibleIngested,TestCategoryDefaults,TestRelationFacts,TestSeedTitleIsFilename}` /
`test_paper_radar_api.py::TestRadarSearch`（可視集合の注入・facts の素通し）/
`test_paper_radar_ui_static.py::TestRadarWave2` / `test_paper_radar_guardrails.py::TestWave2Guardrails` /
`test_paper_discovery_core.py::TestVisibleIngestedDocuments`。

## 20. 追補 — 第 3 波: 当たらなかった語・網を広げる語・要旨からの供給・帯と関係の読み方（2026-10-16・migration / env / アンカー / LLM コール / arXiv 呼び出しなし）

**状態**: 実装済み（2026-10-16）。審判 「uxsim/runs/radar_loop/judge-scan-05.md」 §5 の仮説 1〜5（記録 `scan-05.json`）を、
PR1〜PR8 / PD1〜PD8 の範囲内（新テーブル・新 LLM コール・arXiv 追加呼び出しなし）で解く。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| A 当たらなかった語 | near の検索結果に `keyphrase_hits: [{text, hit, widens}]`（有効な語ごとの**真偽だけ**・件数なし = PD4）を additive に足す。照合は候補カードの一致チップと同じ `search.matched_keyphrases`。候補ゼロでは付けない（「どれにも当たらない」と「候補が無い」を区別できないため）。どれにも当たらなかった語があれば `keyphrase_hits_note`（「この検索で取得した候補のどれにも当たらなかった語: kurtosis, gravity theories」）。UI は検索条件区画のチップ直下 `#pr-keyphrase-hits-note` にサーバ文言を素通しし、当たらなかった有効なチップに「当たらず」の印を添える（外した語の打ち消し表示とは別 — 外したのではない） | `radar.keyphrase_hits` / `radar.keyphrase_hit_notes` |
| A' 照合の表記ゆれ | 一致チップの照合でハイフンと空白の連なりを同じ区切りとみなす（`large-scale structure` が `Large Scale Structure` に当たる）。judge-scan-05 で「一致チップが空なのに候補に入った」2 件の原因候補 | `search.matched_keyphrases`（`_match_form`） |
| B 網を広げる語 | 併合順で「題名と中心命題の両方に出る句」の次に「題名か中心命題に出て、**要旨にも出る**句」を置き、各群の中でも要旨に出る句を先にする。高頻度の汎用語は決定論では判定できないので、事後に「取得した候補の**すべて**に当たった語」（候補 3 件以上・有効な語 2 語以上のときだけ — 1 件・1 語では自明）を `widens: true` とし、`keyphrase_widening_note` で事実として言う。**自動では外さない**（PD3 — 外すのは教員のクリック） | `radar.title_thesis_keyphrases`（`summary=`）/ `radar.keyphrase_hits` |
| C 要旨からの供給 | 段④の材料に seed の arXiv 要旨を足す。要旨は `resolve_seed` が解決したもの（写しを先に読む `_fetch_seed_entry` 経由）だけで、`fetch_arxiv=False`・取得失敗では空のまま（写しを読み直さない・本文チャンクで代替しない）。`seed_keyphrase_candidates` を直接呼ぶときは写し（`metadata_cache.read_fresh`）だけを読む。要旨にしか出ない句は採らず、固有名の 1 語（全部大文字の略号か文頭以外の大文字始まり・英字のみ・3 字以上・機能語でない・同じ語が小文字で現れない）を**別枠**で最大 2 件通す（`DHOST` / `Horndeski`）。出所は `thesis` のままで、ラベル正本 `label_vocab.KEYPHRASE_SOURCE_LABELS["thesis"]` を「論文の中心命題・要旨から」に改め、JS 2 本の逐語ミラーを追随させた | `radar.proper_noun_keyphrases` / `radar._cached_seed_summary` |
| D 帯と関係の読み方 | 帯ラベルの付いた候補があるとき、検索結果に `reading_note`（「距離帯は取得した候補の中での要旨どうしの近さ、関係の種類は AI が要旨を読んだ結果で、同じ語でも別の対象を指していることがあります。」+ near は「近い帯の候補は要旨の近さの順に並んでいます。」/ mid・far は「候補は arXiv の新着順に並んでいます。」）を付ける。関係の種類は比較の結果で検索時点には無いので、画面は比較結果の `relation_label` がいま一覧にある候補に付いたときだけ `#pr-facts` に足す（帯と関係ラベルが同時に出るときだけ）。比較結果で帯やチップを書き換えない（PR1 — 推定で事実を上書きしない） | `radar.reading_note` / `admin-paper-radar.js::hasRelationLabels` |

- 事実文はすべてサーバ（`radar.py`）の定数で、JS は素通しする（「当たらなかった語: 」等の文を JS で組み立てない — ガードレール）。
  チップの印「当たらず」だけは操作要素の装飾として JS に持つ。事実表示なので `data-ui-anchor` は付けない。`admin.html` の `?v=` を上げた
  （ラベルのミラーが変わった `admin-paper-discovery.js` も）。

### 20.1 pre-mortem

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 語ごとの当たり外れは候補カードから導出できたが、語のチップに戻されていなかった | `available-but-unwired` | 当たる（A の主型）。サーバで真偽を導出して DTO に載せ、文もサーバが持つ。UI は素通しと印だけ |
| 汎用語 1 つが網の幅を決め、「近い」に主題の離れた候補が並ぶ | `scope-widened-silently` | 当たる（B）。併合順で要旨にも出る句を先にし、全件に当たった語を事実で言う。PD3 の裏返し（AI が条件を狭める）を避けるため自動で外さない |
| 供給段で落ちた固有名（DHOST / Horndeski）が、下流の「重力側の候補なし」として黙って現れる | `upstream-loss-masked-downstream` | 当たる（C）。下流（帯・比較）を読み替えず、上流（供給）の材料に要旨を足した。要旨が無いときは何もしない（チャンクで代替しない） |
| 帯「近い」は取得した範囲の中の相対で、主題との近さのように読まれる | `extent-decided-by-proxy-signal` | 当たる（D・IK-0706）。帯の母集団（取得の網）は変えず、読み方の事実文で部分対応。母集団の問題は残る |
| 語の一致・要旨の近さ・AI の読みの 3 つが同じカードで根拠の区別なく並ぶ（「近い」と「目立った共通点なし」が矛盾して見える） | `same-name-different-referents` | 当たる（D）。同名の別概念は語の一致では区別できないことを、帯と関係ラベルが同時に出るときの 1 行で言う |
| 出所ラベルを変える（`thesis`） | `duplicate-canonical-sources` | 当たる。正本 `label_vocab` を変え、JS 2 本の逐語ミラーを同時に更新（`TestKeyphraseSupply` の一致検査・`test_label_vocab_guardrails.py::TestKeyphraseSourceLabelThesis`） |
| 一致チップの照合を変える（ハイフン） | `contract-changed-one-side` | 当たり得る。照合は `search.matched_keyphrases` 1 箇所で、分野購読の一致チップと `keyphrase_hits` が同じ関数を通る（食い違いを作らない） |
| DTO に additive なキー | `contract-changed-one-side` | 当たり得る。すべて optional・既存キー不変・フロントは欠落で例外を出さない |

当たらない族: 再実行と削除（読み時導出のみ・保存しない = PR1）／外部 API の予算（要旨は既に解決したものか写しだけを読み、arXiv の呼び出し回数は不変）。

### 20.2 限界・非スコープ

- 固有名の判定は素朴な規則で、`Gaussian` / `Fourier` のような一般的な固有名も通り得る（別枠 2 件で上限を置く）。品詞解析はしない。
- 「網を広げる語」は全件に当たった語だけで、6 / 10 件に当たった汎用語（judge-scan-05 の `large-scale structure`）は言わない
  （割合の閾値は数値の判断になり、PD4 と衝突しやすい。実測を見て判断）。
- 帯の母集団（取得が網の中の新着の先頭に限られる）は変えていない（IK-0706 は open のまま）。
- 分野購読モーダル（`arXivから探す`）の当たり外れの事実文は v1 では足していない（照合のハイフン正規化だけ共有）。

### 20.3 ガードレール

`test_paper_radar_core.py::{TestProperNounKeyphrases,TestSummaryMergeOrder,TestKeyphraseHits,TestReadingNote}` /
`test_paper_radar_api.py::TestRadarSearch::test_keyphrase_hits_and_reading_note_pass_through` /
`test_paper_radar_ui_static.py::TestRadarWave3` / `test_label_vocab_guardrails.py::TestKeyphraseSourceLabelThesis`。

## 21. 追補 — 第 4 波: 供給の品質・照合の語境界・取り込み済み候補の配置の対比・構造照合の成立条件・連鎖の表示（2026-10-16・migration / env / アンカー / LLM コール / arXiv 呼び出しなし）

**状態**: 実装済み（2026-10-16）。審判 「uxsim/runs/radar_loop/judge-deep-05.md」 §③④ と 「judge-scan-06.md」 §④（記録
`scan-06.json` = 2610.08908 を起点にした連鎖）を、PR1〜PR8 / PD1〜PD8 の範囲内で解く。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| A 供給の品質（IK-0703 再々是正） | 連鎖した起点で `P` / `B` / `Gpc` / `averaging` / 「Fisher forecast of covariance-modeling impact」が供給され網が広がった（中心命題の数式区切りの崩れで式の 1 字が「丸ごと 1 語の句」になり、単位が文中の大文字語として固有名に数えられた）。機械供給（`concept` / `title` / `thesis`）に検索語の契約を足す: 1 語は 3 字以上・一般動詞の動名詞（averaging / binning / modeling 等の表）でない・単位と「数字 + 単位」でない・**中心命題と要旨から取った 1 語は大文字を含む**（`FoG` は可）。概念名は 4 語 40 字まで（旧 5 語 48 字）。教員が承認した部品・確認した配置（`component` / `skeleton`）には掛けない（PD3）。落ちた語はチップに出さない（候補として作られた時点で検索語の契約を満たさない — 外した語の打ち消し表示とは別） | `radar.is_quality_keyphrase` / `CONCEPT_PHRASE_MAX_*` |
| A' 照合の語境界 | 一致チップと `keyphrase_hits` の照合（`search.matched_keyphrases`）が大小無視の部分一致で、`P` / `B` が「すべての候補に当たった」と読めた（照合の人工物）。語境界付きの正本 `episteme_graph.agents.alias_matching.text_mentions_alias` に寄せる: 3 字以下は大小区別の単語完全一致、それ以外は大小無視の語境界一致。ハイフンと空白の同一視（§20 A'）は照合形の前処理で維持。分野購読の一致チップにも効く（同じ関数）。部分一致で当たっていた複数形（`galaxy` → `galaxies`）は当たらなくなる | `search.matched_keyphrases` |
| B 配置の対比 | 取り込み済み候補（閲覧できる・`document_id` あり）に `placement_contrast: {shared, seed_only, candidate_only, skeleton_version}` を読み時導出で載せる。各項目は `{label, provenance_label}` だけ（ノード名と「AIによる推定（未確認）」/「教員確認済み」・weight / confidence / 件数なし = PR2 / LS5）。使う配置は `confirmed` / `inferred`、現行骨格にあるノードだけ（ラベルを捏造しない）。重なりは**両側とも確認済み**のときだけ「教員確認済み」。片側に配置が無ければ `placement_contrast_note`（「起点の論文は／この論文はまだ分野の地図に配置されていないため、配置を対比していません。」）。読み口は `core.landscape.store.list_for_documents` を起点 + 候補で 1 回・骨格は `atlas_store.load_learner_skeleton`。UI は取り込み済みカードの導線の手前に 3 行 + 骨格版 1 行 | `radar.placement_contrast` / `radar.attach_placement_contrasts` / `admin-paper-radar.js::placementContrastHtml` |
| C 構造照合の成立条件 | 起点の理論モジュールが未保存だと、候補側の related は「同じ構造の論文は見つかっていません」と閉世界の不在だけを言った。①レーダーの取り込み済みカードに `module_match_note`（「起点の論文の理論モジュールがまだ保存されていないため、構造は照合していません。」/ 候補側が未保存なら「この論文の…」。両側保存済みなら付けない）②`GET .../theory-modules/related` に optional `peer_document_id`（照合の相手。閲覧できるものだけ・解決できなければ無視）を足し、相手が未保存なら `FACT_RELATED_PEER_UNSAVED`（題名付き）を `FACT_RELATED_NONE` の**前に**置く。保存の有無は `structural_matches.documents_with_saved_modules`（live ビュー・空入力は SQL 非発行）の 1 箇所。再解析は代行しない | `core/theory_modules/related.py` / `core/library/structural_matches.py` / `radar.attach_module_match_notes` |
| D 連鎖の表示 | 「この論文を起点に探す」で開き直したとき、seed ヘッダに「前の起点: <題名>（この論文はその『近い論文』として取り込まれました）」を 1 行（`#pr-seed-previous`）。前の起点は JS 内の呼び出し履歴（開き直す前の起点の題名）だけで、保存も送信もしない。教材行から開いたときは出さない | `admin-paper-radar.js::openModal` / `renderSeed` / `bindIngestedActions` |

`admin.html` の `?v=` は `paper-radar-20261016-wave4`。

### 21.1 pre-mortem

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 供給の契約を足す（A） | `upstream-loss-masked-downstream` | 当たる（A の主型）。数式区切りの崩れ（上流）が供給語の 1 字として下流の網を広げた。上流の数式区切りは本書の範囲外なので、供給側で検索語の契約を明示的に検査する。崩れ自体は残る |
| 照合を語境界の正本に寄せる（A'） | `rule-enforced-per-surface` | 当たる。照合規則を面ごとに持たず、cartridge 別名照合と同じ正本 `alias_matching` を通す（原理 `route-all-surfaces-through-one-point` を参照した） |
| 照合を変える（A'） | `contract-changed-one-side` | 当たり得る。一致チップ・`keyphrase_hits`・分野購読の一致チップが同じ `search.matched_keyphrases` を通るので片側だけ変わらない。複数形の取りこぼしは既知の副作用（§21.2） |
| 両教材の配置は読めるのに対比が無い（B） | `available-but-unwired` | 当たる（B の主型・IK-0750）。既存の読み口から対比を組み、取り込み済みカードまで運ぶ |
| 片側の欠落が「見つからない」に畳まれる（C） | `upstream-loss-masked-downstream` | 当たる（C の主型・IK-0751）。照合の相手を明示の入力にし、欠けた側を名指しする |
| 出所ラベル（B） | `duplicate-canonical-sources` | 当たり得る。ラベルは `core.landscape.schema.provenance_label` の正本を使い、JS は素通し（表をミラーしない） |
| 前の起点（D） | `stale-derivative-served` | 当たり得る。保存しないので古くならない。教材行から開き直すと消える（`openModal` が options 無しで初期化） |
| DTO に additive なキー | `contract-changed-one-side` | 当たり得る。すべて optional・既存キー不変・フロントは欠落で何も描かない |

当たらない族: 再実行と削除（読み時導出・保存なし = PR1）／外部 API の予算（配置・保存行は DB の読みだけ・arXiv / LLM の呼び出し回数は不変）／確定の担当（配置の確定・再解析を代行しない）。

### 21.2 限界・非スコープ

- 単位表・動名詞表は素朴な一般表で、分野固有の単位・一般語は漏れ得る。漏れた語は教員がチップで外せる（PD3）。
- 語境界照合で、部分一致に頼っていた複数形・語尾変化（`galaxy` → `galaxies`）は一致チップに出なくなる（arXiv の `all:` は語幹で当てるので、
  画面の一致チップがやや控えめになる）。
- 配置の対比は分野が複数にまたがると骨格版を「分野 版N、…」と並べるだけ。版改訂で読み替わったノード（NC5）は使わず現行骨格のノードだけ。
- 「共有している前提」（審判 §③-3）は両側のモジュールが保存されたあとの課題として残す。AI 比較の関係の種類の揺れ（§④-3）は扱わない（PR1 / PR4）。
- `peer_document_id` を渡す呼び出し元（グラフレビュー）は未配線で、API の口だけ。

### 21.3 ガードレール

`test_paper_radar_core.py::{TestWave4SupplyQuality,TestWave4MatchedKeyphrasesWordBoundary,TestWave4PlacementContrast,TestWave4ModuleMatchNote}` /
`test_paper_radar_ui_static.py::TestRadarWave4` / `test_theory_module_identity.py::TestRelatedPeerUnsaved`。

## 22. 追補 — 第 5 波: 照合の対象外を言う・近い構造の事実文・対比に観点と粒度（2026-10-16・migration / env / アンカー / LLM コール / arXiv 呼び出し / 新テーブルなし）

**状態**: 実装済み（2026-10-16）。審判 「uxsim/runs/radar_loop/judge-deep-06.md」 §①-b・§③・§④ の仮説 1 / 2 / 4（記録 `deep-06.json`
= 起点 2610.08908 / 取り込み 2610.19001、両側に理論モジュールの保存行あり）を、PR1〜PR8 / PD1〜PD8 と理論モジュール層の
TM6 / TM12 / TM13 / TM15 の範囲内で解く。仮説 3（記号・概念の水準の接点）は概念レジストリ（O-6 従属）の課題、仮説 5 は論文層の既知の限界（PL8）として扱わない。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| A 照合の対象外を言う（IK-0778） | `GET .../theory-modules/related` の `checked_module_keys` が対象外の外枠（成員が下限未満・汎用でない工程の型が 2 種未満 = `identity_eligible` でない）まで含み、事実文は「見つかっていません」の 1 行だった（照合したが相手なし と 照合の対象外 が同じ文に畳まれる）。①`checked_module_keys` は**照合した外枠だけ**、対象外は新キー `ineligible_module_keys` ②照合の対象が 1 つも無ければ `FACT_RELATED_NO_ELIGIBLE`（「この論文には、照合の対象になる構造を持つモジュールがありません（…単一の工程の型だけでできているため）」・閉世界の「見つかっていません」は言わない）③対象と対象外が混ざるときは対象外をラベルで名指しする 1 行（最大 5・超過は「ほか」・内部 ID を含むラベルは名指ししない）④グラフレビューの外枠詳細は対象外モジュールに専用の 1 文。レーダーの取り込み済みカードの `module_match_note` は、両側保存のとき（旧: 何も言わない）`structural_matches.module_match_states`（1 SQL・live ビュー・指紋は比較だけで DTO に出さない）から「起点／この論文／両方に照合の対象がない」「照合したが工程の形が同じモジュールはない」「工程の形が同じモジュールがある」「以前の規則で保存」を言い分け、一致・不一致の文には「照合は工程の形の完全一致で、記号・概念の対応は見ていません」を添える | `core/theory_modules/related.py` / `core/library/structural_matches.py::module_match_states` / `radar.module_match_note` |
| B 近い構造の事実文（IK-0779） | 指紋は工程の型の多重度と接点本数まで一致を要求するので、型の集合が近い対は何も言えなかった。`related` に additive に `near_structure: [{seed_module_key, seed_module_label, peer_module_label, peer_document_title, shared_operation_labels}]`（最大 5 対）+ `near_structure_note`。条件は**汎用でない工程の型を 2 種以上共有**（接点本数・多重度は問わない）・指紋の完全一致は `modules` 側に残す・閲覧できない相手は黙って外す・訳語の無い型だけの共有は言わない。工程の訳語は `element_vocab.operation_label`（起点モジュールの工程の順）。**同一性の候補にしない**（候補キーの計算・書き込みなし = TM13 / O-6 従属・規則 ⑤ は不変）・数値なし（TM6）・指紋なし（TM12）。グラフレビューは `seed_module_key` でモジュールに紐づけ、注記 + 「論文 — モジュール（共通する工程: …）」で列挙 | `related.build_near_structure` / `structural_matches.load_peer_outer_modules` / `admin-graph-review.js::nearStructureHtml` |
| C 対比に観点と粒度（IK-0750 追記） | `placement_contrast` の各項目に `perspective_label`（主たる観点。重なりは両側に共通の観点を優先）・`perspective_labels`（その項目に付いた観点すべて）・`layer`（`region` / `concept`）を additive に足す。観点の日本語は `core.landscape.schema.perspective_label` の正本（語彙外は空 = 生のまま出さない）。JS は「〔領域〕 問いから: 宇宙論・大規模構造 ／〔概念〕 方法から: 統計推論」のように粒度で分け観点ごとに並べる（観点の表を JS に持たない） | `radar.placement_contrast` / `radar._skeleton_labels`（3 要素目に層）/ `admin-paper-radar.js::contrastItemsText` |

`admin.html` の `?v=` は `paper-radar-20261016-wave5` / `admin-graph-review.js` は `theory-modules-related-20261016-wave5`。

### 22.1 pre-mortem

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 「照合した」の中身を照合の絞りと揃える（A） | `completion-defined-by-proxy` | 当たる（A の主型）。照合済みの集合を「外枠の全部」で代用していた。照合の判定（`identity_eligible`）と同じ述語で集合を作る |
| 片側の欠落が「見つからない」に畳まれる（A） | `upstream-loss-masked-downstream` | 当たる（副）。対象外を下流の「見つかっていません」で隠さず、対象外の側を名指しする |
| 表現できない近さを落とす（B） | `information-dropped-as-unrepresentable` | 当たる（B の主型）。完全一致の指紋 1 本では近さが表せないので、型の集合の重なりを表示専用の別の段として出す |
| 近さを候補化の根拠にする誘惑（B） | 該当なし（確定の担当の族を検査） | 同一性候補・候補キー・書き込みに触れないことをテストで固定（`test_near_structure_creates_no_identity_candidate`）。O-6 の裁定前に置き場を増やさない |
| 観点と粒度を落とす（C） | `information-dropped-as-unrepresentable` | 当たる（C の主型）。node 単位の集合差は維持し、観点と層を項目に足す |
| 観点の訳語（C） | `duplicate-canonical-sources` | 当たり得る。正本 `core.landscape.schema.perspective_label` を使い、JS とレーダーに観点の表を持たないことをガードレールで固定 |
| DTO に additive なキー（A / B / C） | `contract-changed-one-side` | 当たり得る。`checked_module_keys` の意味の縮小は唯一の読み手（グラフレビュー）を同じ波で追随させ、対象外は新キーで別に読む。他はすべて optional |

当たらない族: 再実行と削除（読み時導出・保存なし）／外部 API の予算（DB の読みのみ・arXiv / LLM 不変。related の SQL は他 document の外枠を 1 回読む分だけ増える）。

### 22.2 限界・非スコープ

- 近い構造は工程の型の重なりで、意味の近さではない（審判 §①-b の 4: 「定義して正規化して導出する」汎用手順の近さは物理的に別物でも出る）。注記で「記号・概念の対応は見ていません」と言う。記号・概念の水準の対応（仮説 3）は概念レジストリの課題。
- related の近い構造は corpus の外枠を全件読む（規模が大きくなれば上限や索引が要る）。読めなければ `near_structure` は空（fail-soft）。
- レーダーのカードには近い構造を出さない（取り込み済み候補との照合は `module_match_note` の 1 行まで）。
- 候補側の「配置候補の生成で入力の一部を処理していない」記録（審判 §③-3）は対比の留保としてまだ出さない。

### 22.3 ガードレール

`test_paper_radar_core.py::{TestWave5ModuleMatchNote,TestWave5ContrastPerspective}` / `test_paper_radar_ui_static.py::TestRadarWave5` /
`test_theory_module_identity.py::{TestWave5IneligibleSplit,TestWave5NearStructure}` / `test_theory_module_ui_static.py::TestRelatedWave5` /
`test_landscape_guardrails.py::TestPerspectiveLabelCanonReused`。

### 22.4 追補 — 第 6 波: ラベルのプレーン化の正本・近い構造の並びと題名・観点ごとの有無（2026-10-16・migration / env / アンカー / LLM / arXiv 呼び出しなし）

**状態**: 実装済み（2026-10-16）。審判 「uxsim/runs/radar_loop/judge-deep-07.md」 §③・§④ の仮説 1〜3（記録 `deep-07.json` = 起点 2610.08908 /
取り込み 2609.35757）。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| A ラベルのプレーン化（IK-0786） | 対象外の名指しで「定義: $R_δ(λ)、定義$: η」「textbfx」が出た。①`$…$` → プレーンの正本を `core/theory_modules/schema.py::plain_math` に移し（builder は import）、`\textbf` / `\textit` / `\mathsf` 等を剥がし `\langle` `\cdots` `\parallel` 等を記号にした ②related の `_display_label` は保存行の `visual_label`（保存時の旧規則の値）を信用せず、`$` 付きの `label` から**読み時に**作り直す ③教員向け投影の `latex_display.delimit_bare_tex` は和文の文字を語の区切りとし、`$` が和文の項目境界をまたがない。レーダーの `module_match_note` はラベルを持たないので変更なし | `theory_modules/schema.plain_math` / `theory_modules/related._display_label` / `latex_display._words` |
| B 近い構造の並びと相手の題名（IK-0787） | 並びを「共有する汎用でない工程の型の数 → 型の珍しさ（その型を外枠に持つ論文の数の逆数の和。並べ替えにだけ使い DTO に出さない = TM6）→ 題名 → 起点の順」の決定論ソートにした。相手の題名は `radar.display_title_for`（起点の `display_title` / `title_note` と同じ正本）で、教材名がファイル名なら arXiv メタデータの**写し**の題名（`structural_matches.load_display_titles`・API は呼ばない）、写しも無ければ教材名のまま `peer_document_title_note` に「題名は未取得」 | `related._near_pairs` / `related.build_near_structure` / `radar.display_title_for` / `structural_matches.load_display_titles` |
| C 観点ごとの有無（IK-0788） | `placement_contrast` に `perspective_facts` を足す。片側にだけ配置のある観点ごとに「この論文には方法の観点の配置がありません（起点だけに見える方法の項目は、方法が違うことを示すものではありません）」（逆向きは「起点の論文には…」）。観点の名詞は `landscape.schema.perspective_label` から「から」を外して作る（訳語表を増やさない）。数値なし | `radar.placement_contrast` / `admin-paper-radar.js::placementContrastHtml` |

`admin.html` の `?v=` は `paper-radar-20261016-wave6` / `theory-modules-related-20261016-wave6`。

#### pre-mortem

| 変更の性質 | 型 | 扱い |
|---|---|---|
| 同じラベルを作る経路が保存時と読み時に分かれ、新しい事実文の面に正規化が届かない（A） | `rule-enforced-per-surface` | 当たる（A の主型）。プレーン化を schema の 1 関数にし、related は読み時に作り直す。原理 `route-all-surfaces-through-one-point` を参照 |
| 上限で切る列挙の並びが件数だけで決まる（B） | `completion-defined-by-proxy` | 当たる（B の主型）。「共有する型の数」を近さの代理にしていた。珍しさを並びに入れ、意味の近さとは言わない（注記は不変） |
| 片側の配置の欠落が対比の差に畳まれる（C） | `upstream-loss-masked-downstream` | 当たる（C の主型）。観点ごとの有無を事実文で言う |
| DTO に additive なキー（B / C） | `contract-changed-one-side` | 当たり得る。`peer_document_title_note` / `perspective_facts` は optional・唯一の読み手（グラフレビュー / レーダーのカード）を同じ波で追随 |

当たらない族: 再実行と削除（読み時導出のみ）／外部 API の予算（題名は写しの DB 読みだけ・arXiv / LLM 呼び出しなし）。

#### 限界

- 型の珍しさは工程の型の出現分布で、主題の近さではない。主題の離れた論文との対が上位に来ることは残り得る（注記で記号・概念の対応を見ていないと言う）。
- 題名の写しは TTL（`DISCOVERY_ARXIV_METADATA_TTL_DAYS`）切れなら読まない（「題名は未取得」になる）。
- 和文を語の区切りにしたことで、プレーンのラベル（`R_δ(λ)`）が単独で `$…$` に囲まれ数式として描かれる場合がある（ASCII の添字を持つもののみ）。

#### ガードレール

`test_theory_module_identity.py::{TestWave6LabelPlainCanon,TestWave6NearStructureOrder,TestWave6PeerDisplayTitle}` /
`test_paper_radar_core.py::TestWave6PerspectiveAbsence` / `test_latex_display.py::test_delimit_does_not_cross_japanese_punctuation`。

### 22.5 追補 — AI 比較に取り込み済み候補の解析結果を足す（IK-0818）（2026-10-17・migration / env / アンカー / LLM コール / arXiv 呼び出しなし）

**状態**: 実装済み（2026-10-17・実 LLM での再演は未実施）。審判 「uxsim/runs/radar_loop/judge-deep-09.md」 ④ の仮説 1（記録 `deep-09.json` =
起点 FoG 再和論文 / 取り込み済み候補 2610.08751）。比較が要旨 2 本だけを材料にし、候補の本文の主張・中心命題にある接点（VDG∞ の
減衰関数 = FoG の模型）を `overlaps` に出せず `shared_subject` 止まりだった。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| 材料 | 候補が取り込み済み（閲覧者に見える document あり = `search.visible_ingested_documents`・起点と同じ document は除外）なら、候補の**承認済み部品ラベル**（無ければ却下以外を「承認前」と明示）と `thesis_reconstruction` の `central_thesis`（短文）を同じ 1 コールのプロンプトへ閉世界で添える。可視集合は route が注入（`services.list_visible_document_ids`）、`None` / 空なら従来どおり要旨だけ（fail-closed）。読めない材料は落とすだけ（fail-soft） | `compare.load_candidate_analyses` / `compare.candidate_analysis_from` / `routes/paper_discovery.radar_compare` |
| 照合 | `overlaps[].component_label` は起点リスト ∪ その候補のリストで照合し、載っていた側を **サーバが** `side`（`seed` / `candidate` / `both`）に残す（部品名が空なら `side` なし）。`evidence_quote` は候補要旨**または**添えた中心命題の逐語で可、出所を `evidence_source`（`abstract` / `thesis`）に。違いも同じ規則。未取り込み候補は従来と同一（中心命題の引用は drop） | `compare.validate_items` / `compare._annotate_overlap_source` |
| 表示 | 出所の事実文 `source_note`（「候補側の解析結果から（部品）」「候補側の部品は教員の承認前です」「引用は候補の解析結果（中心命題）から」）と項目の `analysis_note` はサーバ側固定文で、JS は素通し（新アンカーなし）。`caveat` は不変 | `admin-paper-radar.js::compareSourceNoteHtml` |

不変: LLM 1 回・CostGate・非保存（PR4）・仮説文体・数値なし・`caveat` の固定文。取り込み前の候補に解析結果は無いので対象外。
`admin.html` の `?v=` は `paper-radar-20261017-ik0818`。

#### pre-mortem

| 変更の性質 | 型 | 扱い |
|---|---|---|
| 解析済みの情報が比較の材料に届かない | `available-but-unwired` | 当たる（主型）。取り込み済み候補の部品・中心命題を同じ 1 コールに結線する。確かめ方は run_compare のプロンプトに材料が載ることを単体で見るのに加え、実 LLM での再演（未実施） |
| 承認前の部品を承認済みに見せる | `completion-defined-by-proxy`（確定の担当の族を検査） | 当たり得る。承認済みが無いときだけ未承認を渡し、プロンプトと `source_note` の両方で「承認前」を明示。却下は渡さない |
| 可視性の条件を比較の段へ渡し忘れる | `condition-not-propagated` | 当たり得る。可視集合を必須の注入にし、`None` / 空は SQL 非発行（原理 `enforce-condition-in-query-with-required-argument` を参照・consulted） |
| 重なりの側を LLM の申告に任せる | `duplicate-canonical-sources` | 該当なしにした。`side` はサーバが照合結果から導出し、LLM 出力スキーマに足さない |
| DTO に additive なキー | `contract-changed-one-side` | 当たり得る。`side` / `evidence_source` / `source_note` / `analysis_note` / `candidate_analysis_used` は取り込み済み候補にだけ付く optional。唯一の読み手（レーダーのカード）を同じ変更で追随 |

当たらない族: 再実行と削除（非保存）／外部 API の予算（DB の読みと artifact の読みだけ・arXiv / LLM 回数不変）。

#### 限界

- 候補側に渡すのは中心命題と部品ラベルで、支持構造・本文の主張そのものは渡さない（深い段の「支持構造の 1 文」にある接点は中心命題・部品名に現れたときだけ拾える）。
- `caveat` は「要旨の比較」の固定文のまま（PR4 の固定文を変えない）。解析結果を使ったことは `analysis_note` で別に言う。

#### ガードレール

`test_paper_radar_core.py::TestCompareIngestedCandidateAnalysis` / `test_paper_radar_api.py::TestRadarCompare::{test_visible_documents_are_injected_into_compare,test_analysis_keys_pass_through}` /
`test_paper_radar_ui_static.py::TestCompareCandidateAnalysisSource`。

## 23. 追補 — 供給を名詞句単位にする（複合語を割らない・略語はフル表記を先に）（2026-10-16・migration / env / アンカー / LLM / embedding / arXiv 呼び出しなし）

**状態**: 実装済み（2026-10-16）。

- **起きていたこと**（自律周回 `uxsim/runs/radar_loop/scan-08.json`・起点 2609.35757「Can Fingers of God be Resummed?」・距離 near）:
  供給語が `Fingers / God / FoG / SPCF / Resummed / Doppler` で、クエリは
  `(cat:astro-ph.CO) AND (all:"Fingers" OR all:"God" OR …)`。題名の複合語「Fingers of God」が機能語 `of` で単語に割れ
  （`God` 単独は無意味）、動詞由来の `Resummed`、要旨の 1 語 `Doppler`（「Doppler shifts」の修飾語）が網を広げ、
  候補 10 件中 7 件が Doppler 一致の主題外（パルサー・ニュートリノ・中性子星）だった。一方で論文の定義語
  「single-particle characteristic function」は略語 `SPCF` としてしか供給されなかった。原因は段④の句が
  「機能語で区切った連なり」で、`of` を挟む複合語と「フル表記 (略語)」の定義を知らなかったこと。
- **是正**（`radar.title_thesis_keyphrases` / `proper_noun_keyphrases` / `_thesis_text_from_artifacts`。LLM・embedding は呼ばない）:
  1. **`X of Y` の複合語を 1 句として保つ**（`radar._of_compounds`）。`of` だけを挟む 2 つの連なりをつなぎ、`of the` は
     つながない（`Kinetic Theory of the EFTofLSS` は 2 句のまま）。採るのは両側とも 1 語の連なり（分けると断片 —
     `Fingers` / `God`）か、他の材料（題名・中心命題・目的・要旨）に同じ表記が現れる場合だけ。採った句と包含関係の
     断片は従来の包含検査で落ちる（句から単語へは分解しない）。
  2. **断片の 1 語を落とす**: 動詞の過去分詞・過去形の 1 語（`Resummed`。ハイフン語は対象外）は採らない。題名に
     しか出ない 1 語は略号（大文字 2 つ以上 — `EFTofLSS`）のときだけ採る（`God` / `Alpha` は採らない）。
  3. **略語はフル表記の句を先に**: 題名・中心命題・目的・要旨から「フル表記 (略語)」「略語 (フル表記)」を抽出し
     （規則の正本は新設 `core/abbreviations.py` — 図の略語辞書 `figure_context.py` から切り出し、figure_context は
     ここを参照する。radar は initials が合う**最短**の接尾辞を採る・全角括弧は NFKC で寄せて照合・8 語まで）、
     フル表記を句の群（上限 `MAX_TITLE_THESIS_KEYPHRASES` の内側）に、略語を固有名の別枠
     （`MAX_PROPER_NOUN_KEYPHRASES`）の先頭に置く。複数形（`FoGs`）は単数形にまとめる。要旨にしか出ない句は
     従来どおり採らないが、**略語のフル表記だけは例外**（論文自身が定義した語なので新しい主題を足さない）。
  4. **固有名の 1 語の規則を締める**: 小文字の名詞を修飾する人名・地名（`Doppler shifts`）は句の一部として数えない。
     略語のフル表記に含まれる語（`Effective Field Theory …` の `Field` / `Large`）と略語の複数形は固有名にしない。
  5. **中心命題の材料に `paper_skeleton.paper_goal` を足す**（生成言語が日本語でも、目的の文に英語の定義
     「Fingers of God（FoG）」が残る）。`thesis_reconstruction` が無くても目的の文は読む。
- **供給順**（段④の内側・決定論）: 題名と中心命題（+目的）の両方の句 → 要旨にも出る句 → 中心命題の反復句 → 題名の
  残りの句（複合語を含む）→ **略語のフル表記** → 1 語の句 → 別枠（略語 → 固有名）。段①〜③（承認済み部品・確認済み
  配置・主張の概念）は従来どおり段④より前（人が確定した語を機械供給より後ろに回さない — PD3。上限 12 で
  承認済みの語が切れないため）。出所は既存語彙 `title` / `thesis` の中（新語彙なし）。
- **本文チャンクは使わない**: `documents` に要旨の列は無く、要旨は従来どおり arXiv の写し（`metadata_cache`）だけ。
  写しが無い起点は題名・中心命題・目的だけで供給する（§20 C の「チャンクで代替しない」を維持）。

### 23.1 実測（実 DB・SELECT と関数呼び出しのみ・arXiv は呼んでいない）

| 起点 | before | after |
|---|---|---|
| c795b2cf 2609.35757「Can Fingers of God be Resummed?」 | `Fingers` / `God` / `FoG` / `SPCF` / `Resummed` / `Doppler` | `Fingers of God`（title）/ `single-particle characteristic function`（thesis）/ `FoG` / `SPCF` / `EFT` |
| a8d731be 2610.08908「Full-Shape Analysis of the Redshift-Space Galaxy Trispectrum in the EFTofLSS」 | `Full-Shape` / `Galaxy Trispectrum` / `EFTofLSS` / `FoG` / `AbacusSummit` | `Full-Shape` / `Galaxy Trispectrum` / `effective field theory of large-scale structure` / `Fingers-of-God` / `EFTofLSS` / `FoG` / `AbacusSummit` |
| 842bd9cb 2610.19001「Kinetic Theory of the EFTofLSS: A Halo-based Approach」 | `Kinetic Theory` / `Halo-based` / `EFTofLSS` / `Field` / `Large` | `Kinetic Theory` / `Halo-based` / `Effective Field Theory of Large Scale Structure` / `EFTofLSS` / `EFT` |

3 起点とも中心の問い・中心命題・目的は日本語で、英語の材料は題名・要旨（写し）・目的の中の用語の定義だけ。
依頼時に挙がった主題語「redshift-space distortions」「velocity dispersion」は 2609.35757 の要旨・題名・目的に
その表記で現れないため供給されない（訳語・言い換えを捏造しない）。「galaxy clustering」「effective field theory」は
要旨にしか無い一般句で、`OR` で足すと Doppler と同じく網を広げるので採らない。

### 23.2 pre-mortem

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 句の切り方（機能語で割る）の誤りが下流で「近い論文」の主題外として現れていた | `upstream-loss-masked-downstream` | 当たる（主型）。帯分け・比較を読み替えず、供給（上流）の句の単位を直した。実データ `scan-08.json` の形をテストに写した |
| 略語抽出の規則を radar にも書く | `duplicate-canonical-sources` | 当たる。`figure_context.py` の実装を `core/abbreviations.py` に切り出して両者が参照（figure_context は旧 private 名を互換エイリアスで残す）。radar の差分（最短接尾辞・全角括弧）は引数で表す |
| 汎用の 1 語（`Doppler`）が `OR` の網を広げる | `scope-widened-silently` | 当たる。1 語の供給を締めた（修飾語の固有名・過去分詞・題名にしか無い大文字 1 語を落とす）。教員のクリックで外す経路（PD3）は不変 |
| 検索語としての契約（句であること）を供給側が確かめていなかった | `contract-changed-one-side` | 当たる。照合（`search.matched_keyphrases`）は不変で、供給する側で句の単位を守る |
| 確定の担当 | 該当なし（承認済み部品・確認済み配置を機械供給より後ろに回さない。候補の自動除外はしない） |

当たらない族: 再実行と削除（読み時導出のみ）／外部 API の予算（arXiv 呼び出し回数は不変・写しを読むだけ）。

### 23.3 限界・非スコープ

- 品詞解析はしない。`of` 以外の前置詞（`in` / `for`）を挟む複合語、`-ed` で終わる 5 字以上の名詞（`hundred`）は過去分詞とみなして落ちる。
- 日本語の中心命題・目的からは英語の定義だけを拾う（日本語の句を検索語に訳さない）。
- 要旨の一般句（galaxy clustering 等）を供給するかは、網の広さの実測が要る（本追補では採らない）。

### 23.4 ガードレール

`test_paper_radar_core.py::TestPhraseUnitSupply`（scan-08 の形: 複合語・断片の除外・略語の順・全角括弧・修飾語の固有名・
paper_goal）/ `TestSeedKeyphraseSupply::test_title_keyphrases_rules`（題名にしか無い大文字 1 語の扱いを更新）/
`test_figure_context.py`（切り出し後も図の略語辞書は不変）。

### 23.5 追補 — 短い略語はフル表記の句に置き換える・「この語だけに一致した候補が多い」語を言う（2026-10-16・migration / env / アンカー / LLM / embedding / arXiv 呼び出しなし）

**状態**: 実装済み（2026-10-16）。審判 「uxsim/runs/radar_loop/judge-scan-09.md」 §5 の仮説 1・2（記録 `scan-09.json`）。

- **起きていたこと**: §23 の是正後、起点 c795b2cf（2609.35757）の供給語は
  `Fingers of God / single-particle characteristic function / FoG / SPCF / EFT`。未取り込み候補 7 件がすべて `EFT` だけで
  一致し、うち 5 件が主題外（アクシオン 2・非弾性暗黒物質・高階微分重力・インフレーション）。`EFT` は中心命題・目的の
  日本語の定義「有効場理論（EFT）」から来ており（英語の「フル表記 (略語)」が材料に無いので §23 の略語の辞書に載らず、
  中心命題の反復語・固有名として供給された）、要旨には「effective field theory approach」とだけある。一方、本文
  （chunks）には「effective field theory (EFT) predictions」がある。網を広げたのに `widens` は全語 false
  （条件が「取得した候補の**すべて**に当たった」だけで、取り込み済みのトライスペクトルが `EFT` を含まなかったため）。
- **是正 A（`radar.title_thesis_keyphrases` の `_try` / `radar.body_full_form`）**: 短い略語（大文字だけの 2〜3 字 —
  `radar.is_short_abbreviation`）を採ろうとした位置で、まずフル表記の句を試し、フル表記（またはそれを含む句）が
  供給されていれば略語は**落とす**。フル表記は材料（題名・中心命題・目的・要旨）の「フル表記 (略語)」→ 無ければ
  注入関数 `body_full_form`（`seed_keyphrase_candidates` が渡す。`chunks` を `(略語` を含むものだけ先頭から最大
  `BODY_ABBREVIATION_MAX_CHUNKS` = 5 件 SELECT し、正本 `core/abbreviations.extract_abbreviations(shortest=True)`）の順。
  見つからない・採れない・本文が読めないときは略語を残す（情報を落とさない）。`FoG` / `SPCF` / `EFTofLSS` は短い略語の
  形に当たらないので従来どおり残す — **一般語の表は作らず**、区別は「フル表記の句が別に供給されているか」だけ。
  出所は略語と同じ `thesis`（題名にあれば `title`。新語彙なし）。
- **是正 B（`radar.keyphrase_hits` / `keyphrase_hit_notes`）**: 従来の下限（候補 3 件以上・有効な語 2 語以上）のもとで、
  「**この語だけに**一致した候補」が取得した候補の過半を占める語も `widens: true` とし、その語の項目に限って
  `widens_note`（`NOTE_KEYPHRASE_SOLE_WIDENING`「この語だけに一致した候補が多く、網を広げています」）を足す。
  `keyphrase_widening_note` はこの語群を `NOTE_KEYPHRASES_SOLE_WIDENING_HEAD`（+「外すかどうかは教員が選びます」）で
  列挙する（件数・割合なし = PD4・自動では外さない = PD3）。「すべてに当たった」語は従来の文言・キー集合のまま。
  seed DTO には載せない（候補が出る前の段なので判定材料が無い）。UI（`admin-paper-radar.js`）は `widens` の有効な
  チップに「当たらず」と同じ添え方で印「網を広げる」を付け、title にサーバの `widens_note` を素通しする（日本語の
  事実文は JS に焼き込まない・新アンカーなし・`admin.html` の `?v=` を wave7 に上げた）。

| 起点 | before（§23 後） | after |
|---|---|---|
| c795b2cf 2609.35757 | `Fingers of God` / `single-particle characteristic function` / `FoG` / `SPCF` / `EFT` | `Fingers of God` / `single-particle characteristic function` / `FoG` / `SPCF` / `effective field theory`（本文のフル表記） |
| a8d731be 2610.08908 | `Full-Shape` / `Galaxy Trispectrum` / `effective field theory of large-scale structure` / `Fingers-of-God` / `EFTofLSS` / `FoG` / `AbacusSummit` | 不変（短い略語なし） |
| 842bd9cb 2610.19001 | `Kinetic Theory` / `Halo-based` / `Effective Field Theory of Large Scale Structure` / `EFTofLSS` / `EFT` | `Kinetic Theory` / `Halo-based` / `Effective Field Theory of Large Scale Structure` / `EFTofLSS`（`EFT` はフル表記を含む句が供給済みで落ちた） |

実 DB（SELECT と関数呼び出しのみ・arXiv は呼んでいない）。`scan-09.json` の候補 9 件に新しい `keyphrase_hits` を掛けると
`EFT` が `widens: true` + `widens_note` になり、`keyphrase_widening_note` が「この語だけに一致した候補が多く、網を広げています
（外すかどうかは教員が選びます）: EFT」になる（他の語は不変）。

**pre-mortem**

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 多義の略語の網の広がりが下流（主題外の候補）で現れていた | `upstream-loss-masked-downstream` | 当たる（主型）。帯分け・比較ではなく供給（上流）で略語をフル表記に置き換えた |
| 略語の規則を radar に再び書く | `duplicate-canonical-sources` | 当たる。本文の抽出も `core/abbreviations` を使い、radar には「短い略語」の形の判定だけを置く |
| 網を広げる語の判定が代理（「すべてに当たった」）だけだった | `completion-defined-by-proxy` | 当たる。単独一致が過半という形を足した（従来の判定は残す） |
| 本文を読む新しい経路 | `condition-not-propagated` | 当たらない。読むのは起点の document の chunks だけで、起点の可視性は `resolve_seed` の既存ゲートの内側 |

**限界**: フル表記の句（`effective field theory`）も分野をまたぐ一般句で、網を `EFT` 単独より狭めるかは次の走査で実測する
（`all:"..."` の句検索になる点で略語単独より絞れる見込み）。本文の「フル表記 (略語)」は最初に見つかったものだけを採る。
ガードレール: `test_paper_radar_core.py::TestShortAbbreviationFullForm` / `TestSoleWidening`、
`test_paper_radar_ui_static.py::TestSoleWideningChipMark`。

## 24. 追補 — 第 7 波: 網の偏りの事実文・帯の母集団・取り込み済み候補の比較・起点の系譜（2026-10-22・migration / env / アンカー / LLM コール / arXiv 呼び出し / 新テーブルなし）

**状態**: 実装済み（2026-10-22）。自律周回の未解決 4 件（IK-0760 / IK-0706 / IK-0759 / IK-0761）を、PR1〜PR8 / PD1〜PD8 の範囲内で解く。

| 項目 | 何を変えたか | 正本 |
|---|---|---|
| A 網の偏り（IK-0760） | `keyphrase_hits` に 3 つ目の印を足す。既存の下限（候補 3 件以上・有効な語 2 語以上）のもとで、「すべてに当たった」でも「この語だけに一致が過半」でもないが、取得した候補の **2/3 以上**に当たる語の項目に `dominant: true` と `dominant_note`（`NOTE_KEYPHRASE_DOMINANT_TEMPLATE`「「X」が取得した候補の多くに当たっています。網が一般語に寄っている可能性があります。」）を付け、検索結果に `keyphrase_dominant_note` を足す。閾値はコード定数 `DOMINANT_HIT_SHARE_NUMERATOR` / `DENOMINATOR`（env にしない）。**語の一般性を判定する表は持たない**（分野語を書かない）— 言うのは網の上の事実だけで、外すのは教員（PD3）。件数・割合は書かない（PD4）。arXiv の呼び出しは増やさない（網の取り方 = 案②は §14 の予算に触れるので採らない） | `radar.keyphrase_hits` / `keyphrase_hit_notes` |
| B 帯の母集団（IK-0706） | 帯ラベルの付いた候補があるときは**常に** `band_population_note`（`NOTE_BAND_POPULATION`「帯は arXiv から取得した新着の候補の中での比較です。取得していない論文はこの帯に入りません。」）を返す。§20 D の `reading_note`（関係ラベルと同時に出るときだけ）とは別の 1 行で、凡例の直後に 1 回だけ描く（帯分けできなかったときは出さない）。数値（取得件数・閾値）は出さない（PR2 / PD4） | `radar.band_population_note`、`admin-paper-radar.js::bandPopulationNoteHtml` |
| C 取り込み済み候補の比較（IK-0759） | 取り込み済みの候補カードにも比較用のチェックボックス（`pr-select-compare-only`）を出し、「違いを分析」の対象に選べるようにする。取り込みの対象には入らない（`selectedIngestIds` が status `new` だけに絞る — 取り込み済みを再取得しない IK-0796 と二重に守る）。比較は既存の 1 コール・日次上限・非保存（PR4）のまま。要旨は `run_compare` の写しの read-through（`metadata_cache.read_fresh` → 足りない分だけ arXiv）を通るので、検索で取得した候補なら追加の arXiv 呼び出しはゼロ。比較結果がまだ無い取り込み済みカードには導線 1 行（`INGESTED_COMPARE_HINT`）を配置の対比の直後に添える | `admin-paper-radar.js::candidateCardHtml` / `selectedIngestIds` |
| D 起点の系譜（IK-0761） | 取り込みの監査（`AUDIT_ENTITY_PAPER_DISCOVERY` の `ingest` / `ingest_batch`）の metadata に `seed_document_id` を足す（レーダーの取り込みが `seed_document_id` を送り、サーバが view を確かめて `documents.id` に正規化したときだけ記録 — 申告を信用しない・閲覧できない値は記録しない・取り込みは止めない）。`GET /radar/seed` は `radar.seed_lineage` で「この document の `source_url` の arXiv ID を取り込んだ監査 → その起点 → …」を遡り、`seed.lineage`（`[{document_id, title}]`・古い → 新しい・最後が起点）/ `lineage_line` / `lineage_note` を返す。段数の上限は `LINEAGE_MAX_DEPTH`（コード定数）、循環は止める、閲覧できない論文は `LINEAGE_HIDDEN_TITLE` にして題名も id も出さずそこで止める（可視集合は route からの**必須の注入** — 空なら SQL を発行しない）。失敗は fail-soft（キーを付けない）。新テーブル・保存なし（PR1）。画面は seed ヘッダに 1 行（`#pr-seed-lineage`）+ 事実文で描き、系譜があるときは §21 D の JS 内の「前の起点」行に優先する | `radar.seed_lineage`、`routes/paper_discovery.py::_audit_seed_document_id` / `get_radar_seed` |

- A の画面: `dominant` の語のチップにも §23.5 の印「網を広げる」を付け、title に事実文と操作の案内「クリックで外せます（外したあと「検索」で探し直します）」を添える。外すのは既存のチップのクリック（`enabled` の反転）で、自動では外さず、教員が「検索」を押すまで arXiv を呼ばない（PD3・§14）。新しい操作要素は足していない。
- 対照検索（2026-10-22・オーケストレータが作業ツリーを反映して arXiv を 1 回・`uxsim/runs/radar_loop/contrast-2026-10-22-kurtosis.json`）: 尖度の起点で `band_population_note` が帯付き候補とともに返り、`large-scale structure` には既存の単独一致の印と事実文が立って `dominant` は重ならなかった。他の教員が取り込んだ候補は `status: new` のまま（閲覧者に見えない取り込みを「取り込み済み」と言わない fail-closed で正しい）。
- 事実文はすべてサーバ（`radar.py`）の定数で、JS は素通しする（ガードレール）。比較用チェックボックスの title と導線 1 行は操作要素の装飾・案内として JS に持つ。事実表示・既存のチェックボックスと同じ操作なので新アンカーは足さない。`admin.html` の `?v=` を `paper-radar-20261022-wave24` に上げた。
- 系譜は**この変更以降の取り込みからしか辿れない**（それ以前の監査には起点が無い）。そのため `lineage_note` は「系譜は、論文レーダーから取り込んだときに記録した起点だけからたどっています。」を常に添える。

### 24.1 pre-mortem

| 変更の性質 | 型 | 本書での扱い |
|---|---|---|
| 網の範囲を新着順の先頭で決め、一般語の偏りが下流（帯・比較）でしか現れない | `upstream-loss-masked-downstream` | 当たる（A の主型）。網の取り方は変えず（予算）、偏りを網の上の事実として言う |
| 段階ラベルの母集団を新着順の先頭で決め、全体の位置のように読まれる | `extent-decided-by-proxy-signal` | 当たる（B）。母集団の性質を帯が出るたびに 1 行で言う。取得件数は増やさない |
| 比較の判定は取り込み済みにも呼べるのに、画面の選択肢が新着だけだった | `available-but-unwired` | 当たる（C）。選択肢を広げ、取り込みの選択とは分けた |
| 起点の文脈が JS の一時状態にしかなく、連鎖の系譜が 1 段で途切れる | `condition-not-propagated` | 当たる（D の主型）。取り込みの監査に起点を載せ、読み時に遡る |
| 系譜の読み出しが閲覧できない論文の題名を出す | `condition-not-propagated` | 当たる。可視集合を必須引数で受け、空なら SQL を発行しない（原理 `enforce-condition-in-query-with-required-argument`） |

### 24.2 限界・非スコープ

- 網そのもの（新着順の先頭 N 件）は変えていない。主題語の論文・古い関連論文が網の外に落ちる現象は残り、事実文で「偏っている可能性」を言うだけ（取得の仕方を変えるのは arXiv の予算 §14 の判断と一緒に）。
- 系譜は取り込みの監査に起点を記録した取り込みからだけ辿れる。手動アップロード・分野購読モーダルからの取り込み・この変更以前の取り込みは系譜に出ない。同じ arXiv ID を複数の起点から取り込んだ記録があれば、閲覧できる起点のうち最新の記録を採る。
- 比較結果は従来どおり保存しない（取り込み済みカードの関係の種類は、比較を押したその画面の間だけ読める）。
- ブラウザでの目視・実 arXiv での再演はしていない。

### 24.3 ガードレール

`test_paper_radar_core.py::TestDominantKeyphrase` / `TestBandPopulationNote` / `TestCompareIngestedUsesCache` / `TestSeedLineage`、
`test_paper_radar_api.py::TestWave24SearchNotesPassThrough` / `TestSeedLineageRoute` / `TestIngestRecordsTheSeed`、
`test_paper_radar_ui_static.py::TestRadarWave24`。

## 25. 追補 — 供給語の材料の正規化（TeX の断片・スラッシュ・動詞の頭）（2026-10-22・migration / env / アンカー / LLM / arXiv 呼び出しなし）

**きっかけ**: オーナーがブラウザで 2610.08751（事前体積効果・Mpc 単位）を起点に開いた画面で、キーフレーズに `rm Mpc` / `CDM` /
`adopting Mpc` / `h units` が並び、`CDM` だけに当たる候補が網を占めていた（IK-0845）。中心命題は日本語の文に `${\rm Mpc}/h$`
`$\Lambda$CDM` の TeX 数式区画を含み、題名は `by adopting Mpc over Mpc/h units`。

**是正**（すべて `core/paper_discovery/radar.py` の供給の入口 `title_thesis_keyphrases`）:

- `strip_tex_for_phrases`: `$…$` の数式区画と、それに**くっついた**英字の断片（`$\Lambda$CDM` の `CDM`・`${\rm Mpc}` の `rm`）、
  制御綴り、波括弧を句の材料から外す。記号は句ではなく、くっついた断片は語の半分。落とすのは検索語の材料だけで、表示・保存の本文は変えない。
- 語の正規表現で `/` を語の内側に認め、区切り文字から `/` を外す（`Mpc/h units` は 1 句。`h units` にならない）。
- 連なりの頭の一般動詞の動名詞（`_GENERIC_GERUNDS`。`adopting` / `mitigating` / `probing` / `revisiting` / `exploring` /
  `investigating` / `quantifying` / `characterizing` / `assessing` / `evaluating` / `examining` を追加）は句に含めず、連なりを次の語から
  始める。分野の名詞の -ing（`clustering` / `lensing`）は表に入れない。

**実物での結果**: 題名 + 中心命題から `prior-volume effects` / `galaxy clustering` / `Mpc/h units` / `PVE` だけが残る。

**ガードレール**: `test_paper_radar_core.py::TestKeyphraseSupplyTexAndVerbs`。

**未確認**: 是正後の供給で検索し直した網（arXiv 呼び出しの予算で止めた）。
