# 課題ナレッジ — 想起シート（設計・実装の前に読む 1 枚・機械生成）

[← 課題ナレッジの入口](README.md) ｜ [辞書](dictionary.md) ｜ [解決原理](principles.md) ｜ [索引](index.md)

> **状態:** 生きたリファレンス（機械生成・2026-10-02 新設）。手で編集しない。`backend/.venv/bin/python backend/scripts/issue_knowledge_index.py` が index.md と同時に再生成する。索引と辞書は長く、開きに行かないと読まれなかった（効果検証 2026-10-02）。この 1 枚は**読み込み経路に置く**ための要約で、設計書の pre-mortem 節に型の slug を写すところまでが使い方。

## 1. 変更に含まれる語 → 先に当てる族

| 変更に含まれる語 | 先に当てる族 |
|---|---|
| 再実行・再解析・再生成・削除・全件再適用 | 再実行と削除（`reexecution-and-deletion`） |
| 既定値・プリフィル・自動確定・AI の判定 | 確定の担当（`human-decision-authority`） |
| 段階間の受け渡し・権限・可視性・オプション | 条件と対象の伝達（`condition-and-scope-transfer`） |
| キャッシュ・投影・音声・索引などの派生物 | 派生物と配線（`derivative-and-wiring`） |
| ID・キー・版・名前空間 | 同一性の表現（`identity-representation`） |
| 語彙表・判定・分割規則の新設 | 正本の分裂（`canonical-source-split`） |
| 外部 API・LLM 呼び出し | 順序と予算（`ordering-and-budget`） |
| 画面・応答・経路を 1 つ足す（既存の規則を新しい面にも効かせる） | 派生物と配線（`derivative-and-wiring`） |

## 2. 繰り返し出ている型（月をまたいだ回数の多い順）

設計書の pre-mortem 節には、当たる型の slug をバッククォートで書く（当たらなければ「該当なし」）。「見分け方」が当たるなら「確かめ方」を検証計画に写す。

| 型 | 族 | 見分け方 | 確かめ方 | 原理 | 直近の事例 |
|---|---|---|---|---|---|
| [`available-but-unwired`](dictionary.md#available-but-unwired) | 派生物と配線 | 「作ったのに使われていない」か。新規に作る必要が無いなら本型 | 呼び出し側の操作列を実際に辿り、その素材・判定が呼ばれる（出力・ログに現れる）ことを見る。単体テストの緑は確認にならない | — | [IK-0596](entries/IK-0596-dictionary-and-principles-not-in-reading-path.md), [IK-0588](entries/IK-0588-child-component-context-dead-end.md) |
| [`duplicate-canonical-sources`](dictionary.md#duplicate-canonical-sources) | 正本の分裂 | 「どちらが正しいか」を決めないと直せないなら本型。正本が 1 つで伝達だけ欠けるなら接続の型 | 正本だけを変えて他所が追随すること（逐語ミラーなら一致検査が赤になること）を見る。二重の定義が残っていないかを全文走査する | — | [IK-0565](entries/IK-0565-graph-grounding-duplicate-nodes.md), [IK-0551](entries/IK-0551-inner-module-duplicate-lhs.md) |
| [`id-namespace-conflated`](dictionary.md#id-namespace-conflated) | 同一性の表現 | 同じ引数に 2 種類の ID が渡り得るか。`same-name-different-referents` は人間向けの呼称の衝突、本型はコード内の識別子の衝突 | 2 種類の ID を同じ引数に渡し、片方が型で弾かれるか解決不能として閉じることを見る | — | [IK-0598](entries/IK-0598-teacher-projection-drops-symbol-keys.md), [IK-0595](entries/IK-0595-document-row-resolution-per-path.md) |
| [`guardrail-does-not-cover-new-path`](dictionary.md#guardrail-does-not-cover-new-path) | レビューと記録 | ガードレールが緑のまま規律違反が入ったか | 規律に違反する変更を意図的に入れてガードレールが赤になることを見る（緑のまま通るなら覆っていない）。開発機でスキップされる検査は CI で走ったことを確かめる | [`route-all-surfaces-through-one-point`](principles.md#route-all-surfaces-through-one-point) | [IK-0594](entries/IK-0594-early-return-paths-skip-response-shape.md), [IK-0586](entries/IK-0586-stuck-utterance-routed-outside-shape.md) |
| [`stale-derivative-served`](dictionary.md#stale-derivative-served) | 派生物と配線 | 更新の入口ごとに無効化を書いているか。1 つでも漏れると本型 | 前段を更新する全入口を列挙し、各入口の直後に派生物が無効化または再生成されることを見る | [`sync-by-stable-key-and-protect-human-decisions`](principles.md#sync-by-stable-key-and-protect-human-decisions) | [IK-0488](entries/IK-0488-stale-embeds-kept-in-current-draft.md), [IK-0484](entries/IK-0484-padded-requirements-survive-reworded-key-concepts.md) |
| [`failure-reported-as-success`](dictionary.md#failure-reported-as-success) | 単一処理の不良 | 例外を捕まえた後に成功と同じ戻り値を返している箇所があるか | 失敗を意図的に起こし（依存を落とす・例外を投げる）、戻り値が成功と区別されて利用者まで届くことを見る | — | [IK-0585](entries/IK-0585-descent-ladder-unavailable-without-reason.md), [IK-0568](entries/IK-0568-module-related-without-partner.md) |
| [`external-budget-exceeded`](dictionary.md#external-budget-exceeded) | 順序と予算 | 一連の操作で外部を何回叩くか答えられるか | 一連の操作を通して外部呼び出しの回数を計測で読み、宣言した予算の範囲に収まることを見る | — | [IK-0597](entries/IK-0597-test-library-seed-reaches-live-embedding.md), [IK-0349](entries/IK-0349-uncapped-stage-llm-calls-unrecorded.md) |
| [`full-update-clobbers-unrelated-fields`](dictionary.md#full-update-clobbers-unrelated-fields) | 分割と粒度 | 書き戻す側が知らない列が対象に増えると壊れるか | 書き戻す側が知らない列に値を入れてから操作し、その列が保たれることを前後比較で見る | — | [IK-0444](entries/IK-0444-citation-map-lost-when-client-resends-history.md), [IK-0374](entries/IK-0374-course-content-build-writes-back-stale-data-clobbering-atlas-binding.md) |
| [`doc-drifts-from-code`](dictionary.md#doc-drifts-from-code) | レビューと記録 | 乖離の原因が「更新する手続が無い」か「正本が 2 つある」か。後者なら構造の型 | 文書の参照先・番号・件数を機械照合し、不一致で赤になる検査を置く。手で読み合わせるだけでは再発する | — | [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md) |
| [`information-dropped-as-unrepresentable`](dictionary.md#information-dropped-as-unrepresentable) | 表せない情報 | 削られた情報を後から復元できるか。できないなら本型。受け皿はあるが渡していないなら接続の型 | 上限超え・語彙外・部分結果を意図的に作り、受け皿（unknown / coverage / review_required）に残って後段と表示に届くことを追う | — | [IK-0572](entries/IK-0572-anchor-list-segment-zero.md), [IK-0571](entries/IK-0571-a-layer-english-generated-text.md) |
| [`wording-mismatch`](dictionary.md#wording-mismatch) | 単一処理の不良 | 文言だけ直して終わるか。文言の出所が複数あって食い違うなら構造の型 `duplicate-canonical-sources` | 文言の出所を 1 箇所にしたうえで、表示面（画面・読み上げ・マニュアル）を横に並べて同じ語が出ることを見る | — | [IK-0591](entries/IK-0591-learner-self-confidence-numbers.md), [IK-0587](entries/IK-0587-provenance-caveat-phrase-spliced-mid-sentence.md) |
| [`extent-decided-by-proxy-signal`](dictionary.md#extent-decided-by-proxy-signal) | 単一処理の不良 | 範囲を決めている量は、対象そのものの性質か、対象の隣にあるものの性質か。後者なら本型 | 手がかりと中身が食い違う入力（caption より広い図・並び順と位置がずれた頁・部品だけの画像）を作り、出力の範囲が中身に一致し、中身の無い候補が出ないことを実物で見る | — | [IK-0590](entries/IK-0590-prereq-gate-consumes-affirmative-lead-in-question.md), [IK-0583](entries/IK-0583-topic-material-question-retrieves-nothing.md) |

## 3. 解決原理（当たる型があれば、併用する部品・派生形・非適用を principles.md で読む）

- [`sync-by-stable-key-and-protect-human-decisions`](principles.md#sync-by-stable-key-and-protect-human-decisions) — 再実行や取り込みは前回の成果を消して作り直さず、内容由来の同一性キーで既存の行と突き合わせて同期し、人が確定した列を保護し、成果を指す参照を結び直す（当たる型: `reexecution-overwrites-human-decision`, `delete-cascade-loses-derived-records`, `id-not-stable-across-versions`, `stale-derivative-served`）
- [`enforce-condition-in-query-with-required-argument`](principles.md#enforce-condition-in-query-with-required-argument) — 権限・可視性・対象範囲の条件は呼び出し側の善意に頼らず、必須引数として受けて問い合わせの内側で強制し、判定できないときは閉じる（当たる型: `condition-not-propagated`, `scope-widened-silently`, `entry-scope-mismatch`）
- [`route-all-surfaces-through-one-point`](principles.md#route-all-surfaces-through-one-point) — 同型の是正が複数の面・複数の周にわたって出たら面ごとに塞ぐのをやめ、規則を全経路が必ず通る 1 箇所に置き、新しい面がその 1 箇所を迂回できないことを全経路を走査する検査で固定する。上流の損失なら下流の読み替えではなく上流の入力と条件を変える（当たる型: `rule-enforced-per-surface`, `upstream-loss-masked-downstream`, `guardrail-does-not-cover-new-path`）

## 4. 設計書に書く形

見出しに `pre-mortem` を含む節を置き、表の「型」列に slug を書く。後で起票したエントリの `sources` にその設計書を
書くと、索引 §14.5 が「予告した型」と「実際に出た型」を突き合わせる。実装後のエントリの `resolution.principles` は
`use: consulted`（設計前に読んだ）にする。

```markdown
## N. 設計時の検査（pre-mortem）

| 変更の性質 | 型 | 本書での扱い（採る部品・見送る派生形） |
|---|---|---|
| 再解析で既存の確定が消えないか | `reexecution-overwrites-human-decision` | … |

当たらない族: 該当なし（理由を一行）
```
