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
| [`duplicate-canonical-sources`](dictionary.md#duplicate-canonical-sources) | 正本の分裂 | 「どちらが正しいか」を決めないと直せないなら本型。正本が 1 つで伝達だけ欠けるなら接続の型 | 正本だけを変えて他所が追随すること（逐語ミラーなら一致検査が赤になること）を見る。二重の定義が残っていないかを全文走査する | — | [IK-1123](entries/IK-1123-identity-link-row-double-decision-mark.md), [IK-1034](entries/IK-1034-topic-summary-evidence-listed-twice-under-alias-ids.md) |
| [`stale-derivative-served`](dictionary.md#stale-derivative-served) | 派生物と配線 | 更新の入口ごとに無効化を書いているか。1 つでも漏れると本型 | 前段を更新する全入口を列挙し、各入口の直後に派生物が無効化または再生成されることを見る | [`sync-by-stable-key-and-protect-human-decisions`](principles.md#sync-by-stable-key-and-protect-human-decisions) | [IK-1081](entries/IK-1081-saved-topic-body-keeps-work-notes-from-older-generation-rule.md), [IK-0993](entries/IK-0993-atlas-post-freeze-index-window-misreported.md) |
| [`doc-drifts-from-code`](dictionary.md#doc-drifts-from-code) | レビューと記録 | 乖離の原因が「更新する手続が無い」か「正本が 2 つある」か。後者なら構造の型 | 文書の参照先・番号・件数を機械照合し、不一致で赤になる検査を置く。手で読み合わせるだけでは再発する | — | [IK-0879](entries/IK-0879-learner-lecture-gated-on-audio-manual-says-timer.md), [IK-0654](entries/IK-0654-disclosure-declaration-overpromises-withdrawal.md) |
| [`information-dropped-as-unrepresentable`](dictionary.md#information-dropped-as-unrepresentable) | 表せない情報 | 削られた情報を後から復元できるか。できないなら本型。受け皿はあるが渡していないなら接続の型 | 上限超え・語彙外・部分結果を意図的に作り、受け皿（unknown / coverage / review_required）に残って後段と表示に届くことを追う | — | [IK-1146](entries/IK-1146-structure-anchor-has-no-atlas-place-type.md), [IK-1144](entries/IK-1144-run-in-paragraph-heading-not-used-as-source-section.md) |
| [`available-but-unwired`](dictionary.md#available-but-unwired) | 派生物と配線 | 「作ったのに使われていない」か。新規に作る必要が無いなら本型 | 呼び出し側の操作列を実際に辿り、その素材・判定が呼ばれる（出力・ログに現れる）ことを見る。単体テストの緑は確認にならない | — | [IK-1142](entries/IK-1142-selection-block-lacks-formula-latex-of-selected-passage.md), [IK-1116](entries/IK-1116-source-excerpt-lines-lack-section-heading.md) |
| [`condition-not-propagated`](dictionary.md#condition-not-propagated) | 条件と対象の伝達 | 各段は単体テストで正しい。境界を歩いたときだけ落ちる | 条件が不利な側（権限なし・オプション off）で境界をまたいだ呼び出しを行い、後段が閉じることを見る。単体テストでは見えない | [`enforce-condition-in-query-with-required-argument`](principles.md#enforce-condition-in-query-with-required-argument) | [IK-1145](entries/IK-1145-intent-hint-prefers-stale-atlas-place-over-current-selection.md), [IK-1141](entries/IK-1141-record-question-gets-no-closed-world-constraint-without-ledger-fact.md) |
| [`contract-changed-one-side`](dictionary.md#contract-changed-one-side) | 段階間の契約 | 変更が片側のリポジトリ領域（フロント／API／agent／永続化）に閉じていないか | 前段の出力を後段に実際に流す検査（mock ではなく）を置き、消費者・実行経路が複数なら全経路を流す | — | [IK-0951](entries/IK-0951-topic-titles-raw-tex-across-surfaces.md), [IK-0894](entries/IK-0894-component-context-unresolved-equation-label-raw-tex.md) |
| [`last-mile-missing`](dictionary.md#last-mile-missing) | 派生物と配線 | 装置単体のテストは通るが、利用者の操作列で一度も呼ばれない | 利用者の操作列を入口から確定まで実際に歩き、装置に到達できることを見る（手動操作か E2E） | — | [IK-1121](entries/IK-1121-proposal-completion-without-attribution-or-downstream-reflection.md), [IK-1074](entries/IK-1074-unplotted-assumption-rows-drop-scope-coverage-and-distinguishers.md) |
| [`completion-defined-by-proxy`](dictionary.md#completion-defined-by-proxy) | 確定の担当 | 判定に使っている量は、判定したい状態と同じものか、それを匂わせるだけか | 代理指標だけが立つ状態（履歴はあるが本人の記録は無い等）を作り、完了と判定されないことを見る | — | [IK-1143](entries/IK-1143-grounding-label-course-material-while-answer-disclaims-sources.md), [IK-1061](entries/IK-1061-explanation-citation-delivery-fact-ignores-target-course-materials.md) |
| [`id-namespace-conflated`](dictionary.md#id-namespace-conflated) | 同一性の表現 | 同じ引数に 2 種類の ID が渡り得るか。`same-name-different-referents` は人間向けの呼称の衝突、本型はコード内の識別子の衝突 | 2 種類の ID を同じ引数に渡し、片方が型で弾かれるか解決不能として閉じることを見る | — | [IK-1007](entries/IK-1007-owner-name-disclosure-shows-login-identifier.md), [IK-0944](entries/IK-0944-lecture-slide-formula-position-key-overwrites-id.md) |
| [`failure-reported-as-success`](dictionary.md#failure-reported-as-success) | 単一処理の不良 | 例外を捕まえた後に成功と同じ戻り値を返している箇所があるか | 失敗を意図的に起こし（依存を落とす・例外を投げる）、戻り値が成功と区別されて利用者まで届くことを見る | — | [IK-1154](entries/IK-1154-repeat-endorse-and-cite-not-distinguished.md), [IK-1013](entries/IK-1013-fallback-placeholder-text-consumed-as-content.md) |
| [`guardrail-does-not-cover-new-path`](dictionary.md#guardrail-does-not-cover-new-path) | レビューと記録 | ガードレールが緑のまま規律違反が入ったか | 規律に違反する変更を意図的に入れてガードレールが赤になることを見る（緑のまま通るなら覆っていない）。開発機でスキップされる検査は CI で走ったことを確かめる | [`route-all-surfaces-through-one-point`](principles.md#route-all-surfaces-through-one-point) | [IK-0819](entries/IK-0819-generated-summary-contains-block-id-token.md), [IK-0615](entries/IK-0615-prompt-role-vocabulary-leaks-into-learner-facts.md) |

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
