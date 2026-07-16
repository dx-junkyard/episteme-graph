# 個人知識ネットワーク（Personal Knowledge Network, Phase P）設計

> **状態: Phase P-0 / P-1 / P-2 / P-0.5 / P-2拡張（コース横断）/ P-3 実装済み**
> （2026-07-14 起草・P-0/P-1 実装、2026-07-15 P-2 実装、2026-07-16 に UX 提案書
> `/Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md` に基づき
> P-0.5「個人スコープへの意味論移行」/ P-2拡張「コース横断の橋」/ P-3「最上位わたしの地図」/
> §6 訂正操作（地図には反映しない）を実装 — 詳細は本書 **§16**）。
> P-2 実装物: `backend/core/personal_graph/journey.py`（純粋部 `build_journey` + 遅延import の
> `journey_for_node`。`MAX_FANOUT_PER_SEGMENT=5` / `MAX_STEPS=12`）+ queries 拡張 +
> `GET .../personal-network/journey`（GET のみ・本人のみ）+ personal-map.js 旅カード
> （マーカーポップ/トレイの「ここから旅に出る」・常に最新1枚・frontier_note は通常文体）。
> **v1 の実装判断**: [3] 他論文ホップは§6の権限規則のサブセットとして**当該コースの sources 内
> に限定**（fail-closed 最小実装。コース横断は将来拡張）。frontier_note は component/claim
> アンカー起点で confirmed リンクが空のときのみ（topic 起点は [1]-[3] を静かに省く）。
> PN-6 は W-β の `confirmed_links_for_document`（confirmed のみ返す正本）経由で構造的に担保。
> 本書は `knowledge_network_vision.md`（親文書）§7 Phase P の独立設計書であり、
> ビジョン §8 未決事項 4「ノード導出規則の詳細」を本書 §2 で確定する。
> P-0 実装物: `backend/core/personal_graph/`（schema / queries / derive / graph_data）+
> `backend/api/routes/personal_map.py`（`GET /api/learning/courses/{course_id}/personal-network`）+
> `backend/tests/test_personal_graph_{derive,guardrails}.py`。§2「同意の汲み取り」の
> N4 opt-out 規則（match + 異議なし、NULL 含む）はユーザー確認済み（2026-07-14）。
> P-1 実装物: `frontend/public/js/personal-map.js`（`window.PersonalMap`。トグル既定OFF・
> in-memory のみ・kind 別ドット4種を L1 のみに重畳・「まだ地図にない」トレイ・軌跡ビュー
> 相互リンク）+ atlas-overlay.js のガード付きフック3箇所 + app.js 統合（init/DI・
> `data-trace-id`・annotate・コース切替 invalidate）+
> `backend/tests/test_personal_map_ui_guardrails.py`（静的15項目: ポーリング禁止・
> 禁止語彙・user_id 非送信・ミニマップ非改変・ガード形式）。ミニマップ（F-1）非改変。
>
> **親文書**: `knowledge_network_vision.md`。KN-1〜KN-4 の不変条項に従う。
> **兄弟設計**: `element_deliberation_workspace_design.md`（W層。同一性リンク
> `element_identity_links` = Phase W-β は本書 §6 の旅の traversal が依存する）。
>
> **呼称**: 「Phase P」「個人知識ネットワーク」を用いる。「P層」とは呼ばない
> （tension 等の不変条項 P1〜P7 との混同を避ける）。コード接頭辞は
> `personal_graph`（backend）/ `personal-map`（frontend）。

---

## §0 位置づけと不変条項

### 何を解くか

学習者の痕跡は既に大量にある — 本人が引き受けた tension、確定した構造アンカー、
再構成の成功、本人が張った接続（`connect`）。しかしこれらは digest・軌跡ビューに
**バラバラに**現れるだけで、「自分の理解のネットワークが公共の知識構造の上に
どう育っているか」を本人が見る手段が無い。Phase P はこれを:

1. **ノード導出規則の確定**（§2）— どの痕跡が個人ネットワークのノードになるか
2. **「わたしの地図」**（§9）— atlas 個人層の本格化（本人のみ可視）
3. **旅の経路探索**（§6）— 自分のノードから公共構造を辿る決定論的 traversal

として形式化する。**個人ネットワークは書かれるのではなく、彫り出される**:
学習者は自分のグラフを著述しない。本人確定の痕跡から**決定論的に導出される投影**が
個人ネットワークである（ビジョン §1-1/1-5、G層「完了フラグを持たず状態から導出」思想）。

### 立場（他層と同じ「読む側」）

A層成果（`theory_component_graphs` / `theory_claims`）・atlas 骨格・L層 library_entries・
W-β `element_identity_links` を**読むだけ**。B層の痕跡（`interest_traces` /
`learner_reconstructions`）も読むだけで、書き込みは既存 API（confirm / connect / dismiss）
に一切手を入れない。

### 不変条項（PN-1〜PN-7）

- **PN-1 本人のみ可視**: 個人ネットワークを本人以外（教員・管理者・他学習者）に見せる
  API・UI を作らない。教員向け集約は Phase B（k-匿名 k=3・`core/privacy.py` 正本・
  橋候補のみ）まで**存在しない**。評価利用禁止（P3 継承）。
- **PN-2 導出であって記録ではない**: ネットワークの確定保存・完了フラグを持たない。
  毎回サーバ状態から決定論的に導出する（G1 と同族）。源の痕跡が dismiss / supersede
  されれば次の導出から自然に消える（行削除はしない・P4）。
- **PN-3 candidate を数えない**: ノードにするのは**本人が引き受けた痕跡のみ**。
  LLM 候補（tension `candidate`・anchor `llm_candidate`）、AI 検出の誤解
  （`personal_graph.misconceptions_by_topic` — 本人確定経路が無い）は入れない（P1 継承）。
  ただし「引き受け」の要求水準は二層（§2「同意の汲み取り」）: AI が内面を解釈した出力は
  明示 confirm 必須、**本人が著述した成果物への非LLM判定は「異議を挟まず進み続けた」ことで
  足りる**（opt-out）。
- **PN-4 数値を見せない**: 踏破率・網羅率・件数・スコア・順位を本人にも出さない
  （atlas「踏破率を数値にしない」の継承。自分の痕跡の**一覧**は可、**集計数値**は不可）。
  バッジ・ランキング化しない（P7 継承）。
- **PN-5 旅は非LLM・決定論・境界付き・明示操作のみ**: traversal に LLM を使わない。
  fan-out・深さは固定上限、順序は決定論的（`created_at, id`）。自動で開かない
  （atlas 導線の抑制ルールを踏襲）。提示は事実文のみ（煽らない・宣言しない）。
- **PN-6 リンクは confirmed のみ辿る**: `element_identity_links` の candidate を
  traversal に使わない（KN-3。誤った同一視は誤った知識より有害）。
- **PN-7 fail-closed**: 学習者が閲覧できない document への hop は黙って省く（件数も
  出さない）。骨格なし分野では地図系 UI を出さない（atlas 継承）。W-β 未実装環境では
  同一性リンク区間が「まだ道が無い」になるだけで、エラーにしない。

KN-1（神の視点を作らない — すべて egocentric）/ KN-2（正規化は追加）/
KN-3（同一視は人間確定）/ KN-4（3系統分離 — 個人ネットワークをドメイン知識候補に
自動昇格させない）を継承する。

---

## §1 スコープ（v1）

- **コース単位**の個人ネットワーク（`(user_id, course_id)` スコープ。`learning_states` の
  受講単位と一致）。コース横断ビューは非スコープ（§15）。
- 対象学習者: 受講中の本人のみ。教員向けは何も作らない（PN-1）。
- 3成果物: `core/personal_graph/`（導出・アクセサ・旅）+ 学習者 API 2本 + 「わたしの地図」UI。
- **新 migration なし**（v1。導出は既存テーブルの読みだけ。§5）。
  旅の同一性リンク区間のみ W-β（migration 046 同乗）に依存し、無ければ縮退（PN-7）。

---

## §2 ノード導出規則（本書の心臓部・ビジョン §8 未決4 の確定）

ノードは**公共構造への参照（アンカー）+ 本人の関与の種別**である。独立した私的語彙の
ノードを作らない（重ね合わせ＝アンカーID交差を well-defined にする。ビジョン §3 修正①）。

### 採用する痕跡（すべて本人確定のみ・PN-3）

| # | 由来 | 採用条件（実カラム・実語彙） | node_kind |
|---|---|---|---|
| N1 | 引っかかり | `interest_traces.kind='tension'` AND `status IN ('open','articulated','connected','abstracted')`（= `TENSION_OWNED_STATUSES`、`core/tension/schema.py` 正本） | `tension` |
| N2 | 構造帰属付きの問い | `kind='question'` AND `payload.structure_anchor.attribution_source IN ('learner_selected','confirmed')` AND `payload.structure_anchor.status='active'` AND `status <> 'superseded'` | `question` |
| N3 | 帰属なしの問い | `kind='question'` AND `status <> 'superseded'` AND（structure_anchor 無し or `llm_candidate` のみ）。**llm_candidate 帰属は使わず**、topic 粒度の粗いアンカーに落とす | `question`（coarse） |
| N4 | 再構成の成功 | `learner_reconstructions.machine_verdict='match'` AND `self_check NOT IN ('disagreed','verdict_wrong')`（**NULL＝異議なしとして含める**。下記「同意の汲み取り」）。改訂チェーン（`revision_of`）は**終端行のみ**で判定（改訂し続けた最新状態が本人の現在地） | `reconstruction` |

### 採用しない痕跡（明示・P4 で保持はされる）

- tension `candidate` / `dismissed` / `unclassified`、anchor `llm_candidate`（本人未確定）
- `status='superseded'`（書き直し・削除で取り除かれた往復の派生痕跡）
- `kind='misconception'` と `personal_graph.misconceptions_by_topic`（AI 検出であり
  本人確定経路が無い。本人 confirm フローができたら N5 として追加 — §15）
- `kind='raw'` / `kind='detour'`（detour は既存 atlas 個人層の**足あと**
  （`payload.atlas.node_id` → now/footprints/visited、`atlas_view._personal_layer`）に
  留める。ノード化しない）
- 再構成の `mismatch`、および**本人が異議を挟んだもの**（`self_check='disagreed'` /
  `'verdict_wrong'`）。これらは「理解ノード」ではなく、つまづき集約（R層 stumble・k-匿名）の
  領分。個人ネットワークに失敗を刻まない — 煽らない。**self_check 未実施（NULL）は除外しない**
  （下記「同意の汲み取り」）

### 同意の汲み取り（本人確定の二層区別・2026-07-14 確定）

「本人確定」の要求水準は、痕跡の出所で二層に分かれる:

1. **AI が学習者の内面を解釈した出力**（tension の paraphrase・anchor の LLM 帰属候補）:
   **明示 confirm 必須**（P1。AI が本人の口に言葉を入れるため、opt-in でしか確定しない）。
   N1/N2 はこの層 — 既存の confirm フローの結果だけを数える。
2. **学習者自身が著述した成果物への決定論的照合**（再構成 + 非LLM DIFF verdict）:
   成果物は本人の言葉で既に書かれており、verdict は機械的比較の事実。ここでは
   **「その方向に進み続け、判断に異議を挟まなかった」ことから同意を汲み取る（opt-out）**。
   明示クリックを要求せず、異議（`disagreed` / `verdict_wrong`）・改訂（revision チェーンの
   継続）だけが取り消しシグナルになる。確認の儀式を増やさない低ストレス UX。

なお R層のループ上 SELF-CHECK は必須ステップのままであり（R層設計は非改変）、本規則は
「SELF-CHECK を完了しなかった match をノード導出から**落とさない**」という導出側の判断である。

### アンカー解決（ノード → 公共構造の対応）

structure_anchor の語彙（`claim | equation | derivation_step | concept | stage | chunk |
segment`）をそのまま使い、粗い粒度への縮退も B層既存の
`claim → concept → chunk → segment` 順に従う。追加の対応:

| 痕跡 | アンカー |
|---|---|
| N1 connect 済み tension | `payload.connected_refs.component_ids / edge_ids`（`connect_tension_trace` が connect 操作時に**本人が指定した ID のみ**で書く専用キー。実装レビューで判明した通り、LLM 候補生成時点で書かれる `payload.target_refs` は本人未確認のまま非空になり得るため、`status='connected'` かつ `connected_refs` が非空のときのみこのアンカーを使う。それ以外（`target_refs` しか無い等）は N1 未接続として扱う） |
| N1 未接続 tension / N3 | `topic_id` → `topics[].atlas_node_id`（コース⇄地図バインディング。無ければ topic 粒度のまま） |
| N2 | `payload.structure_anchor.anchor_type / anchor_id / anchor_label` |
| N4 | `claim_id`（`learner_reconstructions` 非正規化列） |

アンカーが atlas 骨格に対応づかないノードも**捨てない**（P4）—「まだ地図にない」
トレイとして UI に出す（§9）。

---

## §3 エッジ意味論

エッジは**本人の行為または DB 上の事実**からのみ導出する。AI 推測のエッジを作らない
（KN-3）。v1 は3種のみ:

| edge_kind | 由来（事実） | 事実文の例 |
|---|---|---|
| `bridge` | tension `connect`（`status='connected'` + `payload.connected_refs`。本人が connect 操作で明示的に指定した ID のみ・LLM 候補由来の `target_refs` は使わない）。**本人が明示的に張った唯一の著述辺**であり、Phase B「多くの学習者がここに橋を架ける」集約の入力になる | 「この引っかかりを◯◯に自分でつないだ」 |
| `revision` | `learner_reconstructions.revision_of` チェーン | 「改訂を経てたどり着いた再構成」 |
| `descend` | `learner_reconstructions.descended_to_symbol=TRUE` → 対応する symbol 葉 | 「原因を絞るため記号まで降りた」 |

同一アンカーを持つノード同士は**エッジで結ばず同座グルーピング**で表現する
（推測を辺として固定しない）。時間近接・セッション共起による推測エッジは作らない（§15）。

**P-0 実装ノート**: §2 で revision チェーンの終端行のみをノード化するため、`revision` /
`descend` は両端がノードになる辺として実体化しない。P-0 では **`bridge` のみ**を辺として
出力し、revision / descend はノードの**事実フィールド**（`facts` に「改訂を経てたどり着いた
再構成」「原因を絞るため記号まで降りた」）として保持する（情報は落とさない・P4。
回数は出さない・PN-4）。過去行を個別ノード化する必要が出たら辺形式に昇格する。

---

## §4 導出アルゴリズム（非LLM・決定論）

```
derive_personal_network(user_id, course_id) -> PersonalNetwork
  1. interest_traces を (user_id, course_id) で読み、§2 の条件でフィルタ
  2. learner_reconstructions を (user_id, course_id) で読み、N4 条件でフィルタ
     （revision_of チェーンは最新のみノード化）
  3. 各痕跡をアンカー解決（§2 の表。コースの atlas binding は course_data.py アクセサ経由）
  4. §3 の3種エッジを組み立て
  5. アンカー同値でグルーピングし、決定論順（created_at, id）で返す
```

- 全行が本人スコープ（数十〜数百行）なので毎回導出で十分軽い。キャッシュしない（v1・PN-2）。
- 弱み付け（`weight` / DecayPolicy）は**使わない**（v1）。減衰で「消えていく」表現は
  煽り（もう忘れた?）と紙一重なので、単純な時系列表示に留める（§15）。

---

## §5 `core/personal_graph/` と JSONB アクセサ

`backend/core/personal_graph/`（**FastAPI 非 import**・開発ルール2）:

```
__init__.py
schema.py    → node_kind / edge_kind / anchor 語彙・dataclass の正本（PersonalNode / PersonalEdge / PersonalNetwork / JourneyStep）
graph_data.py→ learning_states.personal_graph JSONB の正本アクセサ（course_data.py と同じ
               「素の dict アクセス禁止」方式。PersonalGraphData Pydantic・extra="allow"・
               misconceptions_by_topic アクセサ）
derive.py    → §4 の導出（interest_traces / learner_reconstructions の読み + フィルタ）
journey.py   → §6 の旅の traversal（非LLM・境界付き）
queries.py   → SQL 読みプリミティブ（core.postgres.get_session 直読み）
```

- **`learning_states.personal_graph` 列の位置づけを確定する**: 既存どおり
  「AI が見つけた誤解や個別メモの差分」の置き場であり、**個人ネットワークの格納庫では
  ない**（PN-2: ネットワークは導出であって保存しない）。列への素の dict アクセスは
  `graph_data.py` アクセサに一本化し、既存の直接参照（`services.py` の
  `record_misconception` 系 3〜4 箇所）を移行する（Tier 3-18 course_data と同じ
  機械的移行。他レイヤのロジックは変えない）。
- ビジョン §7 の「`core/personal_graph.py` アクセサ新設」は、導出・旅を含むため
  単一ファイルでなく**パッケージ**として実装する（正本の所在は本書）。

---

## §6 旅の経路探索（Journey）

ビジョン §1-6「つながりが見出せないときは、局所ネットワークから関心が芽生える
ネットワークをたどる旅に出る」の実装。**経路の形はビジョン §7 で固定済み**:

```
本人のノード（起点）
  → [1] 論文ローカルグラフ（theory_component_graphs.graph_json、main layer 優先）
  → [2] element_identity_links（confirmed のみ・PN-6）
  → [3] L層ハブ（library_entries、active のみ）→ 他論文のインスタンス
  → [4] atlas 骨格 node（topics[].atlas_node_id / build_concept_signals）
  → [5] atlas node 近傍にある本人の別ノード
```

### 境界（PN-5）

- 深さは上記5区間で固定（それ以上遡行しない）。各区間の fan-out ≤ 5、
  経路全体の提示 step ≤ 12。順序は決定論的（`created_at, id`）。乱択なし。
- **起点アンカーが解決する document 自体の閲覧可否をまず確認する**（実装レビューで
  判明した抜け穴の是正）: `journey_for_node` は component/claim アンカーから document を
  解決した直後、`services.user_can_view_document` 相当の判定を通し、閲覧不可なら
  ローカルグラフ・同一性リンクを一切読まず document が見つからなかった場合と同じ経路
  （[1]〜[3] を省く）に倒す。`core/personal_graph/` は FastAPI / services を import しない
  規約のため、判定関数は呼び出し側（`routes/personal_map.py`）が
  `journey_for_node(..., can_view_document=user_can_view_document)` として注入する。
- [3] の他論文インスタンスは**学習者が閲覧可能な教材のみ**（受講コースの
  `sources[].material_id` / public 教材に限定。判定不能な document は黙って省く・PN-7）。
  加えて `connect_tension_trace`（tension を component/edge に接続する既存 API）も
  connect 時点で component の閲覧可否を検証し、不正・閲覧不可な参照は接続自体を拒否する
  （journey が閲覧不可 document の情報を漏らす経路を connect 時点で断つ）。
- [3] の L層ハブは **active な `library_entries` 行のみ**を経由する。retired・存在しない
  shared_part は（generic な「共通部品」名へのフォールバックも含め）hop 自体を生成しない
  ——active であることをハブ traversal の前提条件として扱う。
- 各区間は独立に縮退する: 骨格なし → [4][5] を省く / W-β 未実装 or confirmed リンク
  0件 → [2][3] を省く / connect 未実施の tension → topic 粒度から [4] へ直行。

### 提示（事実文・数値なし）

各 step は `{fact_sentence, ref}`。事実文はテンプレート合成（非LLM）:

> 「この問いは論文 A の claim◯に触れている」→「claim◯は共通部品 H の論文 A での
> 表れとして教員が確認している」→「H は論文 B にも現れる」→「H は地図の
> 『◯◯』にある」→「あなたの3月の引っかかりも『◯◯』の近くにある」

- 経路が途切れる場所は**そのまま正直に**出す: 「ここから先はまだ道が無い」。
  エラー表示・警告色にしない（D層「空欄スコープは発見」と同族）。
- 提示は明示操作（「ここから旅に出る」ボタン）のみ。自動で開かない。カードは常に
  最新1枚・直近閲覧の抑制など atlas cues（F-2）の流儀を踏襲する。

---

## §7 API（`backend/api/routes/personal_map.py`、実パス `/api/learning/...`、本人のみ）

> **P-0.5 追記（2026-07-16）**: 本節のコース配下 API は「コースビュー」＝互換ラッパー。
> 正本は個人スコープの `GET /api/me/personal-network` / `GET /api/me/personal-network/journey`
> （同ファイル `me_router`。§16 参照）。

| メソッド・パス | 役割 |
|---|---|
| `GET /api/learning/courses/{course_id}/personal-network` | §4 の導出結果 `{nodes, edges}`（各 node は `atlas_node_id` nullable — 「まだ地図にない」はクライアント側で導出）。**非LLM・DB 非変更**。受講ゲートは `get_accessible_course_data`（既存）。集計数値なし（PN-4） |
| `GET /api/learning/courses/{course_id}/personal-network/journey?node_id=...` | §6 の旅（steps / 縮退情報）。明示操作でのみ呼ぶ |

- レスポンスに confidence・weight・件数集計を含めない（PN-4。ノードの列挙自体は本人の
  データなので可）。
- 教員向け・管理者向けエンドポイントは**作らない**（PN-1。Phase B で別途設計）。
- 書き込み API は無い（導出のみ・PN-2）。confirm / connect / dismiss は既存 API のまま。

---

## §8 監査・コスト

- **監査**: 読み取り専用層のため `theory_review_events` への記帳は**しない**
  （既存の read 系 API と同じ扱い。confirm / connect 等の状態変更は既存層で監査済み）。
- **コスト**: LLM 呼び出しゼロ（PN-5）。コスト上限・U層 feature 語彙とも不要。

---

## §9 フロント「わたしの地図」（`frontend/public/js/personal-map.js`、`window.PersonalMap`）

- **atlas オーバーレイに「わたしの地図」トグル**を追加: 既存個人層（いまここ・足あと・霧）
  の上に、§2 のノードを kind 別ドット（問い / 引っかかり / 再構成 / 橋）で重ねる。
  骨格なし分野では領域ごと非表示（atlas fail-closed 継承）。ミニマップ（F-1）は
  **変更しない**（「いまここ + 状態ドット + 霧」のみの規約を維持）。
- **「まだ地図にない」トレイ**: atlas 対応の取れないノードをオーバーレイ脇に一覧
  （P4: 捨てない。地図に置けないことは異常ではない）。
- **問いの軌跡ビュー（既存 `GET .../interest-traces`）との関係**: 軌跡ビュー＝時系列の
  一覧、わたしの地図＝構造上の配置。同じ痕跡の2ビューであり、相互リンクする
  （軌跡アイテム→地図上の位置、地図ドット→軌跡の該当項目）。
- **旅カード**: ノード詳細から「ここから旅に出る」→ journey API → 事実文 step の
  カード表示。自動で開かない・最新1枚（§6）。
- 数値（件数バッジ・網羅率・%）をどこにも描かない（PN-4）。

---

## §10 既存層との合成(重複させない)

| 欲しいもの | 使う既存機構（新設しない） |
|---|---|
| 痕跡の確定・接続 | tension confirm/connect・anchor confirm（既存 API） |
| いまここ・足あと | atlas 個人層 `_personal_layer`（`payload.atlas.node_id`） |
| コース⇄地図対応 | atlas binding（`topics[].atlas_node_id`、course_data.py アクセサ） |
| 同一性リンク | W-β `element_identity_links`（confirmed のみ読む） |
| 共通部品ハブ | L層 `library_entries`（active のみ読む） |
| 論文ローカル文脈 | `theory_component_graphs.graph_json`（main layer 優先） |
| k-匿名集約 | Phase B で `core/privacy.py`（本 Phase では使わない=集約しない） |

---

## §11 ガードレール（`backend/tests/test_personal_graph_guardrails.py`）

`guardrail_helpers.py` を使い構造的に守る:

- `core/personal_graph/` が FastAPI / routes / services / `core.llm` を import しない
  （**ソースレベルの検査**。なお `core.tension.schema` / `core.structure_anchor.schema` の
  語彙正本 import は親パッケージ `__init__` 経由で LLM クライアント系モジュールを連鎖
  ロードするが、呼び出しは発生しない — 挙動としての非LLM（PN-5）は `derive.build_network`
  の純粋性で保証する。既存 `doubt/naive_signal.py` はこの連鎖を避けるため同値タプルを
  ローカル再定義しているが、本層は正本 import を選ぶ＝ドリフトリスクの方を重く見る）。
- 導出ソースが candidate / `llm_candidate` / `dismissed` / `superseded` を除外し、
  再構成の異議シグナル（`disagreed` / `verdict_wrong`）を処理している
  （`derive.py` ソースへの語彙アサーション）。
- API ルートが本人スコープ（`current_user["id"]` 以外の user_id を受けるパラメータが無い）
  かつ教員向けルータに登録されていない（PN-1）。
- レスポンス組み立てに集計数値（`count` / `coverage` / `%`）を出す経路が無い（PN-4）。
- journey が `element_identity_links` の confirmed 以外を読まない（PN-6）。
- 書き込み・削除 API が存在しない（PN-2 / P4）。
- 禁止語彙（「踏破」「達成率」「ランキング」等）が UI 文言・事実文テンプレートに無い。

---

## §12 issue 分割（実装フェーズ）

- **Phase P-0**: `core/personal_graph/`（schema / graph_data / derive / queries）+
  `GET .../personal-network` + ガードレール + `personal_graph` 列アクセサ移行。
  **W-β 非依存・migration 不要**。ここまでで軌跡が構造化されて返る。
- **Phase P-1**: 「わたしの地図」UI（atlas トグル + kind 別ドット + まだ地図にないトレイ +
  軌跡ビュー相互リンク）。
- **Phase P-2**: 旅（`journey.py` + journey API + 旅カード + 抑制ルール）。
  [2][3] 区間は **W-β 実装後に有効化**（それまで縮退動作で出荷可）。
- **Phase B（本書外・親文書 §7）**: bridge の k-匿名集約 → 教員レビューへの橋候補。

---

## §13 非スコープ（v1）

- 個人ネットワークの他者公開・共有（本人のみ可視が既定。ビジョン §8 非スコープ）
- 教員向けの個人ネットワーク閲覧・集約（Phase B で k-匿名集約のみ）
- LLM による経路説明・要約・推薦（PN-5）
- ネットワークの保存・スナップショット・履歴比較（PN-2）
- コース横断の統合ビュー（§15）
- 誤解ノード（本人 confirm フロー未整備のため。§15）

---

## §14 既知の限界（正直に）

- **N3（帰属なしの問い）は topic 粒度**で粗い。構造帰属付きの問いが増えるほど地図は
  精密になる — これは「アンカー確定 UI を使う動機」として設計上むしろ健全。
- **旅の [2][3] 区間は同一性リンクの確定量に比例**して育つ。確定は人間のみ（KN-3）
  なので初期は疎。W層 cross-corpus レンズが確定コストを下げる（ビジョン §3 修正④）。
- 受講解除・コース削除時は `learning_states` / traces の既存ライフサイクルに従う
  （本 Phase で新たな削除経路を作らない）。

---

## §15 未決事項

1. **誤解ノード（N5）**: `misconceptions_by_topic` に本人 confirm フローを付けてから
   ノード化するか（P1 を守る形でのみ）。
2. ~~**コース横断ビュー**~~ → **§16 で解決済み**（2026-07-16）。個人スコープ正本 API
   `/api/me/personal-network` + 最上位「わたしの地図」パネルとして実装。
3. **weight / DecayPolicy の扱い**: 減衰を「薄くなる」表現に使うか、使わないままか。
   煽り（忘却の可視化）にならない表現が見つかるまで使わない。
4. **時間近接エッジ**: 同セッション共起を「点線の弱い辺」として出すか。推測辺は
   KN-3 の精神に反しやすく、v1 は見送り。
5. **journey の提示条件の詳細**: どの画面のどのノード詳細に「旅に出る」を置くか
   （atlas 導線①〜④との整合。Phase P-2 着手時に確定）。

---

## §16 個人スコープへの意味論移行（Phase P-0.5 / P-2拡張 / P-3、2026-07-16 実装）

仕様の正本: `/Users/Shared/issues/episteme_graph_personal_knowledge_network_ux_proposal.md`。
本書 §1 の「コース単位スコープ」を次のように**読み替える**:

> 個人知識ネットワークの所有単位は常に **`user_id`（本人）**であり、`course_id` は
> 所有境界ではなく **provenance（学習が起きた出所）+ 表示フィルター**である。
> コース配下 API（§7）は「コースビュー」＝互換ラッパーとして維持する。
> コースを削除・終了しても本人の痕跡は本人の地図から消えない（タイトルが引けなくなる
> だけでノードは残る）。

### P-0.5: 個人スコープの正本 API

- `PersonalNode.course_id`（`kw_only`・既定 None）を追加（provenance。schema.py）。
- `derive.build_person_network(traces, reconstructions, atlas_by_course)`（純粋関数）:
  course_id でグルーピング → 既存 `build_network` をコースごとの topic_atlas で呼び →
  course_id をスタンプしてマージ・(created_at, id) 再ソート。DB 起点は
  `derive_person_network(user_id)`。queries に `fetch_traces_for_user` /
  `fetch_reconstructions_for_user` / `fetch_topic_atlas_binding_for_courses` /
  `fetch_course_titles` を追加。
- `derive.group_nodes_by_anchor(network)`: 同じ公共アンカー（anchor_type, anchor_id）を
  またぐ複数コース痕跡を1グループへ束ねるレスポンス表現（提案書 §5.2「同じ公共アンカーを
  コースごとに複製しない」）。件数フィールドなし（PN-4）。
- **`GET /api/me/personal-network`**（`me_router`、main.py 登録済み・nginx `/api/me/` は
  既存 proxy）: `{nodes, edges, anchor_groups, courses:{course_id:{title}}}`。
  クエリ: `context_type=course&context_id=`（コースビューへの投影）/ `focus_anchor_id` /
  `include_candidate_links`（**true は 422**。候補リンクは本人確定まで含めない fail-closed。
  opt-in の余地を作らない）。受講ゲートなし（本人自身の痕跡のみ・PN-1）。
- **`GET /api/me/personal-network/journey?node_id=`**: コース横断の旅（下記）。
- me_router は**読み取り専用**（書き込みメソッド禁止をガードレールで固定）。

### P-2拡張: コース横断の橋（journey.py）

- `build_person_journey(...)`（純粋関数）: 既存 `build_journey` と同じ5区間を全コースの
  個人ネットワークに対して辿る。差分は3点 — [3] のフィルタが「当該コース限定」でなく
  「can_view_document で事前フィルタ済みの viewable_document_ids」/ atlas 解決が各ノードの
  course_id ごとの binding / [5] の兄弟探索が全コース（同一 atlas 解決 or 非topicアンカー
  (anchor_type, anchor_id) 完全一致。後者は atlas が無くても提示）。別コースの兄弟の
  事実文はコース名を含める（「あなたが『◯◯』で残した問い『…』もここにつながっています」。
  タイトル不明は「以前の学習」）— 元のコース文脈と表現を失わない（提案書完了条件）。
- `journey_for_person_node(user_id, node_id, can_view_document)`: DB 起点。
  can_view_document が callable でなければ他インスタンス hop を**空集合**に倒す
  （既存 journey_for_node の後方互換スキップより厳しい fail-closed）。
- 既存 `journey_for_node`（コーススコープ）の戻り値に **`cross_course_hint`** を追加:
  別コースに同一アンカーの兄弟が存在すれば
  `{"fact": "以前の学習につながる道があります", "node_id": ...}`、無ければ null。
  コース名・件数は伏せる（本人が開いたときだけコース外へ移動する。提案書 §3.2-B）。
  hint 計算は例外を握って null に倒し、単一コースの旅を壊さない。

### §6 訂正操作: 地図には反映しない / 地図に戻す

- `services.set_trace_map_exclusion(user_id, trace_id, excluded)`: 本人の tension/question
  行の `payload.map_excluded` を jsonb_set で更新（**status は触らない** — dismiss（候補の
  当落判定）とは独立の表示除外。行削除しない・P4）。監査は `record_review_event` で
  既存カタログ定数（tension → AUDIT_ENTITY_TENSION / question →
  AUDIT_ENTITY_STRUCTURE_ANCHOR）に記帳。
- `POST /api/learning/traces/{trace_id}/map-exclude` / `.../map-restore`（learning.py。
  personal_map.py には置かない — 読み取り専用ガードレール維持）。
- `GET .../interest-traces` の各項目に `map_excluded: bool` を付与（restore 導線用）。
- 導出側は `build_network` が `payload.map_excluded` truthy な trace をスキップ
  （コースビュー・個人スコープの両方に自動で効く）。

### P-3: 最上位「わたしの地図」（frontend）

- `personal-map-home.js`（新規・`window.PersonalMapHome`: init/open/close/invalidate）。
  ヘッダの「わたしの地図」ボタン（`#my-map-btn`、地図ボタンの隣）から開く全画面パネル。
  データソースは `/api/me/personal-network` のみ。常設注記
  「この地図はあなたにだけ表示されます。成績評価には使用されません。」（提案書 §7.1）。
- タブ3つ（提案書 §3.2 A/C/D。B「このコースでの地図」は既存 personal-map.js の担当）:
  **いまの地図**（直近痕跡=現在地 + 同一アンカーグループ + 直近5件。巨大グラフを作らない）/
  **問いからの旅**（question/tension 新しい順・上限20件・「すべて見る」なし）/
  **振り返り**（月別グルーピング・件数/進捗率なし）。各ノードから「ここから旅に出る」→
  `/api/me/.../journey` の事実文カード（常に最新1枚・ref.kind 別の種別ラベルで
  専門家確認済み共通部品/理論構成/教材/あなたの痕跡を区別 — 提案書 §2.5）。
  空状態は「まだ痕跡がありません。…」（空の巨大地図を見せない §3.1）。
- `personal-map.js` 拡張: 旅カードに cross_course_hint の静かな一行 +
  「以前の学習につながる道を見る」（押下時のみ横断版へ差し替え）/ マーカーポップに
  「地図には反映しない」（tension/question のみ）/ 問いの軌跡の除外済み項目
  （`data-map-excluded="1"`、付与元 app.js）に「地図に戻す」チップ。
- fetch はすべて明示操作起点・ポーリングなし・fail-closed（失敗は「いまは表示できません」）。

### ガードレール追加

- `test_personal_graph_person_scope.py`（17）: 個人スコープ導出・アンカーバンドル・
  map_excluded フィルタ・決定論。
- `test_personal_graph_journey_person.py`（19）: 横断兄弟の事実文・閲覧不可 hop 非生成・
  上限・`_has_cross_course_sibling`。
- `test_personal_graph_map_ops.py`（29）: 本人行のみ・status 非変更・DELETE なし・
  監査カタログ定数・一覧 enrich。
- `test_personal_map_home_ui_static.py`（静的）: ポーリング禁止・禁止語彙・プライバシー
  注記・user_id 非送信・正本 API 使用・横断/除外の結線。
- 既存 `test_personal_graph_guardrails.py` に me_router 読み取り専用・
  include_candidate_links fail-closed・map_excluded 語彙の検査を追加。

### v1 の正直な限界

- `context_type` は `course` のみ（document コンテキストは別 issue）。`cursor`
  ページングなし（本人痕跡は数十〜数百行で全件返しても提案書の「巨大グラフ一括取得」には
  当たらない — nodes はカード列挙用で、クライアントは局所表示のみ）。
- 教材オープン時の「過去の理解との接続」事実文（提案書 §4.1）は旅カードの
  cross_course_hint までで、トピック表示への自動掲出は未実装（自動で開かない原則を
  優先。導線設計が固まったら別 issue）。
- AI候補（tension digest / anchor digest）は地図・わたしの地図に出さない（PN-3 維持。
  提案書 §2.5 の「AIが見つけた候補」の表示面は既存 digest UI が担う）。
