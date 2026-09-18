# 課題ナレッジ — 分類体系（正本）

[← 課題ナレッジの入口](README.md) ｜ [記入様式](TEMPLATE.md) ｜ [辞書](dictionary.md) ｜ [索引](index.md)

> **状態:** 生きたリファレンス（2026-09-18 新設・同日改訂 = 分類の確定状態・族/型の2段・解決観点の分割・層語彙。2026-09-19 排他の主分類を廃止し 4 軸の座標に。同日 §2.1〜2.4 = 軸ごとの確信度・境界の相手・新設の提案・値のライフサイクル・再評価）。語彙を増やす・意味を変えるときは本書を先に直し、
> `backend/tests/test_issue_knowledge_guardrails.py` の語彙表を同じ変更で追随させる。

本書は、`docs/` 配下で管理してきた課題（調査記録・レビュー文書・是正リスト・設計書の残課題）を
**原因の性質**で分類し、**どの観点で見つけたか**・**どの観点で解いたか**を語彙で残すための
体系である。目的は 2 つ。

1. 新しい課題が管理対象になったとき、同型の課題が過去にどう見つかり・どう解かれたかを引けること。
2. 課題を機能・症状の場所から切り離して一般化し、あとで**辞書**（[dictionary.md](dictionary.md)）に
   畳めること。

---

## 1. 四つの軸と座標（排他の主分類はない）

課題の原因の性質を **4 本の軸**で記述する。各軸の値は §2 の語彙から **1〜2 個**、または単独の
`none`（見て、要素が無いと判断した）か `unknown`（まだ見ていない）。1 つの課題は複数の軸に値を
持ってよい（実データでは 7 割の課題が 2 軸に値を持つ。排他の「主分類」は 2026-09-19 に廃止 —
記述と決定を 1 つの欄に押し込み、構造が受け皿になっていた）。

| 軸 | 値 | 定義（この軸に要素があるかを問う） |
|---|---|---|
| 処理 | `processing` | 入力・契約・前提はすべて妥当で、**単一処理の不良**がある |
| 構造 | `structure` | **情報表現・責務分担・分割・集約の設計**が、必要な挙動を妨げている。表現や責務を変えなければ再発する |
| 接続 | `connection` | **段階間**で情報・意味・条件・対象・版が失われる、または前後の契約が両立しない。各段は単体では正しく見える |
| 統制 | `governance` | **担当割り当て・順序・予算・レビュー・停止再開・完了判定**に問題がある。処理も表現も正しいのに、いつ・誰が・どこまでやるかが崩れている |

### 1.1 二つの群は座標から導く

| 群 | 条件 |
|---|---|
| 局所 | 構造・接続・統制の 3 軸がすべて `none`（処理軸だけに値がある） |
| 構造・接続・統制 | 構造・接続・統制のいずれかに値がある |
| （未判定） | 3 軸のどれかが `unknown` で、値のある軸も無い |

群を手で書かない（索引が導出する）。

### 1.2 判定の手順（原因の性質で決める）

1. **原因を一文で書く**。「〜が〜を〜するため」の形で、症状ではなく原因を主語にする。
2. 4 軸それぞれに問う。**「この軸の何かを変えなければ再発するか」**。
   - はい → その軸の値（§2）を 1〜2 個置く。2 個目は、1 個目を直しても残る要素があるときだけ。
   - いいえ（見て、無い） → `none`。
   - まだ見ていない・判断材料が無い → `unknown`（`cause_status: hypothesis` と併用する。§3）。
3. 記述と決定を分ける。座標は**原因がどこにあるか**の記述。**どの軸を変えて直したか**は
   `resolution.perspective` と `landed_in` が持つ（座標に「主」を書かない）。
4. 原因が確定していないなら `cause_status: hypothesis` とし、座標は仮説としての座標である
   と明示する（§3）。

### 1.3 分類に使ってはならない基準

- **症状が出た場所**（フロント／バックエンド／DB、ファイル名、層名）。場所は `feature_context`
  に書き、座標の根拠にしない。
- **修正の行数・ファイル数・規模**。1 行の修正でも、その 1 行が段階間の契約の欠落なら接続軸に値がある。
- **修正した手段**。ガードレールを足して直したから統制軸、とはしない（手段は
  `resolution.perspective` に書く）。
- **発見した人・発見した経路**。オーナー指摘で見つかった課題が統制軸に寄るわけではない。

`classification.basis` には「各軸のどの要素が定義に当たるか（none にした軸はなぜ無いと判断したか）」を
書く。上の禁止基準を根拠にした記述（「フロント側のバグなので」「1 行なので」）は機械検査で弾く。

---

## 2. 軸の値（語彙）

`classification.axes.<軸>` に置ける値。表のトークンは `軸.値` 表記だが、エントリには値だけを書く
（`structure: [representation, aggregation]`）。統制軸の値は**制御系（実行時）**と**手続系（人）**に
分けて読む（軸は分けない — 実データで分けるべきかは確定レビュー後に判断）。

| 軸 | 値 | 意味 | 境界が曖昧になりやすい相手 |
|---|---|---|---|
| processing | `processing.input_handling` | 入力の取り扱い（正規化・境界値・空・重複）の不良 | `processing.logic` |
| processing | `processing.logic` | 条件式・分岐・計算の誤り | `connection.contract`, `processing.input_handling`, `processing.regression` |
| processing | `processing.resource` | 接続・ファイル・メモリ・例外処理の後始末不良 | `governance.completion` |
| processing | `processing.wording` | 表示文言・ラベル・翻訳の不良（挙動は正しい） | `connection.meaning` |
| processing | `processing.regression` | 過去に正しかった単一処理が変更で壊れた | `processing.logic` |
| structure | `structure.representation` | 情報表現（ID・キー・列・語彙・型）が必要な区別や同一性を表せない | `connection.information`, `connection.version`, `governance.resume`, `structure.aggregation` |
| structure | `structure.responsibility` | 責務の置き場所（どの層・どの関数が判断するか）が不適切・二重 | `connection.condition`, `governance.assignment`, `structure.aggregation`, `structure.decomposition` |
| structure | `structure.decomposition` | 分割の粒度（大きすぎる／細かすぎる・混在） | `connection.target`, `structure.responsibility` |
| structure | `structure.aggregation` | 集約・正本の一本化が無く、同型実装・語彙表・状態が分散 | `governance.review`, `structure.representation`, `structure.responsibility` |
| connection | `connection.information` | 段階間で**情報**（値・行・フィールド）が落ちる | `connection.contract`, `structure.representation` |
| connection | `connection.meaning` | 段階間で**意味**（ラベルの解釈・座標系・単位・語彙）がずれる | `connection.contract`, `processing.wording` |
| connection | `connection.condition` | 段階間で**条件**（権限・可視性・オプション・前提）が伝わらない | `connection.target`, `governance.assignment`, `structure.responsibility` |
| connection | `connection.target` | 段階間で**対象**（どの document／run／ユーザー／コースか）がずれる | `connection.condition`, `structure.decomposition` |
| connection | `connection.version` | 段階間で**版**（run・凍結版・revision・キャッシュ世代）がずれる | `governance.resume`, `structure.representation` |
| connection | `connection.contract` | 前段の出力契約と後段の入力契約が両立しない（片方だけ変えた） | `connection.information`, `connection.meaning`, `processing.logic` |
| governance（制御系） | `governance.ordering` | 実行・判定・記帳の順序 | `governance.budget`, `governance.resume` |
| governance（制御系） | `governance.budget` | 回数・コスト・上限・スロットル | `governance.ordering` |
| governance（制御系） | `governance.resume` | 停止・再開・再実行・冪等性 | `connection.version`, `governance.ordering`, `structure.representation` |
| governance（手続系） | `governance.assignment` | 誰が（人／AI／どの層が）判断・実行するかの割り当て | `connection.condition`, `governance.completion`, `structure.responsibility` |
| governance（手続系） | `governance.review` | レビュー・確認・承認の手続と記録 | `governance.completion`, `structure.aggregation` |
| governance（手続系） | `governance.completion` | 完了判定・状態の正本・「済み」の定義 | `governance.assignment`, `governance.review`, `processing.resource` |

| 空の値 | 意味 |
|---|---|
| `none` | この軸を見て、要素が無いと判断した |
| `unknown` | まだ見ていない。`cause_status: confirmed` と同居できない |

「境界が曖昧になりやすい相手」は対称で、モジュール側 `NEIGHBORS` と逐語一致する（機械検査）。
分類者が 2 つの値で迷った記録（`history` の変更前後）が積もれば、この列を更新する。

### 2.1 軸ごとの確信度（`classification.axis_confidence`）

4 軸それぞれに、置いた値（`none` を含む）への確信度を 3 段で付ける。数値は使わない。

| 値 | 意味 |
|---|---|
| `high` | 定義に当たる／当たらないことを根拠から直接言える |
| `medium` | 当たると読めるが、相手の値との境界で迷った |
| `low` | 判断材料が薄い。`none` なら「見たが無いと言い切れない」、値なら「置いたが相手の方かもしれない」 |

`unknown` の軸に `high` は置けない。AI が起こした確信度は AI の自己申告で、人が座標を確定するとき
（§3.1）に確信度も確定する。

### 2.2 新設の問い（`classification.proposals`）

確信度が `low` か `medium` の軸には、**「この軸に新しい値、または新しい軸が要るか」**を問う。
要ると考えたら提案を 1 件書く（要らなければ書かない。`proposals: []`）。

```yaml
proposals:
  - kind: value              # value = 既存の軸に値を足す / axis = 新しい軸
    target_axis: connection  # kind=value のとき必須。kind=axis のとき null
    neighbor_of: [connection.version, structure.representation]  # 境界が曖昧になる相手（既存の 軸.値）
    statement: 機能名を含まない一文で、足したい値（軸）が何を区別するか
    confidence: medium       # high | medium | low
```

- `kind` は `value`（既存の軸に値を足す）か `axis`（新しい軸）。
- 提案は**同じ種別・同じ軸・同じ相手**で束ねる（statement の字面では束ねない）。
- 束の中で確信度 `high` が **3 件以上**あれば「設定候補」として索引に浮上する。人が §2 の表に
  **暫定の値**として足すまで体系は変わらない。3 件に至らない束も消さない（次の 1 件を待つ）。
- 新しい**軸**の提案は、既存 4 軸のどの定義にも当たらないことを 3 件が独立に述べることを条件に
  する（値より一段重い変更）。

### 2.3 値のライフサイクル（暫定 → 成立 / 見送り）

| 状態 | 条件 | 索引 |
|---|---|---|
| 暫定 | 設定候補から人が足した直後。モジュール `PROVISIONAL_VALUES` に載せ、相手（§2 の列）を必ず宣言 | §13 に使うエントリと再評価キューを表示 |
| 成立 | `classification.review: confirmed` のエントリが 2 件以上使う | `PROVISIONAL_VALUES` から外す |
| 見送り | 使われないまま残る。値は表から外してよいが、下の記録に残す | — |

見送りの記録（値・日付・理由）: なし（2026-09-19 時点）。

### 2.4 再評価（新設のたびに全件を見直さない）

暫定の値を足したら、**その値の相手（§2 の列）に当たる軸で確信度が `low` か `medium` のエントリ**だけを
再評価キュー（索引 §13）に出す。再評価では新設値を含めて軸の値を置き直し、座標が変われば `history` に
追記する。再評価の連鎖は 1 段で止め、そこから出た新しい提案は次の巡回で扱う。

---

## 3. 原因の確度

`classification.cause_status` は 2 値。

| 値 | 意味 | 書き方 |
|---|---|---|
| `confirmed` | 原因をコード・データ・再現で確認した | `basis` に確認手段（file:line・実測・再現手順）を含める |
| `hypothesis` | 原因は未確定。分類は仮説 | `basis` を「仮説:」で始め、何を確認すれば確定するかを書く |

`hypothesis` の課題は索引で仮説として区別して表示される。座標に `unknown` の軸があるときは必ず
`hypothesis`。解決時に原因が確定したら `confirmed` に直し（`unknown` は `none` か値に倒す）、座標が
変わったなら `history` に旧座標を残す（情報を落とさない）。

### 3.1 分類の確定状態（`classification.review`）

原因の確度とは別に、**座標そのものを人が確定したか**を持つ。AI（Opus 等）が起こしたエントリの
分類は候補であり、人が確定するまで辞書の型の成立には数えない（この仕組み自身も
「AI は候補まで・確定は人」に従う）。

| 値 | 意味 | 併記 |
|---|---|---|
| `candidate` | AI または起票者の一次分類。未確定 | `reviewed_by: null` / `reviewed_at: null` |
| `confirmed` | 人が 4 軸の値・型・根拠文を読んで確定した | `reviewed_by`（確定者の識別子）/ `reviewed_at`（日付）必須 |

索引は候補と確定を区別して表示し、型の成立（§6.1）は確定エントリだけで数える。

---

## 4. 発見観点（discovery.perspective）

「どのような見方をしたときに、この課題が見えたか」。複数可。**課題を再発見するための
レンズ**として引けることが目的なので、経路（誰が言ったか）ではなく観点（何と何を突き合わせたか）
で書く。

| 値 | 観点 | 典型 |
|---|---|---|
| `symptom_report` | 利用者・オーナーの症状報告から遡った | 「グラフが読めない」「ボタンが多い」 |
| `invariant_audit` | 不変条項・原則（vision §6・各層 P*/DM*/LS* 等）と現行実装を照合した | 六つのレンズ §2 是正 |
| `boundary_walk` | 2 機能の境界（保存×生成／再実行×キャッシュ／オプション×run 継承／権限×投影）を意図的に歩いた | 開発チェックリスト §3 |
| `doc_code_diff` | 文書と実装を突合した（索引の欠落・番号ずれ・正本不在） | docs 総点検 |
| `data_inspection` | 実 DB・実 artifact・実ログを読んだ | scratch DB 実測 |
| `trace_walk` | 入力から出力まで 1 本の経路を辿り、途中で落ちる値を追った | claim ID の永続化経路 |
| `adversarial_review` | 実装後に「壊すつもりで」読む敵対的レビュー | Phase 1〜4 レビュー是正 |
| `inventory` | 全件棚卸し（同型実装・語彙表・kind・ステージの一覧化） | agent 棚卸し |
| `guardrail_failure` | 既存ガードレール・テストの失敗が示した | 網羅テストの赤 |
| `reproduction` | 再現手順を組んで確認した | 429 の再現 |
| `external_constraint` | 外部 API・法・制度・費用の制約から逆算した | arXiv レート制限 |

---

## 5. 解決観点（resolution.perspective）

「どのような見方で解決策を見つけたか」。修正の手段そのものではなく、**解決に至った
見立て**を書く。**最大 2 つ・先頭が主観点**（3 つ以上並べると集計が受け皿化する）。`guardrail_fix` は
単独で主観点にしない（再発防止は常に別の見立ての従）。未解決の課題は `resolution.perspective` を
空にせず `pending` を置く。発見観点も同じく最大 2 つ・先頭が主。

| 値 | 観点 |
|---|---|
| `single_point_fix` | 単一処理を直せば足りると見立てた（`local` の正常な出口） |
| `canonical_source` | 正本を 1 つに決め、他を委譲・ミラーにする |
| `explicit_contract` | 暗黙の前提を**宣言**として明示する（固定注記・状態ヘッダ・規約文・カタログ）。呼び出し契約は `required_argument`、語彙は `vocabulary_table`、状態は `first_class_state` を使う |
| `required_argument` | 省略可能だった条件・対象を必須引数・必須キーにして、渡し忘れを型で弾く |
| `vocabulary_table` | 自由記述・散在する語彙を語彙表（enum・FK・ミラー固定）に集約する |
| `first_class_state` | 暗黙の状態・単位・判断を一級の行・列・型にして正本を持たせる |
| `representation_change` | 情報表現（キー・列・ID・版の持ち方）を変える |
| `responsibility_move` | 判断・実行の責務を別の層・別の主体（人／AI）へ移す |
| `carry_through` | 段階間で落ちていた情報・条件・対象・版を後段まで運ぶ |
| `fail_closed` | 判定できないときは閉じる側に倒す |
| `state_transition` | 削除・上書きを状態遷移（supersede / revoked / dismissed）に置き換える |
| `order_and_budget` | 順序・予算・停止再開の統制を入れる（ゲート位置・スロットル・冪等マーカー） |
| `guardrail_fix` | 再発を構造的に禁じるテスト・検証を置く（他の観点と併記する） |
| `doc_correction` | 文書側を直す（実装は正しかった） |
| `deferred_decision` | 人の判断（オーナー裁定・制度前提）待ちとして保留する |
| `pending` | 未解決（解決観点なし） |

---

## 6. 一般化と辞書化

課題を「この機能でこう壊れた」で終わらせず、辞書に畳めるように次を書く。

- `feature_context.realizing` — **どの機能を実現しているときに**この課題が出るか（機能の目的を
  動詞で。「再解析で成果を差し替えつつ教員の確定を保つ」）。
- `feature_context.layers` — 関係する層の識別子。**語彙は [layers.md](layers.md) の表に限る**（自由記述は
  索引を壊す。表に無い層は先に layers.md へ足す。場所の記録であって分類の根拠ではない）。
- `generalization.level` — どこまで一般化できるか。

| 値 | 意味 |
|---|---|
| `instance` | この機能・この実装に固有。他所で同型は出にくい |
| `repo_pattern` | このリポジトリ内で複数の機能に再発する型 |
| `general` | ソフトウェア一般に見られる型 |

- `generalization.general_form` — 機能名・層名・ファイル名を含まない一文で、課題の型を書く
  （「再実行が前回の人間の判断を上書きする」）。辞書の見出しになる。
- `pattern` — [dictionary.md](dictionary.md) の**型**（`####` 見出し）の slug。既存の型に当たればそれを
  使い、無ければ辞書に型を 1 つ足す（辞書に無い slug はガードレールが弾く）。

### 6.1 辞書の 2 段構成と型の成立

辞書は **族（family, `###`）→ 型（type, `####`）** の 2 段。族は座標空間の大づかみな領域
（同一性の表現・条件と対象の伝達・再実行と削除 など）で、**主に効く軸**の節（## 処理 / 構造 / 接続 /
統制）の下に置く。型はその中の見分けのつく形で、座標空間の小さな領域に当たる。近縁の型（人間向け呼称の衝突と識別子の衝突、範囲の広がりと狭まり）は
統合せず同じ族に置く。

| 型の状態 | 条件 | 索引での扱い |
|---|---|---|
| 成立 | `classification.review: confirmed` のエントリが 2 件以上 | 通常表示 |
| 暫定 | 確定エントリが 1 件以下（候補だけ、または確定 1 件） | 「暫定」と表示。族への畳み込み候補 |

エントリが 0 件の型は置けない（ガードレール）。型の表には「典型的な座標」を書き、索引は型ごとに
エントリの実際の座標の分布を並べる（典型と実際のずれが分類レビューの入口。典型は拘束ではない）。

### 6.2 課題どうしの関係

- `related` — ゆるい関連（従来どおり）。
- `view_of` — **同じ原因の別視点**（恒久解と暫定解、規約の不在と経路の残課題）。索引は
  `view_of` で結ばれたエントリを 1 束にして表示する。束の代表は ID の小さい方。

---

## 7. 課題の状態

`status` は課題そのものの状態。原因の確度（§3）とは別軸。

| 値 | 意味 |
|---|---|
| `open` | 管理対象。未解決 |
| `resolved` | 解決済み（`resolution` に解決観点と着地先を書く） |
| `deferred` | オーナー判断・制度前提・実測待ちで保留 |
| `rejected` | 課題として成立しない、または意図的な設計と判定 |
