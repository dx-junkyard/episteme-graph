# わたしの地図の広がり装置（名前のある霧・共通部品の糸・晴れ間の近接・分野接続行）

> **状態:** 実装済み（正本）— 2026-08-22 起案・同日 v1 実装。migration 不要
> （既存テーブル・凍結骨格の読みのみ）。以後は §7 実装記録の追記のみ

親文書は [personal_knowledge_network_design.md](personal_knowledge_network_design.md)
（PN-1〜PN-7）と [personal_map_nearby_design.md](personal_map_nearby_design.md)
（PMN-1〜PMN-7・§10 範囲モード）。本書は「学習者が立っている場所から、その分野での
広がりを見せ、好奇心を喚起する」ための4つの装置を定義する。

---

## §0 好奇心の文法（本書の設計原理）

この系で許される唯一の好奇心装置は**「存在だけを事実として見せ、詳細は本人の明示操作
まで伏せる」**である（情報ギャップの正直な提示）。既存の実証: `cross_course_hint`
（「以前の学習につながる道があります」— コース名・件数は開くまで伏せる）/ atlas の霧
（未踏を数値化せず霧として見せる）/ SL層の晴れ間（検証記録の不在を発見の候補地として
提示）。本書の4装置はすべてこの文法の適用であり、次を**やらない**:

- 推薦・督促（「次はここを見よう」）・演出（光る・バッジ）・未踏カウント
- AI 生成の紹介文（推定を事実の顔で見せない。全装置とも非LLM・決定論）
- 数値・件数・割合（PN-4/PMN-4。上限超過は黙って切るか「など」で示す）

不在について言えるのは閉世界語彙のみ:
**「このコーパスの中では検証記録がありません」**（SL1/PMN-3 継承）。

## §1 装置1: 名前のある霧（atlas 隣接概念の名前提示）

学習者の現在地（atlas_node_id 解決済みの本人痕跡）から、**凍結骨格の隣接概念の
「名前だけ」**を淡く見せる。骨格の名前・隣接エッジ・領域所属は教員が凍結した事実
（`atlas_skeletons`、読みは `atlas_store.load_learner_skeleton` = 凍結版のみ）なので
推定ゼロで出せる。

- **API**: `GET /api/me/personal-network/atlas-neighbors?node_id=`（`me_router`・
  本人のみ・読み取り専用・GET のみ）。DTO は
  `{available, here:{label, region_label}, neighbors:[{id, label, region_label,
  relation: edge|sibling}], note}`。neighbors は骨格エッジ接続（edge）→ 同領域の
  兄弟概念（sibling）の順・骨格出現順・**最大8件**（黙って切る）。座標・seed_status・
  件数は返さない。
- **実装**: `backend/core/personal_graph/atlas_fog.py`（FastAPI/LLM 非 import）+
  `queries.fetch_atlas_concept_context`（骨格読みの集約点。atlas_store は遅延 import）。
- **UI**（`personal-map-home.js`「いまの地図」タブ・現在地の直後）: 見出し
  「この場所の隣にあるもの」+ 淡いチップ（非インタラクティブ — 骨格に説明文が無いため
  開くものが無い。名前が装置のすべて）。`available:false`・取得失敗は**何も描かない**
  （霧は装飾。エラー文言を出さない）。
- **縮退**: atlas_node_id 無し / cartridge 明示値なし / 凍結骨格なし / concept 突合不能
  → `available:false` + note「この記録は、まだ分野の地図に結びついていません。」

## §2 装置2: 共通部品の糸（confirmed 同一性リンクの水平提示）

nearby 点ビュー（near/root）の**中心ノード限定**で、confirmed 同一性リンク（W-β）→
L層 active エントリ → 他論文インスタンスの事実文を `facts` に追記する:

> 共通部品『◯◯』は、論文『△△』にも現れます。

- 規則は旅の [2][3] 区間の**鏡写し**: confirmed のみ（PN-6）/ L層 active のみ・名前が
  引けないエントリは糸ごと不生成（generic フォールバック禁止）/ 他 document は
  `can_view_document` フィルタ・callable でなければ糸を一切出さない（fail-closed）。
- (部品名, タイトル) で重複除去・ソート決定論順・**最大3行**（件数を言わない）。
- 例外は握って糸を落とすだけ（support_fact_line と同じ fail-soft）。range では出さない。

## §3 装置3: 検証の晴れ間の近接提示

nearby 点ビューで `ledger_available` のとき、**表示集合に入らなかった** main ノードの
うち台帳 status が `untested` / `unknown` のものがあれば facts 末尾に1行:

> この近くには、このコーパスの中では検証記録がない場所があります：『A』、『B』、『C』 など。

- 3件以下は「など」なしで全列挙。4件以上は先頭3件（main_nodes 決定論順）+「 など。」
- **台帳行が無いノードは対象外**（「行が無い＝何も主張しない」の既存意味論を崩さない。
  nearby v1 の `.no-ledger` 中立表示と同じ線引き）。

## §4 装置4: 範囲ビュー→分野の地図の接続行

範囲モード（§10 範囲モード）の facts 末尾に、topic が atlas binding を持つ場合のみ:

> このトピックは、分野の地図の『{領域}』にある『{概念}』に対応づけられています。

- 解決: `fetch_topic_atlas_binding` → `course_cartridge_id`（明示値のみ）→
  凍結骨格の concept/region ラベル。突合不能・骨格なし・例外は行を落とすだけ（fail-soft）。
- 論文スケール（範囲ビュー）から分野スケール（atlas）への視線の持ち上げが目的。

## §5 縮退の規則（共通）

全装置とも: 計算できなければ**その装置の行・ブロックだけが静かに消える**。ビュー本体・
既存の facts は影響を受けない。エラー表示・警告色にしない（P4「空欄は発見」と同族）。

## §6 非スコープ（v1）

- 霧チップから骨格説明を開く導線（骨格に description が無いため開くものが無い。
  骨格スキーマ拡張は atlas 側の議論）
- 範囲ビュー・淡いノードへの糸マーカー（中心限定の facts のみ。チップ click は
  中心移動に割当済みで衝突するため）
- k-匿名の「他の学習者の橋」提示（PN-1 の再検討が必要。Phase B は教員向けのみ）
- 霧の既読管理・表示履歴（PN-2: 状態を保存しない）

## §7 実装記録（2026-08-22, Fable 指揮 + Sonnet 2体並列）

| 層 | 実装先 |
|---|---|
| 装置1 backend | `core/personal_graph/atlas_fog.py`（新規）+ `queries.fetch_atlas_concept_context` + `GET /api/me/personal-network/atlas-neighbors` |
| 装置2〜4 backend | `nearby.py` の facts 拡張（点ビュー: 糸・晴れ間 / 範囲: 分野接続行） |
| フロント | `personal-map-home.js`「いまの地図」タブの霧ブロック（装置2〜4は既存 facts 描画で自動表示のためフロント変更なし）+ `styles.css` `.pm-home-fog-*` |
| テスト | `test_personal_map_fog.py`（新規）+ `test_personal_map_nearby.py` 追記 + `test_personal_map_home_ui_static.py` 追記 |

- 併せて `personal-map-home.js` の `invalidate()` が `nearbyCache` / `fogCache` を
  破棄するよう修正（従来は `cache` のみ破棄で、ログアウト後に前ユーザーの nearby 結果が
  in-memory 残留し得た。PN-1 由来の修正）。
