# 課題の型の辞書

[← 課題ナレッジの入口](README.md) ｜ [分類体系](taxonomy.md) ｜ [索引](index.md)

> **状態:** 生きたリファレンス（2026-09-18 新設・同日改訂 = 族/型の 2 段構成。2026-09-19 座標化に伴い「主分類の傾向」→「典型的な座標」）。**族**（`###`）は原因の
> 性質の大づかみな括り、**型**（`####`）はその中の見分けのつく形で、エントリの `pattern` は型の slug を
> 参照する。**エントリの無い型は置かない**（ガードレールが弾く）。型の成立（確定エントリ 2 件以上）と
> 暫定の区別、各型に属するエントリの一覧、典型と実際の座標のずれは [index.md](index.md)
> が機械生成する（ここに手で列挙しない）。規則は [taxonomy.md §6.1](taxonomy.md)。

各族の見出し直後に族の表（一般形・主に効く軸・含む型）、各型の見出し直後に型の表（一般形・典型的な
座標・一般化レベル・典型的な発見観点・典型的な解決観点・見分け方）を書く。典型的な座標は拘束ではなく、
索引が型ごとに実際の座標の分布を並べる。表の値は語彙
（taxonomy §4/§5）で、エントリ側と同じ。「どの機能を実現するときに出るか」は個々のエントリの
`feature_context.realizing` が持ち、辞書側には**機能名・層名・ファイル名を書かない**。

型は「直さなければ再発する原因の性質」で置く。同じ症状（例: 権限漏れ）でも原因が違えば別の型
（判定結果が後段に伝わらない → `condition-not-propagated` / 対象の取り違え → `scope-widened-silently` /
表示側が権限を推測 → `permission-and-affordance-asymmetric`）。近縁の型は統合せず同じ族に置く
（人間向け呼称の衝突 `same-name-different-referents` と識別子の衝突 `id-namespace-conflated`、
範囲の広がり `scope-widened-silently` と狭まり `entry-scope-mismatch`）。

---

## 処理

処理軸の族が小さいのは、`docs/` で管理対象になる課題が調査・レビュー由来で、単一処理の不良は
通常テストと修正で閉じて文書に残らないため（[README.md §1](README.md) の対象範囲の宣言）。

### single-processing-fault

| 項目 | 内容 |
|---|---|
| 族の名 | 単一処理の不良 |
| 一般形 | 入力・契約・前提はすべて妥当で、単一の照合・例外処理・文言だけが誤っている |
| 主に効く軸 | `local` |
| 含む型 | `substring-match-false-positive` / `failure-reported-as-success` / `wording-mismatch` |

#### substring-match-false-positive

| 項目 | 内容 |
|---|---|
| 一般形 | 語・別名・ラベルの照合を部分文字列一致で行い、短い語が無関係な語の内部に当たって誤検出する |
| 典型的な座標 | 処理=logic / 構造=aggregation / 接続=meaning（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection`（誤った概念名が実データに出る）/ `trace_walk` |
| 典型的な解決観点 | `single_point_fix`（語境界付き一致）+ `canonical_source`（照合器を 1 本に）+ `guardrail_fix`（`in text` の禁止） |
| 見分け方 | 照合対象に短い語（数文字の略号）が含まれるか。含まれるなら部分一致は必ず誤検出する |

#### failure-reported-as-success

| 項目 | 内容 |
|---|---|
| 一般形 | 失敗を握りつぶして成功を返し、利用者は別の症状（消えた・保存されない）として経験する |
| 典型的な座標 | 処理=resource / 統制=completion（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `inventory`（例外処理の全列挙）/ `trace_walk` |
| 典型的な解決観点 | `explicit_contract`（失敗を返す）+ `single_point_fix` |
| 見分け方 | 例外を捕まえた後に成功と同じ戻り値を返している箇所があるか |

#### wording-mismatch

| 項目 | 内容 |
|---|---|
| 一般形 | 挙動は正しいが、表示文言・ラベル・呼称が利用者の理解や他所の語彙と一致しない |
| 典型的な座標 | 処理=wording / 構造=representation（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `symptom_report` / `invariant_audit`（禁止語彙・数値非表示などの原則照合） |
| 典型的な解決観点 | `single_point_fix` / `canonical_source`（語彙表の一本化） |
| 見分け方 | 文言だけ直して終わるか。文言の出所が複数あって食い違うなら構造の型 `duplicate-canonical-sources` |

## 構造

### identity-representation

| 項目 | 内容 |
|---|---|
| 族の名 | 同一性の表現 |
| 一般形 | 対象を一意に指し、版や文脈をまたいで同じものと分かる表現が無い・衝突する |
| 主に効く軸 | `structure.representation` |
| 含む型 | `id-not-stable-across-versions` / `id-unique-only-within-inner-scope` / `id-namespace-conflated` / `same-name-different-referents` / `unit-of-work-undefined` |

#### id-not-stable-across-versions

| 項目 | 内容 |
|---|---|
| 一般形 | 対象を指す識別子が実行・版・出現順に依存して付け直され、版をまたいだ参照・判断が迷子になる |
| 典型的な座標 | 構造=representation / 接続=version（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection` / `trace_walk` / `adversarial_review` |
| 典型的な解決観点 | `representation_change`（内容由来の安定キー・対応表）+ `state_transition` |
| 見分け方 | 同じ対象に 2 回目の処理で別の ID が付くか。付くなら、それを参照している側の判断が全部宙に浮く |

#### id-unique-only-within-inner-scope

| 項目 | 内容 |
|---|---|
| 一般形 | 内側のスコープ（章・鎖・印字番号）でのみ一意な識別子を外側のキーに流用し、別スコープの対象が同じキーに衝突して逆引きが曖昧になる、または一意制約で落ちる |
| 典型的な座標 | 処理=logic / 構造=representation（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection` / `reproduction` / `adversarial_review` |
| 典型的な解決観点 | `representation_change`（キーの材料に外側の文脈を足す）+ `guardrail_fix` |
| 見分け方 | 「〜の中で N 番目」という局所の名前を全体のキーにしていないか。`id-not-stable-across-versions` は版をまたぐ安定性、本型は同一版内の一意性の範囲 |

#### id-namespace-conflated

| 項目 | 内容 |
|---|---|
| 一般形 | 由来の異なる名前空間の識別子（構造内の採番と永続行の ID など）を同じ型・同じ引数で扱い、解決できない参照が下流で例外になる |
| 典型的な座標 | 構造=representation / 接続=target（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `trace_walk` / `symptom_report` |
| 典型的な解決観点 | `explicit_contract`（名前空間を型で分ける）/ `fail_closed`（解決不能は 404） |
| 見分け方 | 同じ引数に 2 種類の ID が渡り得るか。`same-name-different-referents` は人間向けの呼称の衝突、本型はコード内の識別子の衝突 |

#### same-name-different-referents

| 項目 | 内容 |
|---|---|
| 一般形 | 文脈の中でだけ一意な名前（段階名・番号・略称）が文脈を外して引用され、別のものを指す |
| 典型的な座標 | 構造=representation / 接続=meaning（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `inventory` / `doc_code_diff` |
| 典型的な解決観点 | `explicit_contract`（文脈付きの完全名・一次情報への参照）+ `canonical_source` |
| 見分け方 | その名前を単独で検索したとき、複数の別物に当たるか |

#### unit-of-work-undefined

| 項目 | 内容 |
|---|---|
| 一般形 | 処理・学習・承認の「単位」が定義されておらず、粒度の違う対象が同じ操作で扱われて挙動が崩れる |
| 典型的な座標 | 構造=decomposition+representation / 接続=target / 統制=resume（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `invariant_audit` / `symptom_report` / `data_inspection` |
| 典型的な解決観点 | `representation_change`（単位を一級の行・型にする）+ `explicit_contract` |
| 見分け方 | 「何を 1 つと数えるか」を答えられないなら本型 |

### canonical-source-split

| 項目 | 内容 |
|---|---|
| 族の名 | 正本の分裂 |
| 一般形 | 同じ事実・判定・部品の正本が複数あり、または投影が正本を名乗る |
| 主に効く軸 | `structure.aggregation` / `structure.responsibility` |
| 含む型 | `duplicate-canonical-sources` / `projection-mistaken-for-source` / `destructive-action-without-confirmation` |

#### duplicate-canonical-sources

| 項目 | 内容 |
|---|---|
| 一般形 | 同じ事実（語彙表・判定・分割規則・状態）の正本が複数あり、片方だけ更新されて分裂する |
| 典型的な座標 | 構造=aggregation（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `inventory` / `doc_code_diff` / `boundary_walk` |
| 典型的な解決観点 | `canonical_source`（正本を 1 つに決め、他は委譲・逐語ミラー）+ `guardrail_fix`（ミラー一致の固定） |
| 見分け方 | 「どちらが正しいか」を決めないと直せないなら本型。正本が 1 つで伝達だけ欠けるなら接続の型 |

#### projection-mistaken-for-source

| 項目 | 内容 |
|---|---|
| 一般形 | 投影・生成ログ・キャッシュを正本として読み書きし、正本と投影の依存方向が倒錯する |
| 典型的な座標 | 構造=representation+decomposition（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection` / `trace_walk` / `inventory` |
| 典型的な解決観点 | `canonical_source` / `representation_change` / `responsibility_move` |
| 見分け方 | 正本を直しても投影を直さないと挙動が変わらないなら倒錯している |

#### destructive-action-without-confirmation

| 項目 | 内容 |
|---|---|
| 一般形 | 取り消せない操作の確認が共通部品として公開されておらず、経路ごとに確認の水準がばらつき、無確認の経路が残る |
| 典型的な座標 | 構造=aggregation / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `repo_pattern` |
| 典型的な発見観点 | `inventory`（不可逆操作の全列挙）/ `invariant_audit` |
| 典型的な解決観点 | `canonical_source`（確認部品を境界の外へ公開）+ `guardrail_fix` |
| 見分け方 | 同じ重大度の操作を横に並べたとき確認水準が揃っているか。削除は丁寧で凍結は素、なら本型 |

### missing-representation

| 項目 | 内容 |
|---|---|
| 族の名 | 表せない情報 |
| 一般形 | 居場所の無い情報が削られ、否定の判断・開示・レジーム差・領域差が表現に存在しない |
| 主に効く軸 | `structure.representation` |
| 含む型 | `information-dropped-as-unrepresentable` / `negative-decision-second-class` / `disclosure-not-declared` / `threshold-reused-across-regimes` / `domain-vocabulary-hardcoded` |

#### information-dropped-as-unrepresentable

| 項目 | 内容 |
|---|---|
| 一般形 | 表現に居場所の無い情報（上限超え・語彙外・不確かな対応・部分結果）が黙って削られる |
| 典型的な座標 | 構造=representation / 接続=information（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection` / `trace_walk` / `invariant_audit`（「情報を落とさない」原則） |
| 典型的な解決観点 | `representation_change`（`unknown` / `review_required` / `coverage` などの受け皿を作る） |
| 見分け方 | 削られた情報を後から復元できるか。できないなら本型。受け皿はあるが渡していないなら接続の型 |

#### negative-decision-second-class

| 項目 | 内容 |
|---|---|
| 一般形 | 肯定の判断（承認・確定）は理由と帰属を伴って残るのに、否定の判断（却下・撤回・見送り）は痕跡がほとんど残らない |
| 典型的な座標 | 構造=representation / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `inventory`（判断経路の全列挙）/ `invariant_audit`（帰属必須・情報を落とさない） |
| 典型的な解決観点 | `representation_change`（理由・帰属の列を否定側にも）+ `state_transition` |
| 見分け方 | 却下した記録から「誰が・なぜ」を再構成できるか。承認側だけできるなら本型 |

#### disclosure-not-declared

| 項目 | 内容 |
|---|---|
| 一般形 | 実際に起きている開示・転送を表す軸が表現に無く、当事者に宣言されないまま運用される |
| 典型的な座標 | 構造=representation / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `invariant_audit` / `doc_code_diff`（実装が送っているものと告知の突合） |
| 典型的な解決観点 | `explicit_contract`（軸を宣言し値を実装から導く）+ `canonical_source` |
| 見分け方 | 「誰に・名前つきで・再利用可能に・外部へ」渡るかを一軸の可視性で答えようとしていないか |

#### threshold-reused-across-regimes

| 項目 | 内容 |
|---|---|
| 一般形 | 尺度の成立条件が異なる対象（言語・比較対象の種類・分布）に同じ閾値・段階表を当て、常に同じ側の判定になる |
| 典型的な座標 | 構造=representation / 接続=meaning（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection`（実測分布）/ `symptom_report`（一度も発火しない） |
| 典型的な解決観点 | `representation_change`（レジームごとの表）+ `canonical_source` |
| 見分け方 | 閾値を校正した対象と、適用している対象の比較条件は同じか |

#### domain-vocabulary-hardcoded

| 項目 | 内容 |
|---|---|
| 一般形 | 構造から導けるはずの配置・分類・順序を、対象領域の語彙に依存した規則で決めるため、別の領域・別の対象で機能しない |
| 典型的な座標 | 処理=logic / 構造=representation（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `invariant_audit`（領域非依存の原則）/ `symptom_report` |
| 典型的な解決観点 | `representation_change`（構造だけから導く）+ `guardrail_fix` |
| 見分け方 | 規則の中に特定の分野・特定の対象の語が定数として現れるか |

### boundary-and-granularity

| 項目 | 内容 |
|---|---|
| 族の名 | 分割と粒度 |
| 一般形 | 責務・入口・検査対象の分割が粗すぎる／細かすぎる／実装の形に依存している |
| 主に効く軸 | `structure.decomposition` / `structure.responsibility` |
| 含む型 | `full-update-clobbers-unrelated-fields` / `entry-point-proliferation` / `test-pins-implementation-text` |

#### full-update-clobbers-unrelated-fields

| 項目 | 内容 |
|---|---|
| 一般形 | 一部の状態だけ変えたいのに全体を書き戻す操作を使い、無関係な列・入れ子構造を壊す |
| 典型的な座標 | 構造=responsibility+representation / 接続=contract（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `adversarial_review` / `data_inspection` / `boundary_walk` |
| 典型的な解決観点 | `responsibility_move`（遷移専用の入口を切る）/ `explicit_contract` |
| 見分け方 | 書き戻す側が知らない列が対象に増えると壊れるか |

#### entry-point-proliferation

| 項目 | 内容 |
|---|---|
| 一般形 | 機能を足すたび同じ場所へ入口が並列に増え、優先度も総量も誰も管理していない |
| 典型的な座標 | 構造=decomposition / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `repo_pattern` |
| 典型的な発見観点 | `symptom_report` → `inventory`（同種入口の棚卸し） |
| 典型的な解決観点 | `pending`（情報設計の判断が先）→ 頻度による階層化 + 入口を増やさない規律 |
| 見分け方 | 個々の追加は妥当か。妥当なのに密度が問題なら本型 |

#### test-pins-implementation-text

| 項目 | 内容 |
|---|---|
| 一般形 | 不変条項の検査が実装の字面（関数本体・逐語）に依存し、意味を変えない再構成を妨げる |
| 典型的な座標 | 構造=responsibility / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `inventory` / `guardrail_failure`（正しい変更で赤になる） |
| 典型的な解決観点 | `responsibility_move`（検査対象を挙動・契約へ）+ `explicit_contract` |
| 見分け方 | 挙動を変えないリファクタでテストが落ちるか |

## 接続

### condition-and-scope-transfer

| 項目 | 内容 |
|---|---|
| 族の名 | 条件と対象の伝達 |
| 一般形 | 前段で決まった条件（権限・可視性・オプション）や対象の範囲が後段に正しく渡らない |
| 主に効く軸 | `connection.condition` / `connection.target` |
| 含む型 | `condition-not-propagated` / `scope-widened-silently` / `entry-scope-mismatch` |

#### condition-not-propagated

| 項目 | 内容 |
|---|---|
| 一般形 | 前段で決まった条件（権限・可視性・オプション・前提）が後段に渡されず、後段が無条件で動く |
| 典型的な座標 | 接続=condition（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `boundary_walk`（権限×投影 / オプション×run 継承）/ `adversarial_review` / `trace_walk` |
| 典型的な解決観点 | `carry_through`（必須キーワード引数で運ぶ）+ `fail_closed`（無ければ閉じる）+ `guardrail_fix` |
| 見分け方 | 各段は単体テストで正しい。境界を歩いたときだけ落ちる |

#### scope-widened-silently

| 項目 | 内容 |
|---|---|
| 一般形 | 対象（どの文書・コース・利用者・実行か）の範囲が段階間で黙って広がり、見えてはならないものが見える |
| 典型的な座標 | 接続=condition / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `adversarial_review` / `trace_walk` / `invariant_audit`（fail-closed 原則） |
| 典型的な解決観点 | `carry_through`（対象集合をクエリ内で強制）+ `fail_closed` |
| 見分け方 | 「全域の可視集合」で判定しているが、本来の根拠は「この対象への正規アクセス」ではないか |

#### entry-scope-mismatch

| 項目 | 内容 |
|---|---|
| 一般形 | 入口が示す対象の範囲より実際の問い合わせの範囲が狭く、差分が黙って落ちる |
| 典型的な座標 | 構造=aggregation / 接続=target（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `trace_walk` / `adversarial_review` |
| 典型的な解決観点 | `carry_through`（対象を入口の粒度で受け渡す）+ `canonical_source`（件数と一覧の母数を一本化） |
| 見分け方 | 対象が 1 件しか無い環境では完全に正しく動くか。動くなら範囲のずれは隠れている。`scope-widened-silently` の逆向き（狭まり） |

### contract-between-stages

| 項目 | 内容 |
|---|---|
| 族の名 | 段階間の契約 |
| 一般形 | 前後の契約が両立しない・検査されない・代替値や資料由来テキストが契約の外から入る |
| 主に効く軸 | `connection.contract` / `connection.meaning` |
| 含む型 | `contract-changed-one-side` / `unchecked-type-contract-at-boundary` / `fallback-fabricates-missing-link` / `untrusted-input-reaches-instruction` |

#### contract-changed-one-side

| 項目 | 内容 |
|---|---|
| 一般形 | 前段の出力契約か後段の入力契約の片方だけを変え、両者が両立しなくなる |
| 典型的な座標 | 処理=logic / 接続=contract / 統制=completion（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `guardrail_failure` / `boundary_walk` / `reproduction` |
| 典型的な解決観点 | `explicit_contract`（型・語彙表・必須引数で固定）+ `guardrail_fix`（双方向網羅） |
| 見分け方 | 変更が片側のリポジトリ領域（フロント／API／agent／永続化）に閉じていないか |

#### unchecked-type-contract-at-boundary

| 項目 | 内容 |
|---|---|
| 一般形 | 値の形（単一値か列か・型）の契約が境界で検査されず、取り違えた形のまま下流で展開され、壊れた値が最終利用者まで到達する |
| 典型的な座標 | 構造=representation / 接続=contract（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection` / `trace_walk` |
| 典型的な解決観点 | `explicit_contract` + `representation_change`（検証の効く入れ物へ移す） |
| 見分け方 | 壊れた値が途中の検証を素通りしたか。自由形式の入れ物や生成出力を検査なしに通していないか。`contract-changed-one-side` は片側を「変えた」型、本型は最初から検査が無い型 |

#### fallback-fabricates-missing-link

| 項目 | 内容 |
|---|---|
| 一般形 | 対応が取れなかったときに位置・順番・既定値で代替物を埋め、代替であることが後段に伝わらないため無根拠が根拠として扱われる |
| 典型的な座標 | 処理=logic / 接続=meaning（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection` / `invariant_audit`（出所の正直さ）/ `trace_walk` |
| 典型的な解決観点 | `fail_closed`（埋めない）+ `explicit_contract`（欠落を状態として持つ） |
| 見分け方 | 代替値が下流で「根拠あり」「該当あり」と解釈されるか。`information-dropped-as-unrepresentable` は落ちるだけ、本型は偽の対応が積まれる |

#### untrusted-input-reaches-instruction

| 項目 | 内容 |
|---|---|
| 一般形 | 第三者由来のテキストが、隔離されずに指示・問い合わせ・パスの位置に混入する |
| 典型的な座標 | 構造=representation / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `adversarial_review` / `inventory`（経路の全列挙）/ `trace_walk` |
| 典型的な解決観点 | `explicit_contract`（隔離区画・固定注記）+ `guardrail_fix` |
| 見分け方 | 資料由来文字列がそのまま文字列補間されている位置があるか |

### derivative-and-wiring

| 項目 | 内容 |
|---|---|
| 族の名 | 派生物と配線 |
| 一般形 | 前段の更新が派生物に届かない、または素材・装置が呼び出し側に配線されず到達不能 |
| 主に効く軸 | `connection.information` / `connection.version` |
| 含む型 | `stale-derivative-served` / `available-but-unwired` / `last-mile-missing` / `context-lost-across-execution-boundary` |

#### stale-derivative-served

| 項目 | 内容 |
|---|---|
| 一般形 | 前段（原稿・設定・解析）が更新されたのに、派生物（キャッシュ・投影・音声・索引）が古いまま配信される |
| 典型的な座標 | 構造=aggregation+responsibility / 接続=version（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `boundary_walk`（設定保存×生成チェーン / 再実行×キャッシュ無効化）/ `symptom_report` |
| 典型的な解決観点 | `carry_through`（無効化を更新経路に同乗）/ `canonical_source`（readiness 判定を 1 箇所に） |
| 見分け方 | 更新の入口ごとに無効化を書いているか。1 つでも漏れると本型 |

#### available-but-unwired

| 項目 | 内容 |
|---|---|
| 一般形 | 素材・API・判定は存在するが、それを使うべき呼び出し側に配線されておらず、到達不能のまま |
| 典型的な座標 | 接続=information（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `trace_walk` / `symptom_report` / `inventory` |
| 典型的な解決観点 | `carry_through` / `responsibility_move` |
| 見分け方 | 「作ったのに使われていない」か。新規に作る必要が無いなら本型 |

#### last-mile-missing

| 項目 | 内容 |
|---|---|
| 一般形 | 確定・保存の装置はあるが、そこへ至る生成・入口・導線が無く、利用者が装置に到達できない |
| 典型的な座標 | 構造=representation+responsibility / 接続=information / 統制=assignment（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `repo_pattern` |
| 典型的な発見観点 | `trace_walk`（入口から確定まで歩く）/ `symptom_report` |
| 典型的な解決観点 | `carry_through` / `responsibility_move` |
| 見分け方 | 装置単体のテストは通るが、利用者の操作列で一度も呼ばれない |

#### context-lost-across-execution-boundary

| 項目 | 内容 |
|---|---|
| 一般形 | スレッド・ジェネレータ・プロセス・リクエストの境界で実行コンテキスト（帰属・上書き・ロック）が失われる |
| 典型的な座標 | 構造=responsibility / 接続=condition（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `reproduction` / `trace_walk` / `external_constraint`（実行基盤の制約） |
| 典型的な解決観点 | `carry_through`（値渡し）/ `order_and_budget`（境界の位置を決める） |
| 見分け方 | 同じコードが同期呼び出しでは動き、非同期・ストリームでだけ落ちる |

## 統制

### human-decision-authority

| 項目 | 内容 |
|---|---|
| 族の名 | 確定の担当 |
| 一般形 | 人が確定すべき判断を AI・既定値・代理指標・表示側の推測が代行する |
| 主に効く軸 | `governance.assignment` / `governance.completion` |
| 含む型 | `ai-decides-instead-of-human` / `default-hides-choice` / `completion-defined-by-proxy` / `permission-and-affordance-asymmetric` |

#### ai-decides-instead-of-human

| 項目 | 内容 |
|---|---|
| 一般形 | 人が確定すべき判断（合否・習得・承認・分類）を AI や代理指標が確定し、人の判断が形式化・迂回される |
| 典型的な座標 | 統制=assignment+review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `repo_pattern` |
| 典型的な発見観点 | `invariant_audit`（「AI は候補まで」「沈黙適応しない」原則照合） |
| 典型的な解決観点 | `responsibility_move`（候補提示に留め確定を人へ）+ `state_transition`（candidate 語彙） |
| 見分け方 | AI 出力が `candidate` を経ずに確定状態へ書かれているか。既定で「確定」に倒れているか |

#### default-hides-choice

| 項目 | 内容 |
|---|---|
| 一般形 | 既定値（既定の分野・プリフィル・既定 on）が選択肢を隠し、選ばなかった判断が「選んだ」記録になる |
| 典型的な座標 | 接続=condition / 統制=assignment+review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `invariant_audit`（確定は人の判断を含む、の原則）/ `symptom_report` |
| 典型的な解決観点 | `explicit_contract`（既定を空に・明示スイッチ）/ `fail_closed` |
| 見分け方 | 何も操作しないとどの値が記録されるか。それは判断か |

#### completion-defined-by-proxy

| 項目 | 内容 |
|---|---|
| 一般形 | 完了・習得・準備完了を代理指標（履歴の有無・次が無い・長さ）で判定し、実際の状態と食い違う |
| 典型的な座標 | 統制=completion（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `invariant_audit` / `symptom_report` / `boundary_walk` |
| 典型的な解決観点 | `explicit_contract`（完了の正本状態を持つ）/ `responsibility_move`（判定を人へ） |
| 見分け方 | 判定に使っている量は、判定したい状態と同じものか、それを匂わせるだけか |

#### permission-and-affordance-asymmetric

| 項目 | 内容 |
|---|---|
| 一般形 | 権限の判定と操作の見え方が非対称で、できる操作が隠れる／できない操作が見える |
| 典型的な座標 | 接続=condition / 統制=assignment（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `boundary_walk`（権限×投影）/ `symptom_report` |
| 典型的な解決観点 | `carry_through`（権限フラグを API で返す）+ `canonical_source`（判定を 1 箇所に） |
| 見分け方 | 表示側が権限を独自に推測しているか |

### reexecution-and-deletion

| 項目 | 内容 |
|---|---|
| 族の名 | 再実行と削除 |
| 一般形 | 再実行・削除・全件再適用が過去の人の判断や派生記録を巻き込む |
| 主に効く軸 | `governance.resume` |
| 含む型 | `reexecution-overwrites-human-decision` / `delete-cascade-loses-derived-records` / `replay-conflicts-with-history` |

#### reexecution-overwrites-human-decision

| 項目 | 内容 |
|---|---|
| 一般形 | 再実行・再生成・再解析が、前回の人間の判断（承認・却下・訂正）を上書き・消去・復活させる |
| 典型的な座標 | 構造=representation / 接続=information / 統制=resume+review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `boundary_walk`（再実行×キャッシュ）/ `adversarial_review` / `data_inspection` |
| 典型的な解決観点 | `state_transition`（削除・上書きを supersede に）+ `explicit_contract`（人が触った列の保護） |
| 見分け方 | 再実行の前後で、人が確定した行・列が同じ値のまま残るか |

#### delete-cascade-loses-derived-records

| 項目 | 内容 |
|---|---|
| 一般形 | 行削除が外部キーの連鎖・孤児化で派生記録（痕跡・承認・台帳）を巻き込んで消す |
| 典型的な座標 | 構造=representation / 接続=version / 統制=resume（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `data_inspection`（FK の連鎖を読む）/ `invariant_audit`（「情報を落とさない」） |
| 典型的な解決観点 | `state_transition`（削除を状態遷移に）/ `representation_change`（FK の張り替え） |
| 見分け方 | 削除対象を参照している表を全部列挙できるか。できないなら連鎖は把握されていない |

#### replay-conflicts-with-history

| 項目 | 内容 |
|---|---|
| 一般形 | 全件を毎回再実行する運用のもとで、過去の手続が履歴でもあるため、変更の取り消し方（削除か空撤去かスタブ化か）が決まらない |
| 典型的な座標 | 構造=representation / 統制=resume（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `boundary_walk`（再実行×既存データ）/ `guardrail_failure` |
| 典型的な解決観点 | `state_transition`（巻き戻しを最終状態への遷移として書く）+ `explicit_contract`（冪等性の規約） |
| 見分け方 | 手続を消すと「作って即壊す」往復や再起動時のエラーが起きるか |

### ordering-and-budget

| 項目 | 内容 |
|---|---|
| 族の名 | 順序と予算 |
| 一般形 | ゲートの順序・外部呼び出しの予算が統制されていない |
| 主に効く軸 | `governance.ordering` / `governance.budget` |
| 含む型 | `gate-position-wrong` / `external-budget-exceeded` |

#### gate-position-wrong

| 項目 | 内容 |
|---|---|
| 一般形 | ゲート（予算消費・権限判定・検証）の順序が不適切で、失敗経路でも消費される・弾けるべきものが通る |
| 典型的な座標 | 統制=ordering+review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `adversarial_review` / `trace_walk` / `reproduction` |
| 典型的な解決観点 | `order_and_budget`（ゲートを全 422 経路の後・最初の 1 バイトの前に） |
| 見分け方 | 失敗した往復で予算が減るか。判定前に副作用が起きるか |

#### external-budget-exceeded

| 項目 | 内容 |
|---|---|
| 一般形 | 外部 API・LLM の呼び出し回数・費用が操作あたりの予算を超える、または失敗時の再試行が循環する |
| 典型的な座標 | 構造=responsibility / 統制=budget+resume（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `external_constraint`（レート制限・費用）/ `symptom_report` / `inventory`（呼び出し点の列挙） |
| 典型的な解決観点 | `order_and_budget`（キャッシュ・抑制・1 バッチ相乗り）+ `explicit_contract`（予算を宣言） |
| 見分け方 | 一連の操作で外部を何回叩くか答えられるか |

### review-and-record

| 項目 | 内容 |
|---|---|
| 族の名 | レビューと記録 |
| 一般形 | 検査・記帳・文書更新の手続が経路や参照先を覆っていない |
| 主に効く軸 | `governance.review` |
| 含む型 | `guardrail-does-not-cover-new-path` / `unaudited-write-path` / `doc-drifts-from-code` / `referenced-source-does-not-exist` |

#### guardrail-does-not-cover-new-path

| 項目 | 内容 |
|---|---|
| 一般形 | 再発防止のテスト・検証が新しい経路・新しい表現を覆っておらず、規律が静かに破られる |
| 典型的な座標 | 構造=aggregation / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `adversarial_review` / `inventory`（全経路の列挙と照合）/ `data_inspection` |
| 典型的な解決観点 | `guardrail_fix`（全文走査・双方向網羅・allowlist を理由付きで） |
| 見分け方 | ガードレールが緑のまま規律違反が入ったか |

#### unaudited-write-path

| 項目 | 内容 |
|---|---|
| 一般形 | 状態を変える経路に監査・記帳が無く、誰が・いつ・何を根拠に変えたかを再構成できない |
| 典型的な座標 | 統制=review+completion（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `inventory`（書き込み経路の全列挙）/ `invariant_audit`（帰属必須の原則） |
| 典型的な解決観点 | `explicit_contract`（記帳を経路に同乗）+ `guardrail_fix` |
| 見分け方 | 書き込み経路を列挙し、記帳の呼び出しが無いものがあるか |

#### doc-drifts-from-code

| 項目 | 内容 |
|---|---|
| 一般形 | 文書（索引・正本参照・番号・件数）が実装と乖離し、読者を誤誘導する |
| 典型的な座標 | 構造=aggregation / 統制=completion+review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `doc_code_diff` / `guardrail_failure` |
| 典型的な解決観点 | `doc_correction` + `guardrail_fix`（網羅・リンク実在・時点付き記法） |
| 見分け方 | 乖離の原因が「更新する手続が無い」か「正本が 2 つある」か。後者なら構造の型 |

#### referenced-source-does-not-exist

| 項目 | 内容 |
|---|---|
| 一般形 | 正本と名指しされた対象が実在しないまま参照され続け、根拠を辿れない参照だけが残る |
| 典型的な座標 | 構造=aggregation / 統制=review（エントリの過半が持つ値。none の軸は省略） |
| 一般化レベル | `general` |
| 典型的な発見観点 | `doc_code_diff` / `inventory`（参照名と実体の突合） |
| 典型的な解決観点 | `canonical_source`（再構成して版に入れる）+ `guardrail_fix`（参照先の実在検査）+ `doc_correction` |
| 見分け方 | 参照している側だけ読んで違和感が無いか。無いなら機械照合しないと見つからない |
