# 論文ディスカバリー層（arXiv 分野購読とコーパス成長ループ）

> **状態: 実装済み（正本・凍結）**（2026-08-27 起票・同日 Phase 1 実装、同日 Phase 2 実装。
> migration は **071** `paper_discovery` / **072** `paper_discovery_ingest_queue` で採番済み。
> Phase 3 は未着手、§7 のコーパス回遊は提案（実装対象外）— 着手時は専用設計書を切る。
> 以後は §10 実装記録のみ追記する）

**正本**: 本ドキュメント。
**関連**: [URL指定による教材取得](url_material_upload_design.md)（UF1〜UF6 — 本層の
取得経路はこれを呼ぶだけ）/ [知識ランドスケープ](knowledge_landscape_design.md)（取り込み後の
配置と §12 コーパス地図構想）/ [カテゴリギャップ候補](category_gap_candidates_design.md)
（地図の縁の信号源）/ [「論文と話す」discuss モード](discussion_mode_design.md)（Phase 3 =
document 直付け入口の予約）/ [知識ネットワークビジョン](knowledge_network_vision.md)
（KN-1〜4）/ [広がり装置](personal_map_curiosity_design.md)（好奇心の文法 — 存在だけを
事実として見せる）/ [LLM トークン使用量推計](llm_usage_metering_design.md)（取り込みコストの
事前見積り）。

---

## 1. 目的 — 分野グラフを論文から育てる成長ループを閉じる

本システムは既に「1論文 → 構造化（A層）→ 地図への配置（ランドスケープ）→ 置けなかった
主題からの骨格成長（カテゴリギャップ候補）→ 論文間の同一性リンク（W層/L層）」という、
**論文を入れるほど分野のグラフが育つ**流路を持っている。しかし入口が手動アップロードと
URL 1件指定しかないため、コーパスの成長速度は教員が論文を「探して持ってくる」手作業に
律速されている。

本層は arXiv を論文供給源として、**分野を単位とした発見（ディスカバリー）**を追加する。
教員は分野ごとの購読条件（arXiv カテゴリ + キーフレーズ）を持ち、候補論文の一覧から
選んだものだけを既存の取得・解析経路へ流す。狙いは次のループを閉じることである。

```
分野購読（カテゴリ + キーフレーズ）
   ↓ 教員がモーダルを開いたとき検索（メタデータのみ・LLM 0回）
候補論文リスト
   ↓ 教員が選択・承認（人間の弁）
既存 URL 取得（url_material_upload / UF1〜UF6）→ 既存解析パイプライン
   ↓ 自動
theory_component_graphs（論文単位）+ ランドスケープ配置 + カテゴリギャップ候補
   ↓ 既存の教員レビュー
骨格の成長 + confirmed 同一性リンク
   ↓
検索語彙が増える（骨格概念・confirmed 部品 → 購読キーフレーズの供給源）
   ↺ 次の発見精度が上がる
```

最後の矢印が本層の要点である。キーフレーズを人が保守するのではなく、**システムが既に
持っている分野語彙（atlas 骨格の概念ラベル・カートリッジ ontology・confirmed な理論部品の
語彙）から供給する**ことで、「グラフを育てる仕組み」と「論文を見つける仕組み」が同じ
ループの中で互いを強化する。

「あらかじめ分野のグラフをある程度作っておき、学習者の興味関心に応える」という運用は、
このループを教員が空き時間に回すことの結果として実現される。学習者が来たときには
confirmed リンクと骨格が育っている — 事前構築の実体は自動化ではなく、**発見コストの
最小化と承認作業のバッチ化**である。

---

## 2. 不変条項（PD1〜PD8）

| ID | 条項 | 意味 |
|---|---|---|
| **PD1** | **発見は自動、取り込みは教員の明示承認のみ** | 候補のリストアップまでが機械の仕事。解析パイプラインに入るのは教員が選択・確認した論文だけ。全自動クロール取り込みの経路を作らない（LLM コストの弁であると同時に、レビューされない inferred 成果物の無限堆積を防ぐ。candidate-only 原則の取り込み版）。 |
| **PD2** | **取得・解析は既存経路へ完全合流** | 承認された論文の取得は `core/url_fetch.py`（UF1〜UF6）を呼ぶだけ。許可ドメイン照合・SSRF ガード・形式判定・`_accept_material_source` への合流をそのまま継承し、ディスカバリー専用の取得経路・専用の教材種別を作らない。A層は非改変。 |
| **PD3** | **検索語彙は分野語彙から供給し、出所を明示する** | キーフレーズ候補は骨格概念・カートリッジ ontology（aliases 含む）・confirmed 理論部品から自動供給し、各フレーズに供給元を表示する。教員は自由に外せる・足せる。**AI・サーバが購読条件を教員の操作なしに書き換えることはない**（購読は教員の意思の正本）。 |
| **PD4** | **数値スコアを見せない（教員含む）** | 類似度・一致度の生数値は UI・API レスポンスに出さない。提示は並び順と段階ラベルまで（LS/W8 と同じ規律）。 |
| **PD5** | **候補は保存せず読み時導出** | サーバに保存するのは購読条件・見送り記録・取り込み対応（出所 URL）だけ。候補一覧は毎回 arXiv API から導出し、「取り込み済み」「見送り済み」の判定も既存行との突合で導出する（G1 と同じ思想 — 完了フラグ・候補スナップショットのテーブルを持たない）。見送りは行削除せず状態遷移で保持し、復帰できる（P4）。 |
| **PD6** | **閉世界の正直さ** | 候補一覧は「この購読条件で arXiv を検索した結果」であって分野の全体ではない。UI は検索条件を常に併記し、「この分野の論文は他にない」と読める表示をしない（SL1 の同族）。API 失敗時は事実文で degrade し、空一覧を「該当なし」と偽らない。 |
| **PD7** | **外部 API の行儀** | arXiv API の呼び出しは `export.arxiv.org` への HTTPS に固定し、リクエスト間隔 3 秒以上のスロットルを client 層で構造的に守る。ページング上限・タイムアウトを持ち、失敗はリトライ回数を限って諦める。 |
| **PD8** | **押し付けない** | 候補があってもバッジ・督促・自動表示をしない。教員が「探す」を開いたときに新しいものが上に並んでいるだけ。ポーリング禁止。G層ルールも v1 では追加しない（運用実測後に判断 — カテゴリギャップ候補 §4.6 と同じ裁定）。 |

---

## 3. 全体像 — 4層のじょうごと段階分け

候補の絞り込みは4層のじょうごで行う。各層の役割を混ぜない。

```
第1層【網 / 再現率】   arXiv カテゴリ購読（cat:astro-ph.CO 等）
第2層【絞り / 精度】   キーフレーズ照合（供給源は分野語彙 — PD3）
第3層【並べ替え】      embedding 類似度ランキング（Phase 3。任意・後付け）
第4層【人間の弁】      教員が候補カードを見て承認（PD1）
```

補助シグナルとして**著者フォロー**（既知グループの系譜を確実に追う）を購読条件の
オプションに置くが、土台にはしない。著者集合は分野より小さく、土台にすると既知の系譜
ばかりが濃くなる地図になる（「地図は正解でなく投影」の原則と衝突する）。

段階分け:

| Phase | 内容 | 状態 |
|---|---|---|
| **Phase 1** | 分野購読 + arXiv 検索 + 候補一覧 UI + 承認取り込み（§4） | 実装済み（2026-08-27・§10） |
| **Phase 2** | バッチ取り込みの非同期化 + トークン使用量の事前見積り提示（§5） | 実装済み（2026-08-27・§10） |
| **Phase 3** | embedding 類似度ランキング + 引用グラフ拡張口（§6） | 設計済み・着手待ち |
| **Phase 4 / v2** | コーパス回遊 — コース無し議論・コーパス地図・地図の端（§7） | 提案（実装対象外・着手時に専用設計書） |

---

## 4. Phase 1 実装設計

### 4.1 DB（migration 071。シードしない）

```sql
-- 分野購読（分野単位の教員共同財。L層ライブラリと同じ立場）
CREATE TABLE IF NOT EXISTS paper_discovery_subscriptions (
    domain_key        TEXT PRIMARY KEY,          -- atlas ドメイン / cartridge_id 名前空間
    arxiv_categories  TEXT[] NOT NULL DEFAULT '{}',
    keyphrases        JSONB  NOT NULL DEFAULT '[]',
    -- keyphrases 要素: {"text": "...", "source": "skeleton"|"cartridge"|"component"|"manual",
    --                   "enabled": true}  — 供給元の明示（PD3）。外した状態も保持する（P4）
    followed_authors  JSONB  NOT NULL DEFAULT '[]',
    updated_by        UUID,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at   TIMESTAMPTZ
);

-- 見送り記録（行削除せず revoked 遷移で復帰 — P4/PD5）
CREATE TABLE IF NOT EXISTS paper_discovery_dismissals (
    domain_key    TEXT NOT NULL,
    arxiv_id      TEXT NOT NULL,                 -- version 抜きの正規化 ID（例: 2608.20293）
    dismissed_by  UUID,
    dismissed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked       BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (domain_key, arxiv_id)
);

-- 取り込み出所（重複判定の正本）
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_url TEXT;
```

- `documents.source_url` は URL 経由の取り込み（手動の「URLから取得」とディスカバリー経由の
  両方）が保存する。**「取り込み済み」判定はこの列から正規化した arXiv ID との突合で読み時
  導出する**（PD5）。既存行は NULL のままで良く、UI は「URL 経由で取り込まれた論文のみ
  判定できます」の事実を注記する（手動アップロードされた同一論文は判定できない — 偽装
  しない、PD6）。arXiv ID の正規化（`abs`/`pdf` URL・version サフィックスの吸収）の正本は
  `core/paper_discovery/schema.py` に置く。**version 違い（v1/v2）は同一論文とみなす**。
- 購読は分野単位1行の共同編集とする（`updated_by` で最終編集者を記録）。同時編集の楽観
  ロックは v1 では持たない（購読条件は小さく、last-write-wins の実害が軽微なため。実運用で
  衝突が観測されたら `revision_store` に接続する）。
- migration は行を1つも INSERT しない（UF2 と同じ理由 — 毎起動再実行方式では初期シードが
  削除を殺す）。

### 4.2 core（`backend/core/paper_discovery/`、FastAPI 非 import・LLM 非 import）

| ファイル | 責務 |
|---|---|
| `schema.py` | dataclass / 語彙の正本。`normalize_arxiv_id()`（URL・version 吸収）、キーフレーズ供給元語彙（`skeleton` / `cartridge` / `component` / `manual`） |
| `arxiv_client.py` | arXiv API 呼び出しの唯一の入口。`export.arxiv.org` への HTTPS 固定・**モジュールレベルの 3 秒スロットル**（前回呼び出し時刻を保持し不足分 sleep）・タイムアウト・`xml.etree` による Atom パース。返すのは正規化済み `ArxivEntry`（id / title / authors / summary / categories / published / updated / pdf_url） |
| `vocab.py` | キーフレーズ候補の供給。①`atlas_store.load_learner_skeleton()` の概念ラベル ②カートリッジ `ontology.json` の aliases / notation_patterns ③当該分野の document 群から confirmed / teacher_approved な `theory_components` のラベル。各候補に `source` を付けて返す（PD3） |
| `search.py` | 購読条件 → `search_query` 文字列の組み立て（カテゴリ OR 結合 + フレーズ AND/OR）、結果への注釈付け（取り込み済み / 見送り済みの読み時導出）、新着判定（`last_checked_at` 比較） |
| `store.py` | 購読・見送りの読み書き。`DELETE FROM` を書かない（見送り復帰は `revoked` 遷移） |

- LLM を一切呼ばない（発見層は Phase 1〜2 を通じて LLM 0回。embedding は Phase 3 で
  `core.llm` 経由・U層計測下に入れる）。
- arXiv API 自体は「サーバから外部 URL への到達」だが、宛先が定数 `export.arxiv.org` に
  固定されているため `url_fetch_domains` の照合対象にはしない（許可リストは**教員が自由な
  URL を渡せる経路**を閉じるための仕組み。宛先固定の API クライアントは性質が異なる）。
  ただし PDF 本体の取得は必ず url_fetch 経由なので、`arxiv.org` が許可リストに無ければ
  **候補は見えても取り込めない** — UI はこの状態を事実文で示す（§4.4）。

### 4.3 API（`backend/api/routes/paper_discovery.py`、main.py 直接登録、実パス `/api/admin/discovery/...`、全て `_require_teacher`）

| エンドポイント | 内容 |
|---|---|
| `GET /subscriptions` | 購読一覧（分野ごとの条件 + `last_checked_at`） |
| `PUT /subscriptions/{domain_key}` | 購読の作成・更新（カテゴリ・キーフレーズ・著者）。監査記帳 |
| `GET /subscriptions/{domain_key}/keyphrase-candidates` | `vocab.py` の供給結果（出所付き）。購読編集 UI の初期チップ |
| `POST /search` | body: `domain_key`（+任意の条件上書き）。arXiv API を検索し、注釈付き候補リストを返す。副作用は `last_checked_at` の更新のみ。**候補を保存しない**（PD5） |
| `POST /ingest` | body: `items: [{arxiv_id}]` + `analyze_images?` / `models?`。各 item の PDF URL を組み立てて `url_fetch.fetch_source_from_url`（許可リスト照合込み）→ `_accept_material_source` へ。レスポンスは既存 upload と同形（202 相当の受理結果の配列）。`documents.source_url` を保存。監査記帳 |
| `POST /dismiss` / `POST /restore` | 見送り / 復帰（`revoked` 遷移）。監査記帳 |

- `POST /ingest` は v1 では**リクエスト内同期・少数件**（上限は控えめな定数、超過は 422 の
  事実文で Phase 2 のバッチを待つ）。URL 取得の例外 → HTTP status 写像は
  `upload_material_from_url` の `_URL_FETCH_ERROR_STATUS` を再利用する。
- エラーは日本語の事実文。内部情報（解決 IP 等）を `detail` に載せない（UF6 継承）。

### 4.4 UI（教材管理タブ。`frontend/public/js/admin-paper-discovery.js`、ES5・`window.PaperDiscovery`・admin.js から DI 注入）

入口は教材管理タブの「URLから取得」ボタンの隣に「**arXivから探す**」。新しいタブは
作らない。`url_fetch_domains` に `arxiv.org` が未登録の場合、検索はできるが取り込みボタンを
無効化し、事実文「取得先ドメインが許可されていません。システム管理者が「AIモデル」タブで
設定できます」を表示する（fail-closed の表示は補助で、強制はサーバ側 — UF1 継承）。

モーダルは3区画:

1. **検索・購読パネル（上段）** — 分野セレクタ（atlas ドメイン / cartridge）、カテゴリ
   チップ、キーフレーズチップ（自動供給分は供給元をツールチップで明示 — PD3。外す/足すは
   自由）、著者フォロー（任意）。[この条件で検索] と [この条件を保存]。
2. **候補一覧（本体）** — カード: タイトル / 著者 / 日付 / カテゴリ / 一致した概念名
   （なぜ候補か、を1行で — ブラックボックスのおすすめにしない）/ 要旨（折りたたみ）。
   チェックボックスで複数選択。「取り込み済み」はラベル付き非活性、「見送り済み」は
   フィルタで表示 + [戻す]。並び順は新着順（数値スコアなし — PD4）。検索条件を一覧の
   上に常時表示する（PD6）。
3. **取り込み確認** — 選択件数と事実文（「N件の論文を取得し、解析パイプラインを実行します。
   解析には LLM を使用します。解析結果は候補として保存され、公開するまで学習者には表示
   されません」）→ 実行。受理後は既存の `handleUploadAccepted` に合流し、進捗は教材管理
   一覧の既存表示に任せる（新しいポーリングを作らない）。

**管理UI 3点セット**（development_checklist §1 / admin インスペクト・モードの規約）:
マニュアル節（`docs/manual/teacher/` の教材管理リファレンスに追記。無効化され得る取り込み
ボタンは「無効になっている場合: 理由 + 解消方法」の節を必ず持つ）+ `ADMIN_UI_ANCHORS`
登録 + `data-ui-anchor` 付与。アンカーの想定は `materials.arxiv-discovery`（入口ボタン）/
`materials.arxiv-discovery-modal` / `materials.arxiv-discovery-search` /
`materials.arxiv-discovery-ingest` / `materials.arxiv-discovery-subscribe` の5件（2026-08-27
時点の設計値。正確な件数は実装時の `test_admin_help_ui_anchors.py` が正）。

**Admin Copilot capability**: `materials.arxiv_discovery`（`kind=guidance_only`・
`required_role=TEACHER`・locate_steps で入口ボタンを点灯）を1件登録する。取り込み実行の
代行（action capability）は登録しない — LLM コストを伴う不可逆寄りの操作を Copilot 代行に
載せない判断。

**G層 next_steps**: v1 ではルールを追加しない（PD8。「候補が溜まっています」は督促に
なる。運用実測後にカテゴリギャップ §4.6 と同じ枠組みで再判断）。

### 4.5 監査

`backend/core/schema.py` の監査カタログに `AUDIT_ENTITY_PAPER_DISCOVERY =
"paper_discovery"` を追加し、購読の作成・更新 / 取り込み実行（対象 arXiv ID 列挙）/
見送り / 復帰を `theory_review_events` に記帳する（`services.record_review_event` 経由）。

### 4.6 コスト

- 検索・候補一覧・見送り: **LLM 0回・embedding 0回**。外部コストは arXiv API のみ
  （無料・3秒間隔遵守）。
- 取り込み: 既存解析パイプラインのコストそのもの（1論文 = LLM 呼び出しステージ群。
  既存の `*_MAX_CALLS_PER_DAY` 各上限と U層計測がそのまま効く）。専用の上限は v1 では
  設けず、`POST /ingest` の件数上限だけを持つ。

### 4.7 ガードレール（`backend/tests/`）

`test_paper_discovery_{core,api,guardrails,ui_static}.py`。guardrails の検査項目:

- `core/paper_discovery/` が FastAPI / `core.llm` を import しない
- `store.py` に `DELETE FROM` が無い（見送りは `revoked` 遷移）
- 数値スコア・類似度の生値がレスポンス DTO に現れない（PD4）
- `arxiv_client.py` にスロットル実装が存在し、宛先が `export.arxiv.org` 定数である（PD7）
- `POST /ingest` が `url_fetch.fetch_source_from_url`（許可リスト必須引数）を経由する
  — 独自の HTTP 取得を書いていない（PD2）
- 全自動取り込みの経路（ユーザー操作を経ない ingest 呼び出し・cron/worker からの ingest）が
  存在しない（PD1）
- migration が INSERT を含まない（UF2 継承）
- UI 静的検査: 検索条件の常時表示（PD6）・取り込み確認の事実文・許可ドメイン未設定時の
  案内文言

---

## 5. Phase 2 — バッチ取り込みと事前見積り

Phase 1 の `POST /ingest` は同期・少数件。まとまった数を流せるようにする。

- **非同期バッチ worker**: 既存の `threading.Thread` 方式（tension / V層スイーパと同型）。
  取り込みキュー行を持つ小さなテーブルを追加し（実装時採番）、1件ずつ url_fetch →
  `_accept_material_source` を実行。進捗は教材一覧の既存 status（projector 正本）で見える
  ため、専用の進捗 UI・ポーリングは作らない。
- **事前見積り**: 取り込み確認画面に U層の見積り（`GET /api/admin/llm-usage/estimate/...` と
  同じレンジ表示の流儀。金額なし・レンジのみ）を出す。「N件 × 1論文あたりの目安レンジ」の
  事実文。
- 失敗した item は `status='failed'` で保持し、リトライは教員の明示操作のみ（P4 / PD1）。

---

## 6. Phase 3 — 並べ替えの強化と引用グラフ拡張口

- **embedding 類似度ランキング**: 候補アブストラクトを既存 embedding モデルでベクトル化し、
  分野の既存教材（当該 domain の取り込み済み document のアブストラクト/チャンク重心）との
  cosine で並べ替える。表示は並び順 + 段階ラベル（「関連: 高/中」）のみ（PD4）。
  embedding 呼び出しは `core.llm` 経由で U層計測（feature `discovery:ranking`）に入れる。
- **引用グラフ拡張口**: 「同じ分野」の最強シグナルは引用ネットワーク（被引用 = 理論の
  発展を扱う後継論文）。Semantic Scholar / OpenAlex 等の無料 API を第2の候補供給源として
  接続できるよう、`arxiv_client.py` と同じ形の client インターフェース（宛先固定・
  スロットル・正規化 DTO）を切っておく。v1 では実装しない。外部 API の追加は
  SYSTEM_ADMIN の明示設定（env またはドメイン許可と同じ思想の設定行）でオプトインとする。

---

## 7. Phase 4 / v2 構想 — コーパス回遊と地図の端（提案・実装対象外）

> 本節は方向性の合意を記録するもの。着手時は専用設計書を切る（discuss Phase 3 の規約に
> 従う）。学習者向け表示を含むため、実装前に各層の不変条項（P1/P3/P4/数値非表示）との
> 突合を専用設計書側で行うこと。

大量取り込み後の学習者体験として、次の3機能を構想する。

1. **コース無しで論文と議論する** — discuss モードの document 直付け入口
   （[discussion_mode_design.md](discussion_mode_design.md) が Phase 3 として予約済み。
   観測基盤の実測が着手判断の材料）。アクセスゲートは document 可視性（public / group /
   private、fail-closed 実装済み）に置き換わる。大量取り込みした論文を public / group で
   開示することが前提条件。
2. **論文を渡り歩く** — 2つの道: ①ランドスケープ配置による地図上の移動
   （[knowledge_landscape_design.md](knowledge_landscape_design.md) §12 のコーパス別地図・
   EmergentRegion 構想）②confirmed 同一性リンクを継ぎ目にした構造経由の移動（journey /
   共通部品の糸の既存 traversal）。議論しながら隣の論文へ跳ぶ体験はこの2つの合成。
3. **地図の端** — 端の先には3つの同心円があり、それぞれ既存データから読み時導出できる:

```
[内側] 配置済み・閲覧可能な論文       → 渡り歩き・議論の対象
[縁]   取り込み済みだが地図に置けなかった論文
       → landscape_gap_signals の学習者向け投影（region 単位でクラスタ済み）。
         「この領域の先に、まだ地図にない主題を扱う論文があります」の事実文
[外]   まだ取り込まれていない arXiv 候補
       → 本層の検索結果の存在表示。「この概念の先を扱う論文が arXiv にあります
         （この検索条件では）」— 存在と名前だけ。取り込みは教員の弁のまま
```

[外] の輪により、学習者の好奇心が（k-匿名集約を経て — P3）教員への需要信号になり、
教員の承認で地図が育ち、縁が先へ動く。ディスカバリー層が「教員の空き時間の作業」から
「学習者の関心に駆動される仕組み」へ変わる。

規律: 縁と外の表示は霧・晴れ間の同族として、事実文と名前だけ・数値なし・煽りなし
（踏破率禁止の既存原則）。閉世界語彙（「この検索条件では」「このコーパスの中では」）を
落とさない。学習者個人の到達履歴を教員に見せない（k=3 は `core/privacy.py` 正本）。

---

## 8. 非スコープ（v1）

- 全自動取り込み・スケジュール実行（PD1。発見の worker 化は Phase 2 のバッチ取り込みでも
  行わない — 検索はモーダルを開いたときだけ）
- G層 next_steps ルール・通知・バッジ（PD8。運用実測後に再判断）
- 学習者向け表示のすべて（§7 は専用設計書マター）
- 引用グラフ API 接続・embedding ランキング（Phase 3）
- arXiv 以外の供給源（bioRxiv / HAL 等。client インターフェースの一般化で拡張可能な形には
  しておくが実装しない）
- 購読の個人化（購読は分野単位の共同財。教員個人ごとの購読は需要が観測されてから）
- 手動アップロード済み論文との重複判定（`source_url` が無い既存行は判定不能 — 事実として
  表示するのみ）

---

## 9. 実装時の確認事項

- migration 採番: `ls backend/db/` で空き番号を確認（本書は番号を予約しない — §5-4）。
  → 2026-08-27 解消（`backend/db/071_paper_discovery.sql` で採番）。
- `documents.source_url` の追加が既存の document 削除・purge 経路（V層 `purge_object` /
  `delete_material`）に影響しないこと（列追加のみなので原則影響なしだが、DTO への露出は
  教員向けのみとし、学習者向け DTO に出さない）。
- arXiv カテゴリ語彙（`astro-ph.CO` 等）の妥当性検査をどこまでやるか — v1 は自由入力 +
  検索結果 0 件の事実文で足りる想定（カテゴリ表のハードコード保守を避ける）。
- `POST /search` のページング上限と1回の表示件数（arXiv API は 1 リクエスト最大約 2,000 件
  だが、UI 表示は数十件 + 「さらに読み込む」で十分）。
- 教材管理タブは SYSTEM_ADMIN から不達の既知事情（AL層実装記録参照）— 入口の配置が
  SYSTEM_ADMIN でも到達可能かを実装時に確認。

---

## 10. 実装記録（Phase 1, 2026-08-27）

Fable 5 指揮・Opus 5 並列サブエージェント4体（core+migration / フロントエンド /
API ルーター / 管理UI 3点セット）で実装。backend フルスイート green・コミットは未実施。

### 実装ファイル

- **migration**: `backend/db/071_paper_discovery.sql`（§4.1 のとおり。INSERT ゼロ・完全冪等・
  actor 列に FK なし = AL1 と同じ理由）
- **core**: `backend/core/paper_discovery/`（schema / arxiv_client / vocab / store / search /
  __init__。FastAPI・LLM 非 import）
- **API**: `backend/api/routes/paper_discovery.py`（main.py 直接登録・7ルート・全て
  `_require_teacher`）。`backend/core/schema.py` に `AUDIT_ENTITY_PAPER_DISCOVERY` 追加。
  `routes/admin.py` の `_accept_material_source` に `source_url` キーワード引数を追加し
  documents INSERT に列を追加（`upload_material_from_url` が `body.url` を渡す。multipart は
  None のまま）
- **UI**: `frontend/public/js/admin-paper-discovery.js`（ES5・`window.PaperDiscovery`）+
  `admin.html`（script + 入口ボタン `#paper-discovery-link`）+ `admin.js`（DI init・
  click ハンドラ・Copilot 論理アンカー `paper_discovery_button`）
- **3点セット**: `docs/manual/teacher/11-admin-materials.md` に5節（`{#arxiv-discovery}` 系）/
  `core/help_kb/admin_ui_anchors.py` に5アンカー（総数 283→288、正は
  `test_admin_help_ui_anchors.py`）/ `core/admin_assistant/capabilities.py` に
  `materials.arxiv_discovery`（guidance_only）+ `docs/admin_operations/materials.md` の
  `{#arxiv-discovery}` 節
- **テスト**: `test_paper_discovery_{core,guardrails,api,ui_static}.py`
- **docs 追随**: `docs/architecture/layer_registry.md` §3（071 行 + 空き番号 072）/
  `docs/architecture/data-model.md` / `docs/backend/api.md`（直接登録ルーター一覧 + §3 節）/
  `CLAUDE.md`（論文ディスカバリー層 節 + 監査語彙 38 へ更新）/ `docs/README.md` 索引

### 設計からの主な確定事項・逸脱

1. **ingest は 202**（既存 upload と同形の受理）。部分失敗は `failed[]`（arxiv_id + detail）で
   返し、`NoDomainsConfiguredError` のみ全体 422（1件も成功し得ないため）。per-item の
   取得エラーは url_fetch の事実文を素通し、search の arXiv 失敗は固定事実文
   「arXiv に接続できませんでした。…」（`ArxivApiError` の文言はホスト名等を含むため
   素通ししない — UF6 継承）。
2. **`MAX_INGEST_PER_REQUEST = 5`**（v1 同期取得の上限。超過 422
   「一度に取り込めるのは5件までです。件数を減らして実行してください。」）。
3. **`run_search` は条件ゼロなら arXiv を呼ばない**（`query=""` / 空 candidates）。フロントは
   検索実行フラグで「未検索」と「条件未指定」を区別表示（PD6 の両側担保）。
4. **既存購読がある分野への新規キーフレーズ供給候補は enabled=false で追加**し事実文で告知
   （PD3 — サーバ/AI が教員の操作なしに検索条件を広げない）。購読未保存の分野では
   enabled=true で初期供給。
5. **`sortBy` から relevance を外し新着順のみ**（v1。PD4 の並び順規律）。
6. **keyphrases は文字列配列・オブジェクト配列の両方を受理**（PUT はオブジェクト、search の
   条件上書きは enabled テキストの文字列配列 — フロント実装との合意）。
7. **監査語彙**: subscribe（`""|subscribed→subscribed`）/ ingest（`candidate→ingest_requested`、
   metadata に arxiv_ids・failed 件数）/ dismiss（`candidate→dismissed`）/
   restore（`dismissed→candidate`）。
8. **Copilot の locate_steps 用に admin.js の `registerUiAnchors("materials")` へ論理アンカー
   `paper_discovery_button` を登録**（`data-ui-anchor` の5件とは別系統）。取り込み実行の
   action capability は登録しない（§4.4 の判断のとおり）。
9. 承認済み部品語彙（vocab ③）の承認 status は R層と同じ
   `("teacher_approved","teacher_reviewed","endorsed")` を vocab.py にローカル定義
   （R層への import 依存を作らない。コメントで相互参照）。

### Phase 2 実装記録（2026-08-27）

同日、同体制（Fable 5 指揮・Opus 5 並列3体 = backend / frontend / 3点セット）で実装。
backend フルスイート 11,188 pass。

- **migration 072** `paper_discovery_ingest_queue.sql`: `paper_discovery_ingest_items`
  （status CHECK = queued/fetching/accepted/failed・INSERT シードなし・FK なし・部分
  インデックス2本）
- **キュー store は core・worker ループは api 層**という配置を確定（指揮判断）:
  `core/paper_discovery/ingest_queue.py` は FastAPI / threading / HTTP 非 import の規約を
  維持し（Phase 1 ガードレール非改変のまま）、取得（url_fetch）と受理
  （`_accept_material_source`）を呼ぶ worker 本体は `backend/api/ingest_worker.py`
  （threading.Thread daemon・V層スイーパと同じ lifespan 起動）に置いた。worker は
  `arxiv_client` を import しない（発見をしない = PD1 の構造化）
- `claim_next` は `FOR UPDATE SKIP LOCKED` のアトミック遷移（多重起動安全）。取得のたびに
  許可リストを読み直す（許可の追加・削除が即反映）。アイテム間 3 秒 sleep（PD7 の同族）。
  worker 起動時に `requeue_stale_fetching` で置き去り fetching を回収。env:
  `PAPER_DISCOVERY_WORKER_ENABLED`（既定 on）/ `PAPER_DISCOVERY_WORKER_INTERVAL_SECONDS`
  （既定 30）
- **API 追加4本**: `POST /ingest-batch`（上限 `MAX_INGEST_BATCH=50`・202・queued/skipped/
  notice。models はここで fail-closed 検証）/ `GET /ingest-queue` / `POST
  /ingest-queue/{id}/retry`（failed のみ・監査 `ingest_retry`）/ `GET /ingest-estimate`
- **事前見積り**: `core/llm_usage/metrics.py::recent_document_run_estimate` —
  `llm_usage_events` の `pipeline:%` を document 単位に合算した直近実績（各バケット最大20
  document）の中央値 ±25%（`ESTIMATE_SPREAD`）。reported / estimated を**合算しない**（U1）、
  レンジのみ・金額キーなし（U5）、実績ゼロは `available:false` の正直文
- **フロント**: 選択5件以下 = 従来の同期 `/ingest`、6件以上 = `/ingest-batch`（境界述語
  `usesBatchIngest` の1箇所）。キュー投入時に候補行を `ingested` に偽装しない（PD6）。
  取り込みキュー欄（既定で閉じた details・手動[更新]のみ・ポーリングなし = PD8・失敗行に
  detail + [再試行]）。見積り行は「1論文あたりの目安レンジ × 選択件数」の並置表示で
  クライアント側の掛け算をしない
- **3点セット追随**: アンカー2件追加（`materials.arxiv-discovery-queue{,-refresh}`、総数
  288→290）+ teacher マニュアル2節 + `{#arxiv-discovery-ingest}` 節の2経路化追記 +
  admin_operations 手順更新
- **設計からの逸脱**: 「失敗 item は status='failed' で保持・リトライは教員の明示操作のみ」は
  `retry_item`（failed 限定・それ以外 422）で構造化。ingest-batch は許可ドメイン未設定でも
  **受理**し notice で正直に告げる（worker が取得時点の許可リストで判定 — 後から許可が
  入れば流れる）
