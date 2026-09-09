# コーパスを補う論文（Corpus Complement — 読んだ論文に対して「何を足すか」で候補を選ぶ）

> **状態: 実装済み（正本）**（2026-09-09 起票・同日実装。migration **077**
> `paper_discovery_reference_cache` — 外部事実のキャッシュ1表のみ。実装記録は §11）

**正本**: 本ドキュメント。
**関連**: [論文ディスカバリー層](paper_discovery_design.md)（PD1〜PD8 — 本層はその
第3の探し方で、検索・注釈・取り込みの全機構を再利用する）/
[論文レーダー](paper_radar_design.md)（PR1〜PR8・§12 の「新しい面」— 本層はその
コーパス全体版）/ [分野マップのベクトル係留](atlas_vector_anchoring_design.md)
（VA1〜VA9 — アンカーベクトルと着地予測）/ [知識ランドスケープ](knowledge_landscape_design.md)
（LS3/LS5 — 配置の意味論と数値非表示）/ [賭け金の台帳](stakes_ledger_design.md)
（SL1 — 閉世界語彙）/ [コーパス回遊層](corpus_roaming_design.md)（CR7/CR10 —
学習者起点で外部 API を呼ばない・関心信号による自動化の恒久禁止）/
[制度指標カタログ](indicator_governance_design.md)（IG2 — 学習者指標を推薦に使わない）。

---

## 1. 目的 — 「近い論文」ではなく「読むと知見が足される論文」

既存のディスカバリー層は2つの問いに答えている。

| 既存機構 | 答える問い |
|---|---|
| 分野購読（`POST /search`） | この分野の新着は何か（並べ替えは分野重心との関連度） |
| 論文レーダー（`/radar/*`） | この1本の論文の周辺には何があるか（距離帯） |

どちらも**近さ**を軸にしている。本層が答える問いは別で、

> パイプラインで処理した論文たちを読んだ**うえで**、追加で読むとこの領域の知見が
> さらに得られる論文はどれか。

「知見が足される」を、コーパスがすでに持つ**構造**に対する**補完**として定義する。
補完の根拠は3つあり、それぞれ既存の構造から**決定論的**に導く（LLM 0回）。

| レンズ | 補完の意味 | 導出元（すべて既存） |
|---|---|---|
| **A 地図の薄い領域** | 分野の地図（凍結骨格）のうち、コーパスの論文がほとんど配置されていない概念に着地する候補 | `landscape_placements`（live 配置）× VA層アンカーベクトル |
| **B 検証記録の無い前提** | D層・SL層の台帳で「このコーパスの中では検証記録がありません」となっている前提・主張に近い内容を扱う候補 | `epistemic_ledger`（`untested` × スコープ空欄）× 候補要旨の embedding |
| **C 基盤論文** | 取り込み済みの複数の論文が共通に引用しているのに、まだコーパスに無い論文 | Semantic Scholar の参照リスト（`citation_client` 経由・オプトイン） |

「良質」は外部の被引用数ではなく、**このコーパスの論文たちが依拠している**（C）・
**このコーパスの穴を埋める**（A / B）という、コーパス相対の構造で定義する。分野全体で
有名かどうかは本層の関心ではない（分野全体の判断は教員の対話に残す — SL7 同族）。

---

## 2. 不変条項（CC1〜CC8）

PD1〜PD8 をすべて継承したうえで、本層固有の条項を足す。

| ID | 条項 | 意味 |
|---|---|---|
| **CC1** | **補完の根拠はコーパス構造のみ・学習者信号を混ぜない** | レンズの入力は配置・台帳・引用関係の3つだけ。`frontier_interest` / stumble / tension などの学習者痕跡を選定入力にしない（CR10 の恒久禁止・IG2 の非利用「推薦」を構造的に守る）。教員が関心集約を横に見ることは既存 UI のまま可。 |
| **CC2** | **決定論・非LLM** | 3レンズとも LLM を呼ばない。embedding は既存の関連度バッチ（`ranking.py` の1コール）に**相乗り**し、発見層の `core.llm` 接触 allowlist（`ranking.py` / `compare.py`）を増やさない。 |
| **CC3** | **候補は読み時導出・保存は外部事実のキャッシュだけ** | 候補・レンズ判定は保存しない（PD5 継承）。唯一保存するのは**参照リストという外部事実**のキャッシュ（migration 077）で、教員の判断・候補のスナップショットではない。これは PD5「候補を保存しない」の**設計明示例外**である（外部 API の行儀 PD7 のため。§6.3）。 |
| **CC4** | **数値非表示** | cosine・引用元の本数・配置件数・被引用数を DTO に載せない。レンズ判定は「該当あり」の**有無**と、根拠となる**名前の列挙**（ノード名・前提文・引用元タイトル）で示す（PD4 / LS5 / SL4 継承）。 |
| **CC5** | **閉世界語彙の固定** | レンズB の事実文は SL1 の固定文「このコーパスの中では検証記録がありません」のみ。「この分野では未検証」「誰も検証していない」「世界初」「未踏」を書かない（SL1 denylist をガードレールで継承）。レンズA は骨格版を明示し「地図（版N）の中で」の言明に留める（VA8）。 |
| **CC6** | **仮説文体・推定であることを剥がさない** | レンズの判定は「〜に近い内容を扱っている可能性があります」「〜に着地しそうです」の推定であり、UI は〈推定〉の出所を常時表示する。候補を「良い論文」と断定する語（おすすめ・必読・重要）を使わない。 |
| **CC7** | **取り込みは既存の弁のみ・教員の明示操作のみ** | 本層は候補提示まで。取り込みは既存 `/ingest` / `/ingest-batch`（PD1/PD2/PR3 継承）。ボタン押下時だけ実行し、worker / cron / 起動時から本層を呼ばない。バッジ・G層ルール・ポーリングなし（PD8）。 |
| **CC8** | **教員専用・fail-soft** | 全 API は TEACHER 以上。レンズが1つでも成立しなければ**そのレンズだけ** `available:false` + 事実文で縮退し、検索そのものは必ず成立させる（PD6 — 空一覧を「該当なし」と偽らない）。学習者向け表示・API を作らない（CR7）。 |

---

## 3. 全体像

```
                      ┌ レンズA: 薄い概念ノード（配置 ≤ 上限）  ← landscape_placements × 骨格
候補（arXiv 検索）──▶ │ レンズB: 検証記録の無い前提文          ← epistemic_ledger（untested・空スコープ）
  + 関連度バッチ      └ 判定は候補ベクトル × {アンカー | 前提文} の cosine（同一バッチ・追加コール0）
                                  ↓
                      候補ごとに complement: {fills[], skies[]} を付与（該当なしはキー自体なし）
                      補完あり候補を先頭に（関連度順を保つ安定ソート）

取り込み済み論文 ──▶ レンズC: 参照リスト（S2）→ 2本以上が共通に引用 ∧ 未取り込み ∧ arXiv ID あり
  （キャッシュ read-through・1操作で最大 N 本だけ新規取得）
                                  ↓
                      候補（cited_by: 引用元タイトル列挙）→ 既存 ingest へ
```

レンズA/B は**同じ画面・同じ検索**（分野購読の検索条件）に注釈として重なる。レンズC は
候補集合そのものが違う（arXiv 検索ではなく引用関係から来る）ため、引用グラフ供給
（`/citation-search`）と同じく**別の一覧モード**として提示する。

---

## 4. UI（教材管理タブ「arXivから探す」モーダル）

新しいモーダル・新しいタブは作らない。既存モーダル（`admin-paper-discovery.js`）に
ボタン2つと注釈行を足す。

### 4.1 入口

| 要素 | anchor | 位置 | 動作 |
|---|---|---|---|
| ボタン「コーパスを補う候補を探す」 | `materials.arxiv-discovery-complement` | 「この条件で検索」の隣 | `POST /complement/search`。検索条件は通常検索と同じ（購読条件 or 編集中の条件） |
| ボタン「基盤論文を探す」 | `materials.arxiv-discovery-foundation` | 「引用グラフから探す」の隣 | `POST /complement/foundation`。`citation_source_enabled:false` のときは無効 + 既存の事実文（強制はサーバ） |

### 4.2 一覧の上（PD6 の条件行に追記）

`state.mode` に `"complement"` / `"foundation"` を追加する。条件行（`#pd-query-note`）は
モードごとに出所を書く:

- complement: 「検索条件: … ／ 並び順: 補完の根拠がある候補を先に（関連度順） ／
  地図の版: N ／ closed_world_note」。レンズの縮退は `#pd-complement-note` に
  サーバの事実文をそのまま出す（レンズA・B それぞれ独立）。
- foundation: 「候補の出所: 取り込み済み論文の参照リスト ／ 参照を読んだ論文: タイトル列挙 ／
  closed_world_note」。`pending_seeds:true` なら「まだ参照リストを読んでいない取り込み済み
  論文があります。もう一度押すと続きを読みます。」を出す。

### 4.3 候補カードの「補完」ブロック（`candidate.complement` があるときだけ）

固定の見出し語（フロント定数）で最大3行:

| 行 | 見出し | 内容 |
|---|---|---|
| A | `地図の薄い領域に着地: ` | `fills[].region_label / node_label` を「・」区切り（最大2）+ 「（骨格 版N）」 |
| B | `検証記録の無い前提に近い: ` | `skies[].statement` を『』で囲み、出典タイトルを括弧で添える（最大2）。行末に固定文「このコーパスの中では検証記録がありません」 |
| C | `引用元: ` | foundation モードのみ。`cited_by[].title` を「・」区切り（既存 `CITATION_DERIVED_HEAD` を再利用してよい） |

ブロックの先頭に〈推定〉タグ。数値・件数・バッジは描かない（CC4）。判定はサーバの
キーの有無をそのまま描く（クライアント側で閾値判定・並べ替えをしない）。

### 4.4 取り込み

チェックボックス → 既存 `/ingest`（≤5）/ `/ingest-batch`（6件以上）の経路を**そのまま**使う
（コードの分岐は `state.mode` に依らない）。

---

## 5. バックエンド設計

### 5.1 core — `backend/core/paper_discovery/complement.py`（FastAPI 非 import・LLM 非 import）

```python
#: レンズA: 1候補に付ける「薄い領域」の上限件数。
MAX_FILLS_PER_CANDIDATE = 2
#: レンズB: 1候補に付ける前提文の上限件数。
MAX_SKIES_PER_CANDIDATE = 2
#: レンズB: 埋め込みに載せる前提文の上限（同一バッチに相乗りするための防波堤）。
MAX_SKY_STATEMENTS = 30
#: レンズB: 前提文1件の最大文字数（切り詰め）。
MAX_SKY_STATEMENT_CHARS = 300

#: SL1 の固定文（正本は core/doubt/schema.py 側の定数を import して再利用する。無ければ
#: ここに1本だけ置き、ガードレールで denylist 語彙の不在を固定する）。
CLOSED_WORLD_SKY_NOTE = "このコーパスの中では検証記録がありません"

NOTE_NO_SKELETON = "この分野には凍結された分野の地図がないため、「地図の薄い領域」は判定しません。"
NOTE_NO_ANCHORS = "分野の地図のベクトル索引が未構築のため、「地図の薄い領域」は判定しません。"
NOTE_NO_THIN_NODES = "分野の地図の概念はいずれも取り込み済み論文で覆われているため、「地図の薄い領域」の候補はありません。"
NOTE_NO_SKIES = "このコーパスの台帳には、検証記録の無い前提の記帳がありません。"
NOTE_EMBEDDING_UNAVAILABLE = "候補の埋め込みが作れなかったため、補完の判定を行いませんでした。"

def thin_node_ids(session, domain_key: str, anchors, *, max_documents: int) -> set[str]:
    """凍結骨格の concept ノードのうち、生きた配置（status NOT IN superseded/rejected）の
    distinct document_id 数が max_documents 以下のもの。region ノードは対象外。
    件数は外へ出さない（集合のみ）。DB 不達は空集合（レンズごと縮退）。"""

def sky_statements(session, domain_key: str, *, limit: int = MAX_SKY_STATEMENTS) -> list[dict]:
    """分野の document（corpus.domain_document_ids）に属する epistemic_ledger 行のうち
    verification_status='untested' かつ verification_scopes が空配列のものを、
    target_type ∈ {assumption, claim} に限って本文へ解決する:
      assumption → assumption_nodes.statement（status ∈ {confirmed, operationalized} のみ）
      claim      → theory_claims.text（review_status ∈ reconstruction.schema.APPROVED_REVIEW_STATUSES のみ）
    返り値: [{"target_id", "target_type", "statement", "document_id", "document_title"}]
    （人間が確定した前提・承認済み主張だけを使う = AI 候補を根拠にしない）。"""

def fills_for_vector(vector, anchors, thin_ids: set[str], *, limit=MAX_FILLS_PER_CANDIDATE) -> list[dict]:
    """純関数。ANCHOR_LANDING_THRESHOLD_NEAR 以上で thin_ids に含まれる concept アンカーを
    近い順に最大 limit 件、{"node_label", "region_label"} で返す（node_id・生値は出さない）。
    atlas_vectors.query.nearest_anchors を使う。"""

def skies_for_vector(vector, sky_vectors, statements, *, limit=MAX_SKIES_PER_CANDIDATE) -> list[dict]:
    """純関数。COMPLEMENT_SKY_THRESHOLD 以上の前提文を近い順に最大 limit 件、
    {"statement", "document_title", "closed_world_note": CLOSED_WORLD_SKY_NOTE} で返す。
    未測定は不一致扱い。"""

def build_complement_context(session, domain_key: str, anchor_context: dict | None, *, thin_max_documents: int) -> dict:
    """ranking.rank_candidates(complement_context=...) に渡す材料を組む。
    返り値: {"thin_node_ids": set, "sky_statements": [...], "facts": {"coverage": {available, note?}, "skies": {available, note?}}}
    anchor_context が None → coverage.available=False + NOTE_NO_SKELETON/NOTE_NO_ANCHORS。
    thin が空 → available=True + NOTE_NO_THIN_NODES（該当なしは正常な状態 — 発見）。"""

def order_complement_first(candidates: list[dict]) -> list[dict]:
    """complement キーを持つ候補を先頭へ（元の順序を保つ安定ソート）。"""
```

### 5.2 `ranking.py` への追加 — 同一バッチへの相乗り（embedding の接触点は既存どおりここだけ）

`rank_candidates(session, domain_key, candidates, *, daily_limit=None, anchor_context=None,
complement_context=None)` に optional 引数を1つ足す。

- `complement_context` があるとき、`texts = 候補テキスト + 前提文（sky_statements[].statement）`
  を**1バッチ**で埋め込む（コールは従来どおり1回）。日次ゲートも従来どおり1消費。
- 埋め込み後、前提文ベクトルを分離し、各候補に対して
  `complement.fills_for_vector` / `complement.skies_for_vector` を呼び、いずれかが非空なら
  `payload["complement"] = {"fills": [...], "skies": [...]}`（空の側はキーを付けない）。
  両方空なら `complement` キー自体を付けない（VA4 の流儀）。
- 返り値に `"complement_facts": {"coverage": {...}, "skies": {...}}` を足す（既存キー不変）。
  埋め込み不能（`available:False`）のときは両レンズを `NOTE_EMBEDDING_UNAVAILABLE` で縮退。
- 既存呼び出し（`complement_context=None`）の挙動は完全不変。
- レンズB の閾値は `core.label_vocab.COMPLEMENT_SKY_THRESHOLD`（追加済み）。ranking.py に
  数値を直書きしない。禁止キー（`"score"` `"similarity"` `"confidence"` `"relevance"`
  `"rank"` `"match_score"`）を core ツリーのどこにも書かない（既存ガードレール）。

### 5.3 レンズC — `foundation.py` + `reference_cache.py` + `citation_client.references_for_arxiv`

**`citation_client.py` への追加**（宛先定数・3秒スロットル・タイムアウト・`CitationApiError` を
そのまま共有）:

```python
REFERENCE_FIELDS = "title,abstract,year,authors,externalIds"
MAX_REFERENCE_LIMIT = 200

def references_for_arxiv(arxiv_id: str, *, limit: int = 100, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> list[CitationEntry]:
    """GET https://api.semanticscholar.org/graph/v1/paper/arXiv:{id}/references?fields=...&limit=...
    応答 {"data": [{"citedPaper": {...}}]} を CitationEntry（arXiv ID を持つものだけ・
    seed_arxiv_id = 引用している側）へ。形が違えば空リストへ縮退（推薦 API と同じ規律）。"""
```

**`reference_cache.py`**（store.py には触れない — 既存ガードレール
`test_store_writes_only_subscriptions_and_dismissals` の対象を広げない）:

```python
def get_fresh(session, arxiv_ids: list[str], *, ttl_days: int) -> dict[str, list[dict]]:
    """fetched_at が now() - ttl_days 以内、fetch_status='ok' の行を {arxiv_id: references}。"""
def upsert(session, arxiv_id: str, references: list[dict], *, fetch_status: str) -> None:
    """INSERT ... ON CONFLICT (arxiv_id) DO UPDATE（DELETE を書かない）。references は
    CitationEntry.to_dict() の列。失敗は fetch_status='failed' + references=[] で記録し、
    TTL 内の再取得を抑える（外部 API の行儀 — PD7）。"""
```

**`foundation.py`**（FastAPI 非 import・LLM 非 import・`url_fetch` / `_accept_material_source`
非 import）:

```python
CLOSED_WORLD_NOTE = "この一覧は取り込み済み論文の参照リストから導出した範囲のみを示します。"
NOTE_DISABLED = citation_search.NOTE_DISABLED を再利用
NOTE_NO_SEEDS = "この分野には、参照リストを読む起点になる取り込み済みの arXiv 論文がまだありません。"
NOTE_PENDING_SEEDS = "まだ参照リストを読んでいない取り込み済み論文があります。もう一度押すと続きを読みます。"
NOTE_NO_CANDIDATES = "取り込み済みの複数の論文が共通に引用している未取り込みの論文は、読めた範囲では見つかりませんでした。"

def run_foundation_search(session, domain_key, *, min_citing_seeds, fetch_per_call, ttl_days) -> dict:
    """1. citation_search.citation_source_enabled() が偽なら _disabled_result 同型を返す（外へ出ない）。
    2. seeds = corpus.domain_ingested_papers(session, key)（上限なし・新しい順）。0件 → available:False + NOTE_NO_SEEDS。
    3. cache = reference_cache.get_fresh(...)。未キャッシュのシードを新しい順に最大 fetch_per_call 本だけ
       citation_client.references_for_arxiv で取得し upsert（失敗は 'failed' で記録・他シードは続ける）。
    4. 参照を arXiv ID で集約。引用している seed 数 >= min_citing_seeds かつ seed 自身でないもの。
       status（new/ingested/dismissed）は search.ingested_arxiv_ids / store.dismissed_ids で注釈。
       ingested は候補から**外さず** status で示す（PD6 — 既存一覧と同じ）。
    5. 並び順: 引用している seed 数の降順 → year 降順 → arxiv_id（数は DTO に出さない — 順序だけ）。
    返り値: {"enabled", "available", "domain_key", "candidates": [... + "cited_by": [{"arxiv_id","title"}], "status"],
             "seeds_read": [{"arxiv_id","title"}], "pending_seeds": bool, "closed_world_note", "note"?, "partial"?}
    全シードが未キャッシュかつ全取得失敗 → CitationApiError を投げる（空一覧を「該当なし」と偽らない）。"""
```

### 5.4 migration 077 — `backend/db/077_paper_discovery_reference_cache.sql`

```sql
CREATE TABLE IF NOT EXISTS paper_discovery_reference_cache (
    arxiv_id      TEXT PRIMARY KEY,                 -- 引用している側（取り込み済みシード）の正規化 arXiv ID
    reference_entries JSONB NOT NULL DEFAULT '[]'::jsonb, -- CitationEntry.to_dict() の列（arXiv ID を持つ参照のみ。`references` は予約語）
    fetch_status  TEXT NOT NULL DEFAULT 'ok' CHECK (fetch_status IN ('ok', 'failed')),
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **シード行を入れない**・FK なし・users 参照なし（account_lifecycle の PURGE/RETAIN 宣言不要）。
- 冪等（`IF NOT EXISTS` のみ）。DELETE 文なし（更新は upsert）。
- これは**外部 API が公開しているメタデータの写し**であり、教員の判断でも候補の
  スナップショットでもない（CC3 の設計明示例外。`documents.source_url` と同じ「事実の記帳」側）。

### 5.5 API（`backend/api/routes/paper_discovery.py` へ追加。全て `_require_teacher`）

| Method / Path | 入力 | 出力 |
|---|---|---|
| `POST /api/admin/discovery/complement/search` | `SearchRequest` と同じ（`order` は無視し常に relevance） | `run_search` の DTO + `order:"relevance"` + `ranking:{available,note?}` + `complement:{available, skeleton_version?, lenses:{coverage:{available,note?}, skies:{available,note?}}}` + candidates（`relevance_label` / `landing`? / `complement`? 付き・補完あり先頭） |
| `POST /api/admin/discovery/complement/foundation` | `{domain_key}` | §5.3 の返り値そのまま。`CitationApiError` は 502 + `_DETAIL_CITATION_UNAVAILABLE` |

- `complement/search` の流れ: `pd_search.run_search`（副作用は `last_checked_at` のみ・
  `last_search_found_new` の更新も既存どおり）→ `commit` → `anchor_context = _anchor_context(...)`
  → `complement.build_complement_context(...)` → `pd_ranking.rank_candidates(..., anchor_context=,
  complement_context=)` → `complement.order_complement_first` → DTO 組み立て。
  ranking 失敗は既存 `_apply_relevance_order` と同じく検索結果を捨てない。
- 監査記帳なし（`/search` `/citation-search` と同じ読み時導出。書き込みは参照キャッシュの
  upsert だけで、教員の判断を含まないため監査対象にしない）。
- `docs/backend/api.md` の本数表記（「17本」）を更新する。

### 5.6 env・設定まとめ（`core/config.py` 追加済み）

| env | 既定 | 意味 |
|---|---|---|
| `DISCOVERY_COMPLEMENT_THIN_MAX_DOCUMENTS` | 1 | レンズA: 配置 distinct document 数がこの値以下の concept を「薄い」とみなす |
| `DISCOVERY_FOUNDATION_MIN_CITING_SEEDS` | 2 | レンズC: 浮上させる最低の引用元シード数 |
| `DISCOVERY_FOUNDATION_FETCH_PER_CALL` | 5 | レンズC: 1操作で新規取得するシード数 |
| `DISCOVERY_REFERENCE_CACHE_TTL_DAYS` | 30 | レンズC: キャッシュの新鮮さ |
| （既存）`DISCOVERY_CITATION_SOURCE_ENABLED` | off | レンズC のオプトイン（引用グラフ供給と共用） |
| （既存）`DISCOVERY_RANKING_MAX_CALLS_PER_DAY` | 100 | レンズA/B の embedding バッチが消費する日次ゲート（新カウンタなし） |

---

## 6. コスト・外部 API

- **LLM: 0回**。embedding は `complement/search` 1回 = 1バッチ（既存ゲートを1消費）。
  前提文最大30件が同一バッチに載る分だけトークンが増える（U層 feature は既存
  `discovery:ranking` のまま — 帰属を分けるほどの量ではない。分けるなら KNOWN_FEATURES と
  allowlist 2箇所の更新が要る点を §11 に記録する）。
- **Semantic Scholar**: `foundation` 1操作 = 最大 `FETCH_PER_CALL`（5）リクエスト × 3秒スロットル
  ≒ 15秒。以降はキャッシュ。arXiv 側は `complement/search` の1リクエストのみ（通常検索と同じ）。

### 6.3 なぜキャッシュを持つか（CC3 の例外理由）

参照リストは1シードあたり数十〜百件で、毎回取り直すと 50 本のコーパスで 150 秒待つことになる。
外部 API の行儀（PD7）としても同じデータを繰り返し引かないのが筋で、写しは**外部事実**であって
教員判断・候補判定ではない。TTL で陳腐化を抑え、行削除ではなく upsert で更新する。

---

## 7. ガードレール（`backend/tests/test_corpus_complement_{core,api,guardrails,ui_static}.py`）

- **guardrails**: `complement.py` / `foundation.py` / `reference_cache.py` が FastAPI・`core.llm`・
  `url_fetch`・`_accept_material_source` を import しない（既存 `LLM_EXEMPT_FILES` 不変 =
  `test_only_ranking_touches_the_llm_layer` が自動で守る）/ core ツリーに `DELETE FROM` なし
  （既存）/ 禁止キー不在（既存）/ CC1: `complement.py` `foundation.py` が `interest_traces` /
  `frontier_interest` / `stumble` に触れない / CC5: SL1 denylist 語彙（「この分野では未検証」
  「誰も検証していない」「世界初」「未踏」）が core・JS・マニュアル節に無い + 固定文の原文存在 /
  CC6: 断定語（「おすすめ」「必読」「重要な論文」）が JS・マニュアルに無い / migration 077 に
  INSERT なし・users FK なし・冪等ガード / `references_for_arxiv` が固定ホスト・スロットル経由。
- **core**: thin_node_ids の閾値境界（≤）と region 除外 / sky_statements が candidate assumption・
  未承認 claim を使わない / fills・skies の閾値・上限・未測定の不一致扱い / rank_candidates の
  complement_context=None 完全不変 + 1バッチ（`generate_embeddings` 呼び出し1回）/
  order_complement_first の安定性 / foundation の集約・閾値・キャッシュ read-through・
  fetch_per_call・部分失敗・全失敗 raise。
- **api**: 2ルートの teacher 要求 / 502 写像 / DTO 形（`complement` の有無・`lenses`）/
  レンズ縮退でも 200。
- **ui_static**: 2アンカー担体・ES5・サーバキーの有無をそのまま描く（閾値判定なし）/
  見出し語の固定 / 〈推定〉タグ / 数値非描画 / mode 追加で既存 ingest 経路を分岐しない。

---

## 8. 3点セット（管理UI）

- `docs/manual/teacher/11-admin-materials.md` に `### コーパスを補う候補を探す {#arxiv-discovery-complement}` /
  `### 基盤論文を探す {#arxiv-discovery-foundation}`（無効化理由と解消方法・レンズ3つの意味・
  「AI（LLM）は使いません」の明記）。
- `core/help_kb/admin_ui_anchors.py` に2件追加（327 → 329。件数の正本は
  `test_admin_help_ui_anchors.py`）。
- `data-ui-anchor` 担体は §4.1 の2ボタン。
- 新しい capability は登録しない（既存 `materials.arxiv_discovery` の道案内で同じモーダルに
  到達する。LLM コストを伴わないが取り込みの弁は変わらないため、代行 capability も作らない）。

---

## 9. 非スコープ（v1）

- LLM による「この論文を読むと何が足されるか」の一段落説明（compare の拡張。まず3レンズの
  非LLM 提示で足りるか実測してから）。
- 外部の被引用数（`citationCount`）の利用・表示。
- 学習者向け表示（CR7）。
- レンズC の引用元を「引用」だけでなく「被引用」（citations）へ広げること。
- 参照キャッシュの起動時バックフィル・定期更新（教員操作の read-through のみ）。
- レンズの結果を購読条件（キーフレーズ）へ自動還流させること（PD3 — 条件を広げるのは教員）。

---

## 10. 実装時の確認事項

1. `anchors_with_labels` が返す `AnchorVector.node_kind` の値（`"concept"` / `"region"`）を
   fills の対象判定に使う（region は対象外）。
2. `landscape_placements.document_id` は UUID 列。`domain_key` 列で分野を絞れる。
3. `epistemic_ledger.document_id` は TEXT（`documents.id` の文字列）。
   `corpus.domain_document_ids` の返り値と突き合わせる。
4. Semantic Scholar graph API の references 応答形: `{"offset", "next", "data": [{"citedPaper": {...}}]}`。
   `citedPaper.externalIds.ArXiv` が無い参照は落とす（PD2）。
5. 既存 UI テスト `test_paper_discovery_ui_static.py::TestUiAnchors` は ANCHORS 定数の担体を
   数える。新アンカーは新テストファイル側で数え、既存定数を増やさない（1属性1ID の規約は共通）。

---

## 11. 実装記録（2026-09-09）

Fable 5.1 指揮 + Opus 5 ×5体（Wave 1 = core レンズA/B・core レンズC+migration・フロント+3点セット・
docs の4体並列 / Wave 2 = ルート+API・ガードレールテスト）。backend フルスイート **13,344 pass /
27 skip**（2026-09-09）。migration 077 の実 DB 適用・外部 API（arXiv / Semantic Scholar）を
伴う E2E は docker 環境で未実施。

### 実装ファイル

| 区分 | ファイル |
|---|---|
| core（新規） | `backend/core/paper_discovery/complement.py`（レンズA/B）/ `foundation.py`（レンズC）/ `reference_cache.py`（参照キャッシュ） |
| core（編集） | `ranking.py`（`rank_candidates(..., complement_context=)`・`_sky_texts` / `_attach_complement`）/ `citation_client.py`（`references_for_arxiv` / `parse_references`。URL 組み立てを `_api_url(arxiv_id, path)` に関数化し推薦・参照の2エンドポイントが同じスロットル・タイムアウト・エラー写像を通る）/ `__init__.py`（docstring + import） |
| 共通正本（編集） | `core/config.py`（env 4本）/ `core/label_vocab.py`（`COMPLEMENT_SKY_THRESHOLD=0.45`）/ `.env.example` |
| migration | `backend/db/077_paper_discovery_reference_cache.sql` |
| API | `routes/paper_discovery.py`（`POST /complement/search` / `POST /complement/foundation`・`_complement_block` / `_apply_complement_order`） |
| フロント | `admin-paper-discovery.js`（mode `complement` / `foundation`・`runComplementSearch` / `runFoundationSearch` / `complementLines` / `renderComplementNote`）/ `admin.html`（キャッシュバスター） |
| 3点セット | `docs/manual/teacher/11-admin-materials.md`（2節）/ `core/help_kb/admin_ui_anchors.py`（+2）/ `test_admin_help_ui_anchors.py`（件数更新） |
| docs | `docs/README.md` / `architecture/layer_registry.md` / `architecture/data-model.md` / `backend/api.md` / `admin_operations/materials.md` / `CLAUDE.md` |
| テスト | `test_corpus_complement_{core,foundation,api,guardrails,ui_static}.py`（47 + 36 + 37 + 26 + 42） |

### 設計からの確定事項・逸脱

1. **列名 `references` → `reference_entries`**（§5.4）。`REFERENCES` は PostgreSQL の予約語で
   引用符なしの列名に使えない。§5.4・data-model.md は追随済み。
2. **`reference_cache.fresh_failed_ids()` を追加**。`get_fresh` は `fetch_status='ok'` のみを
   「読めた」扱いにするため、TTL 内に `failed` を記録したシードを「取りに行かない・
   `pending_seeds` にも数えない」の両方を満たす読み取り専用ヘルパが必要だった（PD7）。
3. **全失敗 raise の条件を「読めた参照が1件も無い」に一般化**（§5.3 は「全シード未キャッシュ
   かつ全取得失敗」）。全シードが TTL 内 `failed` のみでも読めたものはゼロで、そこで「見つかり
   ませんでした」を返すのは PD6 に反する。「読めたが参照が空」（`ok` 行あり）は正常系。
4. **ルート層は `CitationApiError` で rollback** するため、全滅した試行の `failed` マーカーは
   捨てられ、外部 API の全面障害は TTL 抑止されず次の操作で即再試行になる（部分失敗の
   `failed` 行は commit される）。全面障害で30日抑止する方が害が大きいと判断し、この挙動を
   採用する。
5. **`complement.degraded_facts()` を追加**（§5.1 に無い）。ranking の fail-soft 5経路で事実文の
   組み立てが散らないよう complement 側に1本置いた（ranking.py に事実文リテラルを書かない）。
6. **`NOTE_NO_SKELETON` / `NOTE_NO_ANCHORS` の出し分け**: `anchor_context is None` → 前者、
   `anchors` / `skeleton_version` が空 → 後者。route の `_anchor_context` は両者を `None` に
   潰すため現状は前者が主経路。
7. **`sky_statements` の LIMIT は JOIN 済みクエリ側**（台帳行を取ってから本文解決すると、
   解決で落ちた分だけ結果が減る silent truncation になる）。assumption → claim の順に残枠を
   割り当てる。
8. **`CLOSED_WORLD_SKY_NOTE` は complement.py に定義**。`core/doubt/` に同一文言の定数は無く
   （`seminar_brief.FACT_LINE_NO_VERIFICATION_RECORD` は文言違い）、ガードレールが原文を固定する。
9. **UI: ranking note は既存 `#pd-ranking-note` に残し、`#pd-complement-note` はレンズ縮退のみ**
   （二重表示回避）。foundation DTO の `partial` は UI で読まない（サーバの `note` が事実を運ぶ。
   専用の行を出すなら文言を設計してから）。complement モードの条件行は §4.2 どおりで
   「該当件数」を含まない。
10. **ガードレールの denylist 走査は利用者可視テキストに限定**（Python は AST で docstring を除いた
    文字列リテラル、JS はコメント除去後、マニュアルは全文）。`complement.py` の docstring が規則の
    説明として禁止語を**列挙**しているため、素の全文 grep だと規則の記述自体が落ちる。
11. **U層 feature は既存 `discovery:ranking` のまま**（前提文最大30件が同一バッチに載る分の増分は
    帰属を分けるほどではない。分けるなら `KNOWN_FEATURES` と `test_llm_policy.py` /
    `test_llm_model_policy_guardrails.py` の allowlist 2箇所の同時更新が要る）。
12. 新 capability は登録しない（§8 どおり）。`core/paper_discovery/__init__.py` に
    `complement` / `foundation` / `reference_cache` を再エクスポート（route は submodule 直接 import で
    `__init__` に依存しない）。
