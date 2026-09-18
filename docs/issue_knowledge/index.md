# 課題ナレッジ — 索引（機械生成）

[← 課題ナレッジの入口](README.md) ｜ [分類体系](taxonomy.md) ｜ [辞書](dictionary.md) ｜ [記入様式](TEMPLATE.md)

> **状態:** 生きたリファレンス（機械生成）。手で編集しない。`backend/.venv/bin/python backend/scripts/issue_knowledge_index.py` で再生成する（`backend/tests/test_issue_knowledge_guardrails.py` が同期を検査する）。

## 0. 概況

| 軸 | 内訳 |
|---|---|
| 群（座標から導出） | 局所 0 / 構造・接続・統制 88 / （未判定） 0 |
| 処理軸 | 値あり 8（延べ）/ none 80 / unknown 0 |
| 構造軸 | 値あり 63（延べ）/ none 30 / unknown 0 |
| 接続軸 | 値あり 50（延べ）/ none 43 / unknown 0 |
| 統制軸 | 値あり 61（延べ）/ none 40 / unknown 0 |
| 値を持つ軸の数 | 0軸 0 / 1軸 18 / 2軸 69 / 3軸 1 / 4軸 0 |
| 状態 | 未解決 9 / 解決済み 73 / 保留 6 / 不成立 0 |
| 分類の確定状態 | 候補 88 / 確定 0 |
| 原因が仮説 | 2 |
| 一般化 | 固有 0 / リポジトリ内の型 24 / 一般 64 |

## 1. 全エントリ

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0001](entries/IK-0001-llm-decides-check-pass.md) | 確認問題の合否を LLM が返し、そのままトピック完了として永続化される | 構造・接続・統制 | — | — | — | assignment+completion | 確定 | 候補 | 解決済み | `ai-decides-instead-of-human` | invariant_audit, trace_walk | responsibility_move, explicit_contract |
| [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md) | 再解析が知識オブジェクトを物理削除し、連鎖で学習者と教員の記録まで消える | 構造・接続・統制 | — | representation | version | resume | 確定 | 候補 | 解決済み | `delete-cascade-loses-derived-records` | trace_walk, invariant_audit | representation_change, state_transition |
| [IK-0003](entries/IK-0003-lecture-silent-adaptation.md) | レクチャーが履歴から「習得済み」を推定し、告知なく内容を落とす | 構造・接続・統制 | — | — | information | assignment | 確定 | 候補 | 解決済み | `ai-decides-instead-of-human` | invariant_audit, trace_walk | responsibility_move, carry_through |
| [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md) | 前提知識の習得を「質問した履歴がある」で判定し、説明にも出所が付かない | 構造・接続・統制 | — | — | information | completion | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | invariant_audit, trace_walk | first_class_state, carry_through |
| [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md) | 誤解メモを AI が即座に確定して書き、本人は撤回できず、古いものは黙って消える | 構造・接続・統制 | — | representation | — | assignment+review | 確定 | 候補 | 解決済み | `ai-decides-instead-of-human` | invariant_audit, inventory | state_transition, responsibility_move |
| [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md) | 誤解検出が決まった語の出現に依存する一方、生成側にはその語を避けるよう指示している | 構造・接続・統制 | logic | — | contract | — | 確定 | 候補 | 未解決 | `contract-changed-one-side` | doc_code_diff, trace_walk | pending |
| [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md) | 承認の操作が解析時の警告を空で上書きし、見た証拠も見なかった証拠も残らない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | adversarial_review, invariant_audit | representation_change, carry_through |
| [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md) | 確認の理由欄に既定文が入り、根拠を見た事実をサーバが定数で断言する | 構造・接続・統制 | — | — | — | review+assignment | 確定 | 候補 | 解決済み | `default-hides-choice` | adversarial_review, invariant_audit | fail_closed, representation_change |
| [IK-0009](entries/IK-0009-rejection-is-second-class.md) | 却下・撤回が理由も帰属も残さず、主張の側には却下する口すら無い | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 未解決 | `negative-decision-second-class` | inventory, invariant_audit | pending |
| [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md) | 分野を選ぶ口が入口に無く、既定の分野が全ての解析に当たる | 構造・接続・統制 | — | — | condition | assignment | 確定 | 候補 | 解決済み | `default-hides-choice` | invariant_audit, boundary_walk | responsibility_move, fail_closed |
| [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md) | 分野の指定が後段の正規化へ伝わらず、既定の語彙が概念名を上書きする | 構造・接続・統制 | — | — | condition+meaning | — | 確定 | 候補 | 解決済み | `condition-not-propagated` | trace_walk, data_inspection | carry_through, representation_change |
| [IK-0012](entries/IK-0012-export-gate-by-role-only.md) | 持ち出しの受け口が役割だけで通し、対象ごとの可視性も記帳も見ない | 構造・接続・統制 | — | — | condition | review | 確定 | 候補 | 解決済み | `scope-widened-silently` | boundary_walk, invariant_audit | fail_closed, carry_through |
| [IK-0013](entries/IK-0013-state-changes-without-audit.md) | 教材の物理削除・版の取り込み・原稿の保存が、監査の記帳を伴わずに状態を変える | 構造・接続・統制 | — | — | — | review+ordering | 確定 | 候補 | 解決済み | `unaudited-write-path` | inventory, invariant_audit | explicit_contract, guardrail_fix |
| [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md) | 外部のモデルへ入力が渡る事実が当事者に告げられず、可視性が一軸に畳まれている | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 解決済み | `disclosure-not-declared` | invariant_audit, doc_code_diff | explicit_contract, canonical_source |
| [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md) | 資料由来のテキストを信頼しない境界が宣言されず、経路ごとに扱いが分かれていた | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `guardrail-does-not-cover-new-path` | inventory, guardrail_failure | canonical_source, guardrail_fix |
| [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md) | 設計書の「非スコープ」や索引が、別の層で実装済みの事実に追随しない | 構造・接続・統制 | — | aggregation | — | completion | 確定 | 候補 | 解決済み | `doc-drifts-from-code` | doc_code_diff, inventory | doc_correction, explicit_contract |
| [IK-0017](entries/IK-0017-phase-label-means-four-things.md) | 「Phase 3」のような段階名が設計書ごとに独立し、同じ名前が別物を指す | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 未解決 | `same-name-different-referents` | doc_code_diff, inventory | pending |
| [IK-0018](entries/IK-0018-diff-substring-false-negative.md) | 自由記述の照合が概念名の部分文字列一致で、言い換えを「言及が無い」と断定する | 構造・接続・統制 | logic | — | meaning | — | 確定 | 候補 | 未解決 | `substring-match-false-positive` | invariant_audit, trace_walk | pending |
| [IK-0019](entries/IK-0019-claim-objects-not-persisted.md) | 分解した主張と式由来の主張が行にならず、グラフの根拠が「未解決」になる | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | symptom_report, trace_walk | representation_change, carry_through |
| [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md) | 版のピン留めが教材側の成果物へ伝わらず、読み手は常に最新を読む | 構造・接続・統制 | — | — | version+condition | — | 確定 | 候補 | 未解決 | `condition-not-propagated` | boundary_walk, inventory | pending |
| [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md) | 人に関するデータの扱いが機能ごとに決まり、横断の規約が一本も無い | 構造・接続・統制 | — | aggregation | — | assignment | 確定 | 候補 | 保留 | `duplicate-canonical-sources` | inventory, invariant_audit | deferred_decision |
| [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md) | 匿名であることを件数の閾値だけで判定し、小さな集団では匿名にならない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 保留 | `completion-defined-by-proxy` | adversarial_review, external_constraint | deferred_decision |
| [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md) | 入力上限の超過を先頭切り捨てで処理し、捨てた範囲をどこにも報告しない | 構造・接続・統制 | — | — | meaning | budget+completion | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | data_inspection, trace_walk | order_and_budget, canonical_source |
| [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md) | 完全性の判定を件数比較という代理指標で行い「完全」と誤報する | 構造・接続・統制 | — | — | information | completion | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | data_inspection, invariant_audit | explicit_contract, guardrail_fix |
| [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md) | 別名の照合が部分文字列一致で、原本に無い概念が全体に注入される | 構造・接続・統制 | logic | aggregation | — | — | 確定 | 候補 | 解決済み | `substring-match-false-positive` | data_inspection, trace_walk | single_point_fix, canonical_source |
| [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md) | 形の契約が検査されない値が段をまたぎ、文字列が1文字ずつ概念として学習者まで届く | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 解決済み | `unchecked-type-contract-at-boundary` | data_inspection, trace_walk | explicit_contract, representation_change |
| [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md) | 生成ログの JSONB blob が知識の正本で、関係テーブルがその劣化投影になっている | 構造・接続・統制 | — | representation+decomposition | — | — | 確定 | 候補 | 解決済み | `projection-mistaken-for-source` | data_inspection, inventory | canonical_source, representation_change |
| [IK-0106](entries/IK-0106-agent-id-derived-from-position.md) | 知識オブジェクトの識別子が出現順由来で、再解析のたびに同じ ID が別物を指す | 構造・接続・統制 | — | representation | version | — | 確定 | 候補 | 解決済み | `id-not-stable-across-versions` | data_inspection, adversarial_review | representation_change, state_transition |
| [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md) | 内側のスコープでしか一意でない識別子を、外側のキーとして使っている | 構造・接続・統制 | logic | representation | — | — | 確定 | 候補 | 解決済み | `id-unique-only-within-inner-scope` | data_inspection, reproduction | representation_change, single_point_fix |
| [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md) | 再解析が成果を削除して作り直すため、教員の確定がその都度消える | 構造・接続・統制 | — | representation | — | resume+review | 確定 | 候補 | 解決済み | `reexecution-overwrites-human-decision` | boundary_walk, data_inspection | state_transition, explicit_contract |
| [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md) | 再解析での区画の作り直しが外部キーの連鎖で主張の行ごと消す | 構造・接続・統制 | — | representation | — | resume | 確定 | 候補 | 解決済み | `delete-cascade-loses-derived-records` | adversarial_review, reproduction | representation_change, state_transition |
| [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md) | 束の取り込みの置き換え指定が、教員が確定した行まで表示対象から外す | 構造・接続・統制 | — | — | information | review+resume | 確定 | 候補 | 解決済み | `reexecution-overwrites-human-decision` | adversarial_review, invariant_audit | state_transition, explicit_contract |
| [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md) | 事前確認と確定が同じ入力である保証が無く、一括確定の記帳も無かった | 構造・接続・統制 | — | — | — | ordering+review | 確定 | 候補 | 解決済み | `gate-position-wrong` | adversarial_review, invariant_audit | required_argument, explicit_contract |
| [IK-0112](entries/IK-0112-document-reference-without-integrity.md) | 文書への参照が型も制約も持たず、掃除の責務が二重で孤児が滞留する | 構造・接続・統制 | — | responsibility+representation | — | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | data_inspection, inventory | representation_change, canonical_source |
| [IK-0113](entries/IK-0113-learning-unit-undefined.md) | 「学ぶ単位」が定義されておらず、教材と成果を題名の重なりで結んでいた | 構造・接続・統制 | — | representation+decomposition | target | — | 確定 | 候補 | 解決済み | `unit-of-work-undefined` | trace_walk, data_inspection | first_class_state, explicit_contract |
| [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md) | 接続できなかった箇所の出典を位置で代入し、根拠の強さまで底上げしていた | 構造・接続・統制 | logic | — | meaning | — | 確定 | 候補 | 解決済み | `fallback-fabricates-missing-link` | data_inspection, invariant_audit | fail_closed, explicit_contract |
| [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md) | 承認の弁を通らない経路だけが実際に機能し、承認ゼロのまま学習者へ届く | 構造・接続・統制 | — | — | — | review+completion | 仮説 | 候補 | 保留 | `unaudited-write-path` | data_inspection, invariant_audit | deferred_decision |
| [IK-0116](entries/IK-0116-human-gate-without-candidates.md) | 人の確定を要する仕組みに候補を供給する経路が無く、受け皿が空のまま死んでいた | 構造・接続・統制 | — | representation | — | assignment | 確定 | 候補 | 解決済み | `last-mile-missing` | inventory, data_inspection | responsibility_move, state_transition |
| [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md) | 供給側が下書きのまま凍結せず、凍結版だけを読む消費側から構造的に見えない | 構造・接続・統制 | — | — | contract+version | — | 確定 | 候補 | 解決済み | `available-but-unwired` | data_inspection, trace_walk | explicit_contract, carry_through |
| [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md) | 分野マップの節点識別子が版ごとに総取り替えになり、確定済みの位置づけが切れる | 構造・接続・統制 | — | representation | version | — | 確定 | 候補 | 解決済み | `id-not-stable-across-versions` | data_inspection, boundary_walk | representation_change, carry_through |
| [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md) | 新しく足した候補表・文脈組み立て・記帳の経路に可視性と編集権限のゲートが付かない | 構造・接続・統制 | — | — | condition | review | 確定 | 候補 | 解決済み | `condition-not-propagated` | adversarial_review, boundary_walk | carry_through, fail_closed |
| [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md) | 規律を守るガードレールが動的な表名と新しい経路を覆わず、静かに破られていた | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `guardrail-does-not-cover-new-path` | adversarial_review, inventory | canonical_source, guardrail_fix |
| [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md) | 「昇格は人間の操作のみ」の条項を、パイプラインが候補行を作る形に読み替えてよいか | 構造・接続・統制 | — | — | — | assignment+review | 確定 | 候補 | 保留 | `ai-decides-instead-of-human` | invariant_audit, adversarial_review | deferred_decision |
| [IK-0122](entries/IK-0122-persistence-split-across-transactions.md) | 知識の永続化が複数のトランザクションに分かれ、中途半端な世代が残り得る | 構造・接続・統制 | — | decomposition | — | resume | 確定 | 候補 | 未解決 | `unit-of-work-undefined` | adversarial_review, trace_walk | pending |
| [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md) | 公開手段の契約をサーバ側だけ差し替え、呼び出し側と案内が旧契約のまま残って学習者がコースに到達できない | 構造・接続・統制 | — | — | contract | completion | 確定 | 候補 | 解決済み | `contract-changed-one-side` | doc_code_diff, inventory | canonical_source, explicit_contract |
| [IK-0203](entries/IK-0203-guidance-registry-drift.md) | 案内機構が実在しない操作を案内し続け、増築された機能には追随しない | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `doc-drifts-from-code` | doc_code_diff, invariant_audit | explicit_contract, guardrail_fix |
| [IK-0204](entries/IK-0204-layers-built-but-unwired.md) | 層ごと作られた処理が画面に配線されず、誰も呼ばないまま完成として扱われる | 構造・接続・統制 | — | — | information | completion | 確定 | 候補 | 解決済み | `available-but-unwired` | inventory, doc_code_diff | carry_through, responsibility_move |
| [IK-0205](entries/IK-0205-decision-ui-without-candidates.md) | 確定する画面はあるのに候補を生む経路と入口が無く、人が確定できないまま止まる | 構造・接続・統制 | — | responsibility | information | — | 確定 | 候補 | 解決済み | `last-mile-missing` | trace_walk, invariant_audit | carry_through, responsibility_move |
| [IK-0206](entries/IK-0206-fail-closed-silence.md) | 候補なし・未処理・権限なし・障害が同じ非表示に畳まれ、空と壊れが区別できない | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | invariant_audit, symptom_report | representation_change, explicit_contract |
| [IK-0207](entries/IK-0207-partial-save-full-replace.md) | 一部だけ編集した保存が設定全体を書き戻し、触っていない項目を既定値で上書きする | 構造・接続・統制 | — | responsibility | contract | — | 確定 | 候補 | 解決済み | `full-update-clobbers-unrelated-fields` | boundary_walk | representation_change, explicit_contract |
| [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md) | 一括の作り直しが派生キャッシュを無効化せず、古い読み上げが実質永続する | 構造・接続・統制 | — | aggregation | version | — | 確定 | 候補 | 解決済み | `stale-derivative-served` | boundary_walk | carry_through, canonical_source |
| [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md) | 実行オプションが前回の実行から継承されず、既定オフの再解析が未レビューの成果を消す | 構造・接続・統制 | — | — | condition+version | — | 確定 | 候補 | 解決済み | `condition-not-propagated` | boundary_walk, adversarial_review | carry_through, explicit_contract |
| [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md) | 回答の出所表示と信頼度表示が別々の入力から導かれ、同じ回答に矛盾が同時に出る | 構造・接続・統制 | — | — | condition+information | — | 確定 | 候補 | 解決済み | `condition-not-propagated` | boundary_walk, symptom_report | carry_through, doc_correction |
| [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md) | 取り消せない操作の確認が経路ごとに非対称で、無確認のまま全教材の再抽出が始まる | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `destructive-action-without-confirmation` | inventory, invariant_audit | canonical_source, guardrail_fix |
| [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md) | 検査が名前の列挙と文字列の存在確認に依るため、後から増えた実装と実行時の不整合を覆えない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 解決済み | `guardrail-does-not-cover-new-path` | guardrail_failure, adversarial_review | explicit_contract, guardrail_fix |
| [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md) | 権限の判定結果が応答に無く、画面が過剰な隠蔽と過小な隠蔽に分かれる | 構造・接続・統制 | — | — | condition | assignment | 確定 | 候補 | 解決済み | `permission-and-affordance-asymmetric` | inventory, invariant_audit | carry_through, fail_closed |
| [IK-0214](entries/IK-0214-course-completion-by-proxy.md) | コースの完了を到達位置の代理指標で断定し、完了状態の正本がどこにも残らない | 構造・接続・統制 | — | representation | — | completion | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | adversarial_review, trace_walk | first_class_state, representation_change |
| [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md) | コース全体の入口が教材ひとつだけを対象にし、件数と一覧の母数も食い違う | 構造・接続・統制 | — | aggregation | target | — | 確定 | 候補 | 解決済み | `entry-scope-mismatch` | adversarial_review, trace_walk | carry_through, canonical_source |
| [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md) | 同じひとつのステージの設定を二つの入口が指し、優先関係も保存の寿命も画面に現れない | 構造・接続・統制 | — | aggregation+representation | — | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | symptom_report, doc_code_diff | canonical_source, representation_change |
| [IK-0217](entries/IK-0217-entry-points-proliferate.md) | 操作と案内の入口が機能追加のたびに横並びで増え、総量を誰も見ていない | 構造・接続・統制 | — | decomposition | — | review | 確定 | 候補 | 未解決 | `entry-point-proliferation` | symptom_report, inventory | pending |
| [IK-0218](entries/IK-0218-element-rendering-split.md) | 同じ要素を描く実装が系統ごとに分かれ、種別の呼び名と辿れるかどうかが画面で食い違う | 構造・接続・統制 | — | aggregation+responsibility | — | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | symptom_report, inventory | canonical_source, responsibility_move |
| [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md) | 正本と名指しされた文書がリポジトリに存在しないまま、番号付きで参照され続ける | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `referenced-source-does-not-exist` | doc_code_diff, inventory | canonical_source, guardrail_fix |
| [IK-0220](entries/IK-0220-count-values-diverge.md) | 同じ事実の件数が複数の文書へ書き写され、版ごとに分裂する | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | doc_code_diff, data_inspection | canonical_source, guardrail_fix |
| [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md) | 設計書だけに追記の運用があり、機能解説と索引には無いため、大型機能が文書から丸ごと欠ける | 構造・接続・統制 | — | — | — | review+completion | 確定 | 候補 | 解決済み | `doc-drifts-from-code` | doc_code_diff, inventory | explicit_contract, guardrail_fix |
| [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md) | 同型のワークフローと判定が層ごとに再実装され、片方だけ更新されて食い違う | 構造・接続・統制 | — | aggregation | meaning | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | inventory, doc_code_diff | canonical_source, guardrail_fix |
| [IK-0223](entries/IK-0223-call-budget-counted-in-process.md) | 呼び出し回数の上限がプロセス内の数え上げで、多重化すると統制が効かなくなる | 構造・接続・統制 | — | responsibility | — | budget | 仮説 | 候補 | 保留 | `external-budget-exceeded` | inventory, external_constraint | deferred_decision |
| [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md) | コンポーネント承認を「読み出し→全体書き戻し」で行い、CHECK 違反と出所情報の破壊を招いた | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 解決済み | `full-update-clobbers-unrelated-fields` | adversarial_review, trace_walk | representation_change, guardrail_fix |
| [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md) | 解析時に焼き込まれた審査状態が現在の判断に勝ち、承認してもレビューが閉じない | 構造・接続・統制 | — | responsibility | version | — | 確定 | 候補 | 解決済み | `stale-derivative-served` | reproduction, adversarial_review | carry_through, explicit_contract |
| [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md) | グラフ内で採番した識別子とデータベース行の識別子を同一視し、500・422・操作不能を招いた | 構造・接続・統制 | — | representation | target | — | 確定 | 候補 | 解決済み | `id-namespace-conflated` | symptom_report, trace_walk | explicit_contract, fail_closed |
| [IK-0304](entries/IK-0304-derived-claims-not-persisted.md) | 生成された主張のうち一部だけが永続化され、グラフの根拠が常に「未解決」になる | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | symptom_report, trace_walk | carry_through, fail_closed |
| [IK-0305](entries/IK-0305-hedged-every-sentence-response.md) | 不確かさを文ごとの留保で表す契約にしたため、応答が読めず聞けないものになった | 構造・接続・統制 | wording | representation | — | — | 確定 | 候補 | 解決済み | `wording-mismatch` | symptom_report, invariant_audit | representation_change, canonical_source |
| [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md) | グラフの段を分野語彙と固定の深さ上限から決めたため、ノードが一直線に並び構造が読めない | 構造・接続・統制 | logic | representation | — | — | 確定 | 候補 | 解決済み | `domain-vocabulary-hardcoded` | symptom_report, invariant_audit | representation_change, guardrail_fix |
| [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md) | サーバが立てた縮退の事実文が画面に描かれず、0 件が「近い論文が無い」と読める | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `available-but-unwired` | symptom_report, trace_walk | carry_through, first_class_state |
| [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md) | 外部 API を操作のたびに引き直し、制限を受けたあとも人の操作で窓が延び続ける | 構造・接続・統制 | — | — | — | budget+resume | 確定 | 候補 | 解決済み | `external-budget-exceeded` | external_constraint, data_inspection | order_and_budget, representation_change |
| [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md) | 画面が見せている構造成果と選択箇所が、AI への入力に一度も入っていなかった | 構造・接続・統制 | — | responsibility | information | — | 確定 | 候補 | 解決済み | `available-but-unwired` | boundary_walk, inventory | carry_through, explicit_contract |
| [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md) | 実行文脈がジェネレータのスレッド跨ぎで失われ、帰属とモデル上書きが効かなくなる | 構造・接続・統制 | — | responsibility | condition | — | 確定 | 候補 | 解決済み | `context-lost-across-execution-boundary` | boundary_walk, doc_code_diff | required_argument, carry_through |
| [IK-0311](entries/IK-0311-tests-pin-function-body-text.md) | テストが関数本体の字面を検査対象にしているため、正しい再分割ができない | 構造・接続・統制 | — | responsibility | — | review | 確定 | 候補 | 解決済み | `test-pins-implementation-text` | guardrail_failure, inventory | responsibility_move, explicit_contract |
| [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md) | 会話の調子を利用者に選ばせる設計のまま、テキスト側にその入口が用意されていなかった | 構造・接続・統制 | — | responsibility | condition | — | 確定 | 候補 | 解決済み | `available-but-unwired` | inventory, doc_code_diff | responsibility_move, explicit_contract |
| [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md) | 比較の条件が違う場面に同じ類似度の閾値を流用し、機能が一度も発火しなかった | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 解決済み | `threshold-reused-across-regimes` | data_inspection, symptom_report | representation_change, canonical_source |
| [IK-0314](entries/IK-0314-canonical-source-split.md) | 同じ事実の正本が複数あり、片方だけが更新されて食い違う | 構造・接続・統制 | — | aggregation | version | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | inventory, doc_code_diff | canonical_source, guardrail_fix |
| [IK-0315](entries/IK-0315-replay-runner-vs-history.md) | 毎起動の全件再実行方式では、過去の適用の意味と最終状態を両立できない | 構造・接続・統制 | — | representation | — | resume | 確定 | 候補 | 解決済み | `replay-conflicts-with-history` | boundary_walk, guardrail_failure | state_transition, explicit_contract |
| [IK-0316](entries/IK-0316-write-path-checks-role-only.md) | 書き込み経路が役割だけを見て、対象の所有条件を確かめていなかった | 構造・接続・統制 | — | — | condition | assignment | 確定 | 候補 | 解決済み | `condition-not-propagated` | inventory, boundary_walk | fail_closed, canonical_source |
| [IK-0317](entries/IK-0317-silent-save-failure.md) | 保存の失敗を握りつぶして成功を返し、利用者には別の症状として現れる | 構造・接続・統制 | resource | — | — | completion | 確定 | 候補 | 解決済み | `failure-reported-as-success` | inventory, trace_walk | first_class_state, single_point_fix |
| [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md) | 許容されない種別の候補注釈が黙って捨てられ、生成側にも利用者にも伝わらない | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 未解決 | `information-dropped-as-unrepresentable` | inventory, trace_walk | pending |
| [IK-0319](entries/IK-0319-no-immutable-conversation-log.md) | 対話本文の正本が上書き・削除で消え、後から何が起きたかを再構成できない | 構造・接続・統制 | — | representation | — | completion | 確定 | 候補 | 保留 | `unaudited-write-path` | inventory, invariant_audit | deferred_decision |
| [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md) | 第三者が書いた資料本文が、指示と区別されないままプロンプトへ流れ込む | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 未解決 | `untrusted-input-reaches-instruction` | inventory, invariant_audit | explicit_contract, pending |
| [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md) | 表示と読み上げが別のコンテンツと別の分割単位を使い、同期が近似にしかならない | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | symptom_report, boundary_walk | representation_change, canonical_source |
| [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md) | 前提が揃わないまま一括生成を始められ、飛ばされた分があっても完了として扱われる | 構造・接続・統制 | — | — | — | completion+ordering | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | boundary_walk, symptom_report | canonical_source, fail_closed |

## 2. 軸別（座標の各軸の値ごと）

各軸は独立に読む。1 つの課題は複数の軸に値を持ち得る（排他ではない）。

### 処理軸（`processing`）

- `processing.input_handling`: （該当なし）
- `processing.logic`: [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md), [IK-0018](entries/IK-0018-diff-substring-false-negative.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md)
- `processing.resource`: [IK-0317](entries/IK-0317-silent-save-failure.md)
- `processing.wording`: [IK-0305](entries/IK-0305-hedged-every-sentence-response.md)
- `processing.regression`: （該当なし）

- `none`: 80 件
- `unknown`: 0 件

### 構造軸（`structure`）

- `structure.representation`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0017](entries/IK-0017-phase-label-means-four-things.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0305](entries/IK-0305-hedged-every-sentence-response.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md), [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md)
- `structure.responsibility`: [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0223](entries/IK-0223-call-budget-counted-in-process.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md)
- `structure.decomposition`: [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md)
- `structure.aggregation`: [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0314](entries/IK-0314-canonical-source-split.md)

- `none`: 30 件
- `unknown`: 0 件

### 接続軸（`connection`）

- `connection.information`: [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md)
- `connection.meaning`: [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0017](entries/IK-0017-phase-label-means-four-things.md), [IK-0018](entries/IK-0018-diff-substring-false-negative.md), [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md)
- `connection.condition`: [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md)
- `connection.target`: [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md)
- `connection.version`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md), [IK-0314](entries/IK-0314-canonical-source-split.md)
- `connection.contract`: [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md)

- `none`: 43 件
- `unknown`: 0 件

### 統制軸（`governance`）

**制御系（実行時）**

- `governance.ordering`: [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `governance.budget`: [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0223](entries/IK-0223-call-budget-counted-in-process.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md)
- `governance.resume`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md)

**手続系（人）**

- `governance.assignment`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md)
- `governance.review`: [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `governance.completion`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md), [IK-0317](entries/IK-0317-silent-save-failure.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)

- `none`: 40 件
- `unknown`: 0 件

### 軸の対の頻度（同じ課題が 2 軸に値を持つ組）

| 軸 A | 軸 B | 件数 |
|---|---|---|
| 構造 | 統制 | 26 |
| 構造 | 接続 | 25 |
| 接続 | 統制 | 13 |
| 処理 | 構造 | 4 |
| 処理 | 接続 | 3 |
| 処理 | 統制 | 1 |

### unknown の軸を持つエントリ（まだ見ていない軸。確定レビューで none か値に倒す）

（該当なし）

## 3. 族・型別（dictionary.md の見出し）

型の成立 = 分類が確定したエントリ 2 件以上。それ未満は**暫定**（族への畳み込み候補）。

### 族 `single-processing-fault` → [辞書](dictionary.md#single-processing-fault)

#### `substring-match-false-positive`（暫定）

→ [辞書の定義](dictionary.md#substring-match-false-positive)

実際の座標: 処理=logic / 接続=meaning（1） ／ 処理=logic / 構造=aggregation（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0018](entries/IK-0018-diff-substring-false-negative.md) | 自由記述の照合が概念名の部分文字列一致で、言い換えを「言及が無い」と断定する | 構造・接続・統制 | logic | — | meaning | — | 確定 | 候補 | 未解決 | `substring-match-false-positive` | invariant_audit, trace_walk | pending |
| [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md) | 別名の照合が部分文字列一致で、原本に無い概念が全体に注入される | 構造・接続・統制 | logic | aggregation | — | — | 確定 | 候補 | 解決済み | `substring-match-false-positive` | data_inspection, trace_walk | single_point_fix, canonical_source |

#### `failure-reported-as-success`（暫定）

→ [辞書の定義](dictionary.md#failure-reported-as-success)

実際の座標: 処理=resource / 統制=completion（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0317](entries/IK-0317-silent-save-failure.md) | 保存の失敗を握りつぶして成功を返し、利用者には別の症状として現れる | 構造・接続・統制 | resource | — | — | completion | 確定 | 候補 | 解決済み | `failure-reported-as-success` | inventory, trace_walk | first_class_state, single_point_fix |

#### `wording-mismatch`（暫定）

→ [辞書の定義](dictionary.md#wording-mismatch)

実際の座標: 処理=wording / 構造=representation（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0305](entries/IK-0305-hedged-every-sentence-response.md) | 不確かさを文ごとの留保で表す契約にしたため、応答が読めず聞けないものになった | 構造・接続・統制 | wording | representation | — | — | 確定 | 候補 | 解決済み | `wording-mismatch` | symptom_report, invariant_audit | representation_change, canonical_source |

### 族 `identity-representation` → [辞書](dictionary.md#identity-representation)

#### `id-not-stable-across-versions`（暫定）

→ [辞書の定義](dictionary.md#id-not-stable-across-versions)

実際の座標: 構造=representation / 接続=version（2）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0106](entries/IK-0106-agent-id-derived-from-position.md) | 知識オブジェクトの識別子が出現順由来で、再解析のたびに同じ ID が別物を指す | 構造・接続・統制 | — | representation | version | — | 確定 | 候補 | 解決済み | `id-not-stable-across-versions` | data_inspection, adversarial_review | representation_change, state_transition |
| [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md) | 分野マップの節点識別子が版ごとに総取り替えになり、確定済みの位置づけが切れる | 構造・接続・統制 | — | representation | version | — | 確定 | 候補 | 解決済み | `id-not-stable-across-versions` | data_inspection, boundary_walk | representation_change, carry_through |

#### `id-unique-only-within-inner-scope`（暫定）

→ [辞書の定義](dictionary.md#id-unique-only-within-inner-scope)

実際の座標: 処理=logic / 構造=representation（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md) | 内側のスコープでしか一意でない識別子を、外側のキーとして使っている | 構造・接続・統制 | logic | representation | — | — | 確定 | 候補 | 解決済み | `id-unique-only-within-inner-scope` | data_inspection, reproduction | representation_change, single_point_fix |

#### `id-namespace-conflated`（暫定）

→ [辞書の定義](dictionary.md#id-namespace-conflated)

実際の座標: 構造=representation / 接続=target（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md) | グラフ内で採番した識別子とデータベース行の識別子を同一視し、500・422・操作不能を招いた | 構造・接続・統制 | — | representation | target | — | 確定 | 候補 | 解決済み | `id-namespace-conflated` | symptom_report, trace_walk | explicit_contract, fail_closed |

#### `same-name-different-referents`（暫定）

→ [辞書の定義](dictionary.md#same-name-different-referents)

実際の座標: 構造=representation / 接続=meaning（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0017](entries/IK-0017-phase-label-means-four-things.md) | 「Phase 3」のような段階名が設計書ごとに独立し、同じ名前が別物を指す | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 未解決 | `same-name-different-referents` | doc_code_diff, inventory | pending |

#### `unit-of-work-undefined`（暫定）

→ [辞書の定義](dictionary.md#unit-of-work-undefined)

実際の座標: 構造=representation+decomposition / 接続=target（1） ／ 構造=decomposition / 統制=resume（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0113](entries/IK-0113-learning-unit-undefined.md) | 「学ぶ単位」が定義されておらず、教材と成果を題名の重なりで結んでいた | 構造・接続・統制 | — | representation+decomposition | target | — | 確定 | 候補 | 解決済み | `unit-of-work-undefined` | trace_walk, data_inspection | first_class_state, explicit_contract |
| [IK-0122](entries/IK-0122-persistence-split-across-transactions.md) | 知識の永続化が複数のトランザクションに分かれ、中途半端な世代が残り得る | 構造・接続・統制 | — | decomposition | — | resume | 確定 | 候補 | 未解決 | `unit-of-work-undefined` | adversarial_review, trace_walk | pending |

### 族 `canonical-source-split` → [辞書](dictionary.md#canonical-source-split)

#### `duplicate-canonical-sources`（暫定）

→ [辞書の定義](dictionary.md#duplicate-canonical-sources)

実際の座標: 構造=aggregation / 統制=assignment（1） ／ 構造=responsibility+representation（1） ／ 構造=aggregation+representation（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md) | 人に関するデータの扱いが機能ごとに決まり、横断の規約が一本も無い | 構造・接続・統制 | — | aggregation | — | assignment | 確定 | 候補 | 保留 | `duplicate-canonical-sources` | inventory, invariant_audit | deferred_decision |
| [IK-0112](entries/IK-0112-document-reference-without-integrity.md) | 文書への参照が型も制約も持たず、掃除の責務が二重で孤児が滞留する | 構造・接続・統制 | — | responsibility+representation | — | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | data_inspection, inventory | representation_change, canonical_source |
| [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md) | 同じひとつのステージの設定を二つの入口が指し、優先関係も保存の寿命も画面に現れない | 構造・接続・統制 | — | aggregation+representation | — | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | symptom_report, doc_code_diff | canonical_source, representation_change |
| [IK-0218](entries/IK-0218-element-rendering-split.md) | 同じ要素を描く実装が系統ごとに分かれ、種別の呼び名と辿れるかどうかが画面で食い違う | 構造・接続・統制 | — | aggregation+responsibility | — | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | symptom_report, inventory | canonical_source, responsibility_move |
| [IK-0220](entries/IK-0220-count-values-diverge.md) | 同じ事実の件数が複数の文書へ書き写され、版ごとに分裂する | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | doc_code_diff, data_inspection | canonical_source, guardrail_fix |
| [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md) | 同型のワークフローと判定が層ごとに再実装され、片方だけ更新されて食い違う | 構造・接続・統制 | — | aggregation | meaning | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | inventory, doc_code_diff | canonical_source, guardrail_fix |
| [IK-0314](entries/IK-0314-canonical-source-split.md) | 同じ事実の正本が複数あり、片方だけが更新されて食い違う | 構造・接続・統制 | — | aggregation | version | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | inventory, doc_code_diff | canonical_source, guardrail_fix |
| [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md) | 表示と読み上げが別のコンテンツと別の分割単位を使い、同期が近似にしかならない | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 解決済み | `duplicate-canonical-sources` | symptom_report, boundary_walk | representation_change, canonical_source |

#### `projection-mistaken-for-source`（暫定）

→ [辞書の定義](dictionary.md#projection-mistaken-for-source)

実際の座標: 構造=representation+decomposition（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md) | 生成ログの JSONB blob が知識の正本で、関係テーブルがその劣化投影になっている | 構造・接続・統制 | — | representation+decomposition | — | — | 確定 | 候補 | 解決済み | `projection-mistaken-for-source` | data_inspection, inventory | canonical_source, representation_change |

#### `destructive-action-without-confirmation`（暫定）

→ [辞書の定義](dictionary.md#destructive-action-without-confirmation)

実際の座標: 構造=aggregation / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md) | 取り消せない操作の確認が経路ごとに非対称で、無確認のまま全教材の再抽出が始まる | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `destructive-action-without-confirmation` | inventory, invariant_audit | canonical_source, guardrail_fix |

### 族 `missing-representation` → [辞書](dictionary.md#missing-representation)

#### `information-dropped-as-unrepresentable`（暫定）

→ [辞書の定義](dictionary.md#information-dropped-as-unrepresentable)

実際の座標: 構造=representation / 接続=information（3） ／ 構造=representation / 統制=review（1） ／ 接続=meaning / 統制=budget+completion（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md) | 承認の操作が解析時の警告を空で上書きし、見た証拠も見なかった証拠も残らない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | adversarial_review, invariant_audit | representation_change, carry_through |
| [IK-0019](entries/IK-0019-claim-objects-not-persisted.md) | 分解した主張と式由来の主張が行にならず、グラフの根拠が「未解決」になる | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | symptom_report, trace_walk | representation_change, carry_through |
| [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md) | 入力上限の超過を先頭切り捨てで処理し、捨てた範囲をどこにも報告しない | 構造・接続・統制 | — | — | meaning | budget+completion | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | data_inspection, trace_walk | order_and_budget, canonical_source |
| [IK-0206](entries/IK-0206-fail-closed-silence.md) | 候補なし・未処理・権限なし・障害が同じ非表示に畳まれ、空と壊れが区別できない | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | invariant_audit, symptom_report | representation_change, explicit_contract |
| [IK-0304](entries/IK-0304-derived-claims-not-persisted.md) | 生成された主張のうち一部だけが永続化され、グラフの根拠が常に「未解決」になる | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `information-dropped-as-unrepresentable` | symptom_report, trace_walk | carry_through, fail_closed |
| [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md) | 許容されない種別の候補注釈が黙って捨てられ、生成側にも利用者にも伝わらない | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 未解決 | `information-dropped-as-unrepresentable` | inventory, trace_walk | pending |

#### `negative-decision-second-class`（暫定）

→ [辞書の定義](dictionary.md#negative-decision-second-class)

実際の座標: 構造=representation / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0009](entries/IK-0009-rejection-is-second-class.md) | 却下・撤回が理由も帰属も残さず、主張の側には却下する口すら無い | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 未解決 | `negative-decision-second-class` | inventory, invariant_audit | pending |

#### `disclosure-not-declared`（暫定）

→ [辞書の定義](dictionary.md#disclosure-not-declared)

実際の座標: 構造=representation / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md) | 外部のモデルへ入力が渡る事実が当事者に告げられず、可視性が一軸に畳まれている | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 解決済み | `disclosure-not-declared` | invariant_audit, doc_code_diff | explicit_contract, canonical_source |

#### `threshold-reused-across-regimes`（暫定）

→ [辞書の定義](dictionary.md#threshold-reused-across-regimes)

実際の座標: 構造=representation / 接続=meaning（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md) | 比較の条件が違う場面に同じ類似度の閾値を流用し、機能が一度も発火しなかった | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 解決済み | `threshold-reused-across-regimes` | data_inspection, symptom_report | representation_change, canonical_source |

#### `domain-vocabulary-hardcoded`（暫定）

→ [辞書の定義](dictionary.md#domain-vocabulary-hardcoded)

実際の座標: 処理=logic / 構造=representation（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md) | グラフの段を分野語彙と固定の深さ上限から決めたため、ノードが一直線に並び構造が読めない | 構造・接続・統制 | logic | representation | — | — | 確定 | 候補 | 解決済み | `domain-vocabulary-hardcoded` | symptom_report, invariant_audit | representation_change, guardrail_fix |

### 族 `boundary-and-granularity` → [辞書](dictionary.md#boundary-and-granularity)

#### `full-update-clobbers-unrelated-fields`（暫定）

→ [辞書の定義](dictionary.md#full-update-clobbers-unrelated-fields)

実際の座標: 構造=responsibility / 接続=contract（1） ／ 構造=representation / 接続=contract（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0207](entries/IK-0207-partial-save-full-replace.md) | 一部だけ編集した保存が設定全体を書き戻し、触っていない項目を既定値で上書きする | 構造・接続・統制 | — | responsibility | contract | — | 確定 | 候補 | 解決済み | `full-update-clobbers-unrelated-fields` | boundary_walk | representation_change, explicit_contract |
| [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md) | コンポーネント承認を「読み出し→全体書き戻し」で行い、CHECK 違反と出所情報の破壊を招いた | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 解決済み | `full-update-clobbers-unrelated-fields` | adversarial_review, trace_walk | representation_change, guardrail_fix |

#### `entry-point-proliferation`（暫定）

→ [辞書の定義](dictionary.md#entry-point-proliferation)

実際の座標: 構造=decomposition / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0217](entries/IK-0217-entry-points-proliferate.md) | 操作と案内の入口が機能追加のたびに横並びで増え、総量を誰も見ていない | 構造・接続・統制 | — | decomposition | — | review | 確定 | 候補 | 未解決 | `entry-point-proliferation` | symptom_report, inventory | pending |

#### `test-pins-implementation-text`（暫定）

→ [辞書の定義](dictionary.md#test-pins-implementation-text)

実際の座標: 構造=responsibility / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0311](entries/IK-0311-tests-pin-function-body-text.md) | テストが関数本体の字面を検査対象にしているため、正しい再分割ができない | 構造・接続・統制 | — | responsibility | — | review | 確定 | 候補 | 解決済み | `test-pins-implementation-text` | guardrail_failure, inventory | responsibility_move, explicit_contract |

### 族 `condition-and-scope-transfer` → [辞書](dictionary.md#condition-and-scope-transfer)

#### `condition-not-propagated`（暫定）

→ [辞書の定義](dictionary.md#condition-not-propagated)

実際の座標: 接続=condition+meaning（1） ／ 接続=version+condition（1） ／ 接続=condition / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md) | 分野の指定が後段の正規化へ伝わらず、既定の語彙が概念名を上書きする | 構造・接続・統制 | — | — | condition+meaning | — | 確定 | 候補 | 解決済み | `condition-not-propagated` | trace_walk, data_inspection | carry_through, representation_change |
| [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md) | 版のピン留めが教材側の成果物へ伝わらず、読み手は常に最新を読む | 構造・接続・統制 | — | — | version+condition | — | 確定 | 候補 | 未解決 | `condition-not-propagated` | boundary_walk, inventory | pending |
| [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md) | 新しく足した候補表・文脈組み立て・記帳の経路に可視性と編集権限のゲートが付かない | 構造・接続・統制 | — | — | condition | review | 確定 | 候補 | 解決済み | `condition-not-propagated` | adversarial_review, boundary_walk | carry_through, fail_closed |
| [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md) | 実行オプションが前回の実行から継承されず、既定オフの再解析が未レビューの成果を消す | 構造・接続・統制 | — | — | condition+version | — | 確定 | 候補 | 解決済み | `condition-not-propagated` | boundary_walk, adversarial_review | carry_through, explicit_contract |
| [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md) | 回答の出所表示と信頼度表示が別々の入力から導かれ、同じ回答に矛盾が同時に出る | 構造・接続・統制 | — | — | condition+information | — | 確定 | 候補 | 解決済み | `condition-not-propagated` | boundary_walk, symptom_report | carry_through, doc_correction |
| [IK-0316](entries/IK-0316-write-path-checks-role-only.md) | 書き込み経路が役割だけを見て、対象の所有条件を確かめていなかった | 構造・接続・統制 | — | — | condition | assignment | 確定 | 候補 | 解決済み | `condition-not-propagated` | inventory, boundary_walk | fail_closed, canonical_source |

#### `scope-widened-silently`（暫定）

→ [辞書の定義](dictionary.md#scope-widened-silently)

実際の座標: 接続=condition / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0012](entries/IK-0012-export-gate-by-role-only.md) | 持ち出しの受け口が役割だけで通し、対象ごとの可視性も記帳も見ない | 構造・接続・統制 | — | — | condition | review | 確定 | 候補 | 解決済み | `scope-widened-silently` | boundary_walk, invariant_audit | fail_closed, carry_through |

#### `entry-scope-mismatch`（暫定）

→ [辞書の定義](dictionary.md#entry-scope-mismatch)

実際の座標: 構造=aggregation / 接続=target（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md) | コース全体の入口が教材ひとつだけを対象にし、件数と一覧の母数も食い違う | 構造・接続・統制 | — | aggregation | target | — | 確定 | 候補 | 解決済み | `entry-scope-mismatch` | adversarial_review, trace_walk | carry_through, canonical_source |

### 族 `contract-between-stages` → [辞書](dictionary.md#contract-between-stages)

#### `contract-changed-one-side`（暫定）

→ [辞書の定義](dictionary.md#contract-changed-one-side)

実際の座標: 処理=logic / 接続=contract（1） ／ 接続=contract / 統制=completion（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md) | 誤解検出が決まった語の出現に依存する一方、生成側にはその語を避けるよう指示している | 構造・接続・統制 | logic | — | contract | — | 確定 | 候補 | 未解決 | `contract-changed-one-side` | doc_code_diff, trace_walk | pending |
| [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md) | 公開手段の契約をサーバ側だけ差し替え、呼び出し側と案内が旧契約のまま残って学習者がコースに到達できない | 構造・接続・統制 | — | — | contract | completion | 確定 | 候補 | 解決済み | `contract-changed-one-side` | doc_code_diff, inventory | canonical_source, explicit_contract |

#### `unchecked-type-contract-at-boundary`（暫定）

→ [辞書の定義](dictionary.md#unchecked-type-contract-at-boundary)

実際の座標: 構造=representation / 接続=contract（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md) | 形の契約が検査されない値が段をまたぎ、文字列が1文字ずつ概念として学習者まで届く | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 解決済み | `unchecked-type-contract-at-boundary` | data_inspection, trace_walk | explicit_contract, representation_change |

#### `fallback-fabricates-missing-link`（暫定）

→ [辞書の定義](dictionary.md#fallback-fabricates-missing-link)

実際の座標: 処理=logic / 接続=meaning（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md) | 接続できなかった箇所の出典を位置で代入し、根拠の強さまで底上げしていた | 構造・接続・統制 | logic | — | meaning | — | 確定 | 候補 | 解決済み | `fallback-fabricates-missing-link` | data_inspection, invariant_audit | fail_closed, explicit_contract |

#### `untrusted-input-reaches-instruction`（暫定）

→ [辞書の定義](dictionary.md#untrusted-input-reaches-instruction)

実際の座標: 構造=representation / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md) | 第三者が書いた資料本文が、指示と区別されないままプロンプトへ流れ込む | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 未解決 | `untrusted-input-reaches-instruction` | inventory, invariant_audit | explicit_contract, pending |

### 族 `derivative-and-wiring` → [辞書](dictionary.md#derivative-and-wiring)

#### `stale-derivative-served`（暫定）

→ [辞書の定義](dictionary.md#stale-derivative-served)

実際の座標: 構造=aggregation / 接続=version（1） ／ 構造=responsibility / 接続=version（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md) | 一括の作り直しが派生キャッシュを無効化せず、古い読み上げが実質永続する | 構造・接続・統制 | — | aggregation | version | — | 確定 | 候補 | 解決済み | `stale-derivative-served` | boundary_walk | carry_through, canonical_source |
| [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md) | 解析時に焼き込まれた審査状態が現在の判断に勝ち、承認してもレビューが閉じない | 構造・接続・統制 | — | responsibility | version | — | 確定 | 候補 | 解決済み | `stale-derivative-served` | reproduction, adversarial_review | carry_through, explicit_contract |

#### `available-but-unwired`（暫定）

→ [辞書の定義](dictionary.md#available-but-unwired)

実際の座標: 接続=contract+version（1） ／ 接続=information / 統制=completion（1） ／ 構造=representation / 接続=information（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md) | 供給側が下書きのまま凍結せず、凍結版だけを読む消費側から構造的に見えない | 構造・接続・統制 | — | — | contract+version | — | 確定 | 候補 | 解決済み | `available-but-unwired` | data_inspection, trace_walk | explicit_contract, carry_through |
| [IK-0204](entries/IK-0204-layers-built-but-unwired.md) | 層ごと作られた処理が画面に配線されず、誰も呼ばないまま完成として扱われる | 構造・接続・統制 | — | — | information | completion | 確定 | 候補 | 解決済み | `available-but-unwired` | inventory, doc_code_diff | carry_through, responsibility_move |
| [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md) | サーバが立てた縮退の事実文が画面に描かれず、0 件が「近い論文が無い」と読める | 構造・接続・統制 | — | representation | information | — | 確定 | 候補 | 解決済み | `available-but-unwired` | symptom_report, trace_walk | carry_through, first_class_state |
| [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md) | 画面が見せている構造成果と選択箇所が、AI への入力に一度も入っていなかった | 構造・接続・統制 | — | responsibility | information | — | 確定 | 候補 | 解決済み | `available-but-unwired` | boundary_walk, inventory | carry_through, explicit_contract |
| [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md) | 会話の調子を利用者に選ばせる設計のまま、テキスト側にその入口が用意されていなかった | 構造・接続・統制 | — | responsibility | condition | — | 確定 | 候補 | 解決済み | `available-but-unwired` | inventory, doc_code_diff | responsibility_move, explicit_contract |

#### `last-mile-missing`（暫定）

→ [辞書の定義](dictionary.md#last-mile-missing)

実際の座標: 構造=representation / 統制=assignment（1） ／ 構造=responsibility / 接続=information（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0116](entries/IK-0116-human-gate-without-candidates.md) | 人の確定を要する仕組みに候補を供給する経路が無く、受け皿が空のまま死んでいた | 構造・接続・統制 | — | representation | — | assignment | 確定 | 候補 | 解決済み | `last-mile-missing` | inventory, data_inspection | responsibility_move, state_transition |
| [IK-0205](entries/IK-0205-decision-ui-without-candidates.md) | 確定する画面はあるのに候補を生む経路と入口が無く、人が確定できないまま止まる | 構造・接続・統制 | — | responsibility | information | — | 確定 | 候補 | 解決済み | `last-mile-missing` | trace_walk, invariant_audit | carry_through, responsibility_move |

#### `context-lost-across-execution-boundary`（暫定）

→ [辞書の定義](dictionary.md#context-lost-across-execution-boundary)

実際の座標: 構造=responsibility / 接続=condition（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md) | 実行文脈がジェネレータのスレッド跨ぎで失われ、帰属とモデル上書きが効かなくなる | 構造・接続・統制 | — | responsibility | condition | — | 確定 | 候補 | 解決済み | `context-lost-across-execution-boundary` | boundary_walk, doc_code_diff | required_argument, carry_through |

### 族 `human-decision-authority` → [辞書](dictionary.md#human-decision-authority)

#### `ai-decides-instead-of-human`（暫定）

→ [辞書の定義](dictionary.md#ai-decides-instead-of-human)

実際の座標: 統制=assignment+completion（1） ／ 接続=information / 統制=assignment（1） ／ 構造=representation / 統制=assignment+review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0001](entries/IK-0001-llm-decides-check-pass.md) | 確認問題の合否を LLM が返し、そのままトピック完了として永続化される | 構造・接続・統制 | — | — | — | assignment+completion | 確定 | 候補 | 解決済み | `ai-decides-instead-of-human` | invariant_audit, trace_walk | responsibility_move, explicit_contract |
| [IK-0003](entries/IK-0003-lecture-silent-adaptation.md) | レクチャーが履歴から「習得済み」を推定し、告知なく内容を落とす | 構造・接続・統制 | — | — | information | assignment | 確定 | 候補 | 解決済み | `ai-decides-instead-of-human` | invariant_audit, trace_walk | responsibility_move, carry_through |
| [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md) | 誤解メモを AI が即座に確定して書き、本人は撤回できず、古いものは黙って消える | 構造・接続・統制 | — | representation | — | assignment+review | 確定 | 候補 | 解決済み | `ai-decides-instead-of-human` | invariant_audit, inventory | state_transition, responsibility_move |
| [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md) | 「昇格は人間の操作のみ」の条項を、パイプラインが候補行を作る形に読み替えてよいか | 構造・接続・統制 | — | — | — | assignment+review | 確定 | 候補 | 保留 | `ai-decides-instead-of-human` | invariant_audit, adversarial_review | deferred_decision |

#### `default-hides-choice`（暫定）

→ [辞書の定義](dictionary.md#default-hides-choice)

実際の座標: 統制=review+assignment（1） ／ 接続=condition / 統制=assignment（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md) | 確認の理由欄に既定文が入り、根拠を見た事実をサーバが定数で断言する | 構造・接続・統制 | — | — | — | review+assignment | 確定 | 候補 | 解決済み | `default-hides-choice` | adversarial_review, invariant_audit | fail_closed, representation_change |
| [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md) | 分野を選ぶ口が入口に無く、既定の分野が全ての解析に当たる | 構造・接続・統制 | — | — | condition | assignment | 確定 | 候補 | 解決済み | `default-hides-choice` | invariant_audit, boundary_walk | responsibility_move, fail_closed |

#### `completion-defined-by-proxy`（暫定）

→ [辞書の定義](dictionary.md#completion-defined-by-proxy)

実際の座標: 接続=information / 統制=completion（2） ／ 構造=representation / 統制=review（1） ／ 構造=representation / 統制=completion（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md) | 前提知識の習得を「質問した履歴がある」で判定し、説明にも出所が付かない | 構造・接続・統制 | — | — | information | completion | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | invariant_audit, trace_walk | first_class_state, carry_through |
| [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md) | 匿名であることを件数の閾値だけで判定し、小さな集団では匿名にならない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 保留 | `completion-defined-by-proxy` | adversarial_review, external_constraint | deferred_decision |
| [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md) | 完全性の判定を件数比較という代理指標で行い「完全」と誤報する | 構造・接続・統制 | — | — | information | completion | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | data_inspection, invariant_audit | explicit_contract, guardrail_fix |
| [IK-0214](entries/IK-0214-course-completion-by-proxy.md) | コースの完了を到達位置の代理指標で断定し、完了状態の正本がどこにも残らない | 構造・接続・統制 | — | representation | — | completion | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | adversarial_review, trace_walk | first_class_state, representation_change |
| [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md) | 前提が揃わないまま一括生成を始められ、飛ばされた分があっても完了として扱われる | 構造・接続・統制 | — | — | — | completion+ordering | 確定 | 候補 | 解決済み | `completion-defined-by-proxy` | boundary_walk, symptom_report | canonical_source, fail_closed |

#### `permission-and-affordance-asymmetric`（暫定）

→ [辞書の定義](dictionary.md#permission-and-affordance-asymmetric)

実際の座標: 接続=condition / 統制=assignment（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md) | 権限の判定結果が応答に無く、画面が過剰な隠蔽と過小な隠蔽に分かれる | 構造・接続・統制 | — | — | condition | assignment | 確定 | 候補 | 解決済み | `permission-and-affordance-asymmetric` | inventory, invariant_audit | carry_through, fail_closed |

### 族 `reexecution-and-deletion` → [辞書](dictionary.md#reexecution-and-deletion)

#### `reexecution-overwrites-human-decision`（暫定）

→ [辞書の定義](dictionary.md#reexecution-overwrites-human-decision)

実際の座標: 構造=representation / 統制=resume+review（1） ／ 接続=information / 統制=review+resume（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md) | 再解析が成果を削除して作り直すため、教員の確定がその都度消える | 構造・接続・統制 | — | representation | — | resume+review | 確定 | 候補 | 解決済み | `reexecution-overwrites-human-decision` | boundary_walk, data_inspection | state_transition, explicit_contract |
| [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md) | 束の取り込みの置き換え指定が、教員が確定した行まで表示対象から外す | 構造・接続・統制 | — | — | information | review+resume | 確定 | 候補 | 解決済み | `reexecution-overwrites-human-decision` | adversarial_review, invariant_audit | state_transition, explicit_contract |

#### `delete-cascade-loses-derived-records`（暫定）

→ [辞書の定義](dictionary.md#delete-cascade-loses-derived-records)

実際の座標: 構造=representation / 接続=version / 統制=resume（1） ／ 構造=representation / 統制=resume（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md) | 再解析が知識オブジェクトを物理削除し、連鎖で学習者と教員の記録まで消える | 構造・接続・統制 | — | representation | version | resume | 確定 | 候補 | 解決済み | `delete-cascade-loses-derived-records` | trace_walk, invariant_audit | representation_change, state_transition |
| [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md) | 再解析での区画の作り直しが外部キーの連鎖で主張の行ごと消す | 構造・接続・統制 | — | representation | — | resume | 確定 | 候補 | 解決済み | `delete-cascade-loses-derived-records` | adversarial_review, reproduction | representation_change, state_transition |

#### `replay-conflicts-with-history`（暫定）

→ [辞書の定義](dictionary.md#replay-conflicts-with-history)

実際の座標: 構造=representation / 統制=resume（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0315](entries/IK-0315-replay-runner-vs-history.md) | 毎起動の全件再実行方式では、過去の適用の意味と最終状態を両立できない | 構造・接続・統制 | — | representation | — | resume | 確定 | 候補 | 解決済み | `replay-conflicts-with-history` | boundary_walk, guardrail_failure | state_transition, explicit_contract |

### 族 `ordering-and-budget` → [辞書](dictionary.md#ordering-and-budget)

#### `gate-position-wrong`（暫定）

→ [辞書の定義](dictionary.md#gate-position-wrong)

実際の座標: 統制=ordering+review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md) | 事前確認と確定が同じ入力である保証が無く、一括確定の記帳も無かった | 構造・接続・統制 | — | — | — | ordering+review | 確定 | 候補 | 解決済み | `gate-position-wrong` | adversarial_review, invariant_audit | required_argument, explicit_contract |

#### `external-budget-exceeded`（暫定）

→ [辞書の定義](dictionary.md#external-budget-exceeded)

実際の座標: 構造=responsibility / 統制=budget（1） ／ 統制=budget+resume（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0223](entries/IK-0223-call-budget-counted-in-process.md) | 呼び出し回数の上限がプロセス内の数え上げで、多重化すると統制が効かなくなる | 構造・接続・統制 | — | responsibility | — | budget | 仮説 | 候補 | 保留 | `external-budget-exceeded` | inventory, external_constraint | deferred_decision |
| [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md) | 外部 API を操作のたびに引き直し、制限を受けたあとも人の操作で窓が延び続ける | 構造・接続・統制 | — | — | — | budget+resume | 確定 | 候補 | 解決済み | `external-budget-exceeded` | external_constraint, data_inspection | order_and_budget, representation_change |

### 族 `review-and-record` → [辞書](dictionary.md#review-and-record)

#### `guardrail-does-not-cover-new-path`（暫定）

→ [辞書の定義](dictionary.md#guardrail-does-not-cover-new-path)

実際の座標: 構造=aggregation / 統制=review（2） ／ 構造=representation / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md) | 資料由来のテキストを信頼しない境界が宣言されず、経路ごとに扱いが分かれていた | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `guardrail-does-not-cover-new-path` | inventory, guardrail_failure | canonical_source, guardrail_fix |
| [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md) | 規律を守るガードレールが動的な表名と新しい経路を覆わず、静かに破られていた | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `guardrail-does-not-cover-new-path` | adversarial_review, inventory | canonical_source, guardrail_fix |
| [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md) | 検査が名前の列挙と文字列の存在確認に依るため、後から増えた実装と実行時の不整合を覆えない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 解決済み | `guardrail-does-not-cover-new-path` | guardrail_failure, adversarial_review | explicit_contract, guardrail_fix |

#### `unaudited-write-path`（暫定）

→ [辞書の定義](dictionary.md#unaudited-write-path)

実際の座標: 統制=review+ordering（1） ／ 統制=review+completion（1） ／ 構造=representation / 統制=completion（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0013](entries/IK-0013-state-changes-without-audit.md) | 教材の物理削除・版の取り込み・原稿の保存が、監査の記帳を伴わずに状態を変える | 構造・接続・統制 | — | — | — | review+ordering | 確定 | 候補 | 解決済み | `unaudited-write-path` | inventory, invariant_audit | explicit_contract, guardrail_fix |
| [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md) | 承認の弁を通らない経路だけが実際に機能し、承認ゼロのまま学習者へ届く | 構造・接続・統制 | — | — | — | review+completion | 仮説 | 候補 | 保留 | `unaudited-write-path` | data_inspection, invariant_audit | deferred_decision |
| [IK-0319](entries/IK-0319-no-immutable-conversation-log.md) | 対話本文の正本が上書き・削除で消え、後から何が起きたかを再構成できない | 構造・接続・統制 | — | representation | — | completion | 確定 | 候補 | 保留 | `unaudited-write-path` | inventory, invariant_audit | deferred_decision |

#### `doc-drifts-from-code`（暫定）

→ [辞書の定義](dictionary.md#doc-drifts-from-code)

実際の座標: 構造=aggregation / 統制=completion（1） ／ 構造=aggregation / 統制=review（1） ／ 統制=review+completion（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md) | 設計書の「非スコープ」や索引が、別の層で実装済みの事実に追随しない | 構造・接続・統制 | — | aggregation | — | completion | 確定 | 候補 | 解決済み | `doc-drifts-from-code` | doc_code_diff, inventory | doc_correction, explicit_contract |
| [IK-0203](entries/IK-0203-guidance-registry-drift.md) | 案内機構が実在しない操作を案内し続け、増築された機能には追随しない | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `doc-drifts-from-code` | doc_code_diff, invariant_audit | explicit_contract, guardrail_fix |
| [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md) | 設計書だけに追記の運用があり、機能解説と索引には無いため、大型機能が文書から丸ごと欠ける | 構造・接続・統制 | — | — | — | review+completion | 確定 | 候補 | 解決済み | `doc-drifts-from-code` | doc_code_diff, inventory | explicit_contract, guardrail_fix |

#### `referenced-source-does-not-exist`（暫定）

→ [辞書の定義](dictionary.md#referenced-source-does-not-exist)

実際の座標: 構造=aggregation / 統制=review（1）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md) | 正本と名指しされた文書がリポジトリに存在しないまま、番号付きで参照され続ける | 構造・接続・統制 | — | aggregation | — | review | 確定 | 候補 | 解決済み | `referenced-source-does-not-exist` | doc_code_diff, inventory | canonical_source, guardrail_fix |

## 4. 発見観点別

- `symptom_report`: [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0305](entries/IK-0305-hedged-every-sentence-response.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `invariant_audit`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0018](entries/IK-0018-diff-substring-false-negative.md), [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0305](entries/IK-0305-hedged-every-sentence-response.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `boundary_walk`: [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `doc_code_diff`: [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0017](entries/IK-0017-phase-label-means-four-things.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md), [IK-0314](entries/IK-0314-canonical-source-split.md)
- `data_inspection`: [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md)
- `trace_walk`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md), [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0018](entries/IK-0018-diff-substring-false-negative.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0317](entries/IK-0317-silent-save-failure.md), [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md)
- `adversarial_review`: [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md)
- `inventory`: [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0017](entries/IK-0017-phase-label-means-four-things.md), [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md), [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0223](entries/IK-0223-call-budget-counted-in-process.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md), [IK-0314](entries/IK-0314-canonical-source-split.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md), [IK-0317](entries/IK-0317-silent-save-failure.md), [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `guardrail_failure`: [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md)
- `reproduction`: [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md)
- `external_constraint`: [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0223](entries/IK-0223-call-budget-counted-in-process.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md)

## 5. 解決観点別

- `single_point_fix`: [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0317](entries/IK-0317-silent-save-failure.md)
- `canonical_source`: [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0305](entries/IK-0305-hedged-every-sentence-response.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md), [IK-0314](entries/IK-0314-canonical-source-split.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `explicit_contract`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `required_argument`: [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md)
- `vocabulary_table`: （該当なし）
- `first_class_state`: [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0317](entries/IK-0317-silent-save-failure.md)
- `representation_change`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0305](entries/IK-0305-hedged-every-sentence-response.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md)
- `responsibility_move`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md)
- `carry_through`: [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md)
- `fail_closed`: [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `state_transition`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md)
- `order_and_budget`: [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md)
- `guardrail_fix`: [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0314](entries/IK-0314-canonical-source-split.md)
- `doc_correction`: [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md)
- `deferred_decision`: [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md), [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md), [IK-0223](entries/IK-0223-call-budget-counted-in-process.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md)
- `pending`: [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0017](entries/IK-0017-phase-label-means-four-things.md), [IK-0018](entries/IK-0018-diff-substring-false-negative.md), [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md), [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)

## 6. 層・モジュール別（場所の記録。分類の根拠ではない）

- `account_lifecycle`: [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md)
- `admin_copilot`: [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md)
- `auth_visibility`: [IK-0012](entries/IK-0012-export-gate-by-role-only.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md)
- `cartridges`: [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md)
- `concept_registry`: [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md)
- `course_builder`: [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0114](entries/IK-0114-positional-fallback-presented-as-evidence.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0317](entries/IK-0317-silent-save-failure.md)
- `decision_context`: [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md)
- `deliberation_w`: [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `disclosure_axes`: [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md)
- `discuss`: [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md)
- `docs`: [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md), [IK-0017](entries/IK-0017-phase-label-means-four-things.md), [IK-0219](entries/IK-0219-canonical-doc-not-in-repo.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md)
- `doubt_d`: [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md)
- `endorsement_c`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md)
- `export_bundle`: [IK-0012](entries/IK-0012-export-gate-by-role-only.md)
- `field_atlas_s`: [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md)
- `frontend_admin_ui`: [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `frontend_learning_ui`: [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md)
- `graph_review`: [IK-0007](entries/IK-0007-approval-erases-analysis-warnings.md), [IK-0009](entries/IK-0009-rejection-is-second-class.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md)
- `guidance_g`: [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md), [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md), [IK-0203](entries/IK-0203-guidance-registry-drift.md), [IK-0217](entries/IK-0217-entry-points-proliferate.md)
- `help_kb`: [IK-0203](entries/IK-0203-guidance-registry-drift.md)
- `image_library_l`: [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0205](entries/IK-0205-decision-ui-without-candidates.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md)
- `indicator_catalog`: [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md)
- `knowledge_landscape`: [IK-0118](entries/IK-0118-atlas-node-id-replaced-every-version.md)
- `knowledge_objects`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md)
- `knowledge_transfer`: [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0110](entries/IK-0110-import-replace-supersedes-approved-rows.md), [IK-0111](entries/IK-0111-dryrun-and-apply-not-same-payload.md)
- `learner_experience_b`: [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md), [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0214](entries/IK-0214-course-completion-by-proxy.md)
- `learning_units`: [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0119](entries/IK-0119-visibility-gate-missing-on-new-paths.md)
- `lecture_player`: [IK-0003](entries/IK-0003-lecture-silent-adaptation.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `lecture_studio`: [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0207](entries/IK-0207-partial-save-full-replace.md), [IK-0208](entries/IK-0208-bulk-regeneration-leaves-stale-audio.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md), [IK-0218](entries/IK-0218-element-rendering-split.md), [IK-0306](entries/IK-0306-layout-levels-from-vocabulary.md), [IK-0316](entries/IK-0316-write-path-checks-role-only.md), [IK-0322](entries/IK-0322-audio-task-completes-with-skipped-slides.md)
- `llm_streaming`: [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md)
- `migrations_db`: [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0314](entries/IK-0314-canonical-source-split.md), [IK-0315](entries/IK-0315-replay-runner-vs-history.md)
- `model_selection_m`: [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md)
- `paper_discovery`: [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md)
- `paper_radar`: [IK-0307](entries/IK-0307-degraded-notice-not-rendered.md), [IK-0308](entries/IK-0308-arxiv-call-budget-and-429-loop.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md)
- `personal_network`: [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0206](entries/IK-0206-fail-closed-silence.md)
- `pipeline_a`: [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md), [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md), [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md), [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0019](entries/IK-0019-claim-objects-not-persisted.md), [IK-0101](entries/IK-0101-input-cap-truncates-head-silently.md), [IK-0102](entries/IK-0102-completeness-gate-by-count-proxy.md), [IK-0103](entries/IK-0103-alias-substring-match-injects-concept.md), [IK-0104](entries/IK-0104-untyped-value-crosses-stage-boundary.md), [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md), [IK-0106](entries/IK-0106-agent-id-derived-from-position.md), [IK-0107](entries/IK-0107-id-unique-only-within-inner-scope.md), [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md), [IK-0113](entries/IK-0113-learning-unit-undefined.md), [IK-0116](entries/IK-0116-human-gate-without-candidates.md), [IK-0117](entries/IK-0117-seed-not-frozen-invisible-to-pipeline.md), [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md), [IK-0122](entries/IK-0122-persistence-split-across-transactions.md), [IK-0209](entries/IK-0209-option-not-inherited-across-runs.md), [IK-0216](entries/IK-0216-vision-model-setting-duplicated.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `rag_chat`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0004](entries/IK-0004-prerequisite-mastery-by-contact.md), [IK-0005](entries/IK-0005-misconception-memo-ai-confirmed.md), [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md), [IK-0014](entries/IK-0014-external-llm-transfer-undeclared.md), [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0210](entries/IK-0210-grounding-and-tier-disagree.md), [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md), [IK-0312](entries/IK-0312-casual-mode-has-no-text-entry.md), [IK-0319](entries/IK-0319-no-immutable-conversation-log.md), [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md)
- `reconstruction_r`: [IK-0001](entries/IK-0001-llm-decides-check-pass.md), [IK-0018](entries/IK-0018-diff-substring-false-negative.md), [IK-0206](entries/IK-0206-fail-closed-silence.md), [IK-0215](entries/IK-0215-entry-scope-narrower-than-implied.md)
- `release_review`: [IK-0008](entries/IK-0008-consent-evidence-fabricated-by-default.md)
- `screen_adapter_sa`: [IK-0309](entries/IK-0309-structure-not-in-answer-prompt.md)
- `shared_infra`: [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md), [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md), [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md), [IK-0223](entries/IK-0223-call-budget-counted-in-process.md), [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md), [IK-0314](entries/IK-0314-canonical-source-split.md), [IK-0317](entries/IK-0317-silent-save-failure.md)
- `status_notification`: [IK-0314](entries/IK-0314-canonical-source-split.md)
- `teaching_figures`: [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md)
- `tests_guardrails`: [IK-0120](entries/IK-0120-guardrail-blind-to-dynamic-table-names.md), [IK-0212](entries/IK-0212-enumerating-guardrails-miss.md), [IK-0220](entries/IK-0220-count-values-diverge.md), [IK-0311](entries/IK-0311-tests-pin-function-body-text.md)
- `theory_artifacts`: [IK-0301](entries/IK-0301-approval-full-update-clobbers-component.md), [IK-0302](entries/IK-0302-frozen-graph-status-beats-live.md), [IK-0303](entries/IK-0303-graph-native-id-vs-db-uuid.md), [IK-0304](entries/IK-0304-derived-claims-not-persisted.md), [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md)
- `trace_registry`: [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md)
- `tts_voice`: [IK-0305](entries/IK-0305-hedged-every-sentence-response.md), [IK-0321](entries/IK-0321-display-and-audio-use-different-sources.md)
- `usage_metering_u`: [IK-0204](entries/IK-0204-layers-built-but-unwired.md), [IK-0310](entries/IK-0310-contextvar-lost-across-generator.md)
- `vector_anchoring_va`: [IK-0313](entries/IK-0313-threshold-reused-across-regimes.md)
- `versioning_v`: [IK-0013](entries/IK-0013-state-changes-without-audit.md), [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md), [IK-0112](entries/IK-0112-document-reference-without-integrity.md), [IK-0211](entries/IK-0211-destructive-action-without-confirmation.md), [IK-0213](entries/IK-0213-owner-flag-absent-from-response.md)

## 7. 原因が仮説のまま（分類も仮説）

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md) | 承認の弁を通らない経路だけが実際に機能し、承認ゼロのまま学習者へ届く | 構造・接続・統制 | — | — | — | review+completion | 仮説 | 候補 | 保留 | `unaudited-write-path` | data_inspection, invariant_audit | deferred_decision |
| [IK-0223](entries/IK-0223-call-budget-counted-in-process.md) | 呼び出し回数の上限がプロセス内の数え上げで、多重化すると統制が効かなくなる | 構造・接続・統制 | — | responsibility | — | budget | 仮説 | 候補 | 保留 | `external-budget-exceeded` | inventory, external_constraint | deferred_decision |

## 8. 未解決・保留

| ID | 題名 | 群 | 処理 | 構造 | 接続 | 統制 | 原因の確度 | 分類 | 状態 | 型 | 発見観点 | 解決観点 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| [IK-0006](entries/IK-0006-misconception-detection-contradicts-prompt.md) | 誤解検出が決まった語の出現に依存する一方、生成側にはその語を避けるよう指示している | 構造・接続・統制 | logic | — | contract | — | 確定 | 候補 | 未解決 | `contract-changed-one-side` | doc_code_diff, trace_walk | pending |
| [IK-0009](entries/IK-0009-rejection-is-second-class.md) | 却下・撤回が理由も帰属も残さず、主張の側には却下する口すら無い | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 未解決 | `negative-decision-second-class` | inventory, invariant_audit | pending |
| [IK-0017](entries/IK-0017-phase-label-means-four-things.md) | 「Phase 3」のような段階名が設計書ごとに独立し、同じ名前が別物を指す | 構造・接続・統制 | — | representation | meaning | — | 確定 | 候補 | 未解決 | `same-name-different-referents` | doc_code_diff, inventory | pending |
| [IK-0018](entries/IK-0018-diff-substring-false-negative.md) | 自由記述の照合が概念名の部分文字列一致で、言い換えを「言及が無い」と断定する | 構造・接続・統制 | logic | — | meaning | — | 確定 | 候補 | 未解決 | `substring-match-false-positive` | invariant_audit, trace_walk | pending |
| [IK-0020](entries/IK-0020-version-pin-not-reaching-document-assets.md) | 版のピン留めが教材側の成果物へ伝わらず、読み手は常に最新を読む | 構造・接続・統制 | — | — | version+condition | — | 確定 | 候補 | 未解決 | `condition-not-propagated` | boundary_walk, inventory | pending |
| [IK-0021](entries/IK-0021-no-canonical-policy-for-personal-data.md) | 人に関するデータの扱いが機能ごとに決まり、横断の規約が一本も無い | 構造・接続・統制 | — | aggregation | — | assignment | 確定 | 候補 | 保留 | `duplicate-canonical-sources` | inventory, invariant_audit | deferred_decision |
| [IK-0022](entries/IK-0022-k-anonymity-threshold-ignores-group-size.md) | 匿名であることを件数の閾値だけで判定し、小さな集団では匿名にならない | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 保留 | `completion-defined-by-proxy` | adversarial_review, external_constraint | deferred_decision |
| [IK-0115](entries/IK-0115-confirmation-gate-bypassed-by-freeze.md) | 承認の弁を通らない経路だけが実際に機能し、承認ゼロのまま学習者へ届く | 構造・接続・統制 | — | — | — | review+completion | 仮説 | 候補 | 保留 | `unaudited-write-path` | data_inspection, invariant_audit | deferred_decision |
| [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md) | 「昇格は人間の操作のみ」の条項を、パイプラインが候補行を作る形に読み替えてよいか | 構造・接続・統制 | — | — | — | assignment+review | 確定 | 候補 | 保留 | `ai-decides-instead-of-human` | invariant_audit, adversarial_review | deferred_decision |
| [IK-0122](entries/IK-0122-persistence-split-across-transactions.md) | 知識の永続化が複数のトランザクションに分かれ、中途半端な世代が残り得る | 構造・接続・統制 | — | decomposition | — | resume | 確定 | 候補 | 未解決 | `unit-of-work-undefined` | adversarial_review, trace_walk | pending |
| [IK-0217](entries/IK-0217-entry-points-proliferate.md) | 操作と案内の入口が機能追加のたびに横並びで増え、総量を誰も見ていない | 構造・接続・統制 | — | decomposition | — | review | 確定 | 候補 | 未解決 | `entry-point-proliferation` | symptom_report, inventory | pending |
| [IK-0223](entries/IK-0223-call-budget-counted-in-process.md) | 呼び出し回数の上限がプロセス内の数え上げで、多重化すると統制が効かなくなる | 構造・接続・統制 | — | responsibility | — | budget | 仮説 | 候補 | 保留 | `external-budget-exceeded` | inventory, external_constraint | deferred_decision |
| [IK-0318](entries/IK-0318-figure-annotations-silently-discarded.md) | 許容されない種別の候補注釈が黙って捨てられ、生成側にも利用者にも伝わらない | 構造・接続・統制 | — | representation | contract | — | 確定 | 候補 | 未解決 | `information-dropped-as-unrepresentable` | inventory, trace_walk | pending |
| [IK-0319](entries/IK-0319-no-immutable-conversation-log.md) | 対話本文の正本が上書き・削除で消え、後から何が起きたかを再構成できない | 構造・接続・統制 | — | representation | — | completion | 確定 | 候補 | 保留 | `unaudited-write-path` | inventory, invariant_audit | deferred_decision |
| [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md) | 第三者が書いた資料本文が、指示と区別されないままプロンプトへ流れ込む | 構造・接続・統制 | — | representation | — | review | 確定 | 候補 | 未解決 | `untrusted-input-reaches-instruction` | inventory, invariant_audit | explicit_contract, pending |

## 9. 同じ原因の別視点（`view_of` の束）

- 代表 [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md): [IK-0002](entries/IK-0002-reanalysis-physical-delete-cascade.md) 再解析が知識オブジェクトを物理削除し、連鎖で学習者と教員の記録まで消える / [IK-0108](entries/IK-0108-reanalysis-deletes-teacher-decisions.md) 再解析が成果を削除して作り直すため、教員の確定がその都度消える / [IK-0109](entries/IK-0109-chunk-delete-cascades-into-claims.md) 再解析での区画の作り直しが外部キーの連鎖で主張の行ごと消す
- 代表 [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md): [IK-0010](entries/IK-0010-default-domain-hidden-at-entry.md) 分野を選ぶ口が入口に無く、既定の分野が全ての解析に当たる / [IK-0011](entries/IK-0011-cartridge-id-not-propagated.md) 分野の指定が後段の正規化へ伝わらず、既定の語彙が概念名を上書きする
- 代表 [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md): [IK-0015](entries/IK-0015-untrusted-source-boundary-undeclared.md) 資料由来のテキストを信頼しない境界が宣言されず、経路ごとに扱いが分かれていた / [IK-0320](entries/IK-0320-untrusted-source-text-in-prompts.md) 第三者が書いた資料本文が、指示と区別されないままプロンプトへ流れ込む
- 代表 [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md): [IK-0016](entries/IK-0016-design-docs-lag-behind-implementation.md) 設計書の「非スコープ」や索引が、別の層で実装済みの事実に追随しない / [IK-0221](entries/IK-0221-explanation-docs-have-no-update-rule.md) 設計書だけに追記の運用があり、機能解説と索引には無いため、大型機能が文書から丸ごと欠ける
- 代表 [IK-0019](entries/IK-0019-claim-objects-not-persisted.md): [IK-0019](entries/IK-0019-claim-objects-not-persisted.md) 分解した主張と式由来の主張が行にならず、グラフの根拠が「未解決」になる / [IK-0105](entries/IK-0105-artifact-blob-is-source-of-truth.md) 生成ログの JSONB blob が知識の正本で、関係テーブルがその劣化投影になっている / [IK-0304](entries/IK-0304-derived-claims-not-persisted.md) 生成された主張のうち一部だけが永続化され、グラフの根拠が常に「未解決」になる
- 代表 [IK-0116](entries/IK-0116-human-gate-without-candidates.md): [IK-0116](entries/IK-0116-human-gate-without-candidates.md) 人の確定を要する仕組みに候補を供給する経路が無く、受け皿が空のまま死んでいた / [IK-0121](entries/IK-0121-pipeline-creates-candidate-rows-in-human-registry.md) 「昇格は人間の操作のみ」の条項を、パイプラインが候補行を作る形に読み替えてよいか
- 代表 [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md): [IK-0201](entries/IK-0201-publish-route-contract-one-sided.md) 公開手段の契約をサーバ側だけ差し替え、呼び出し側と案内が旧契約のまま残って学習者がコースに到達できない / [IK-0203](entries/IK-0203-guidance-registry-drift.md) 案内機構が実在しない操作を案内し続け、増築された機能には追随しない
- 代表 [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md): [IK-0222](entries/IK-0222-same-judgment-reimplemented-per-layer.md) 同型のワークフローと判定が層ごとに再実装され、片方だけ更新されて食い違う / [IK-0314](entries/IK-0314-canonical-source-split.md) 同じ事実の正本が複数あり、片方だけが更新されて食い違う

## 10. 出典文書の被覆（調査・レビュー系文書ごとのエントリ有無）

| 出典文書 | エントリ |
|---|---|
| [docs/architecture/admin_ux_issues_2026-08-01.md](../../docs/architecture/admin_ux_issues_2026-08-01.md) | 3 |
| [docs/architecture/agent_inventory_and_refactoring_2026-09-10.md](../../docs/architecture/agent_inventory_and_refactoring_2026-09-10.md) | 1 |
| [docs/architecture/consolidation_survey_2026-07.md](../../docs/architecture/consolidation_survey_2026-07.md) | 2 |
| [docs/architecture/doc_review_findings_2026-08-13.md](../../docs/architecture/doc_review_findings_2026-08-13.md) | 5 |
| [docs/architecture/feature_consolidation_proposals_2026-08-13.md](../../docs/architecture/feature_consolidation_proposals_2026-08-13.md) | 6 |
| [docs/architecture/issue_494_implementation_review_2026-07-16.md](../../docs/architecture/issue_494_implementation_review_2026-07-16.md) | 4 |
| [docs/architecture/knowledge_structure_review_2026-09-12.md](../../docs/architecture/knowledge_structure_review_2026-09-12.md) | 20 |
| [docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md](../../docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md) | 6 |
| [docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md](../../docs/architecture/knowledge_structure_review_2026-09-12/B_storage.md) | 7 |
| [docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md](../../docs/architecture/knowledge_structure_review_2026-09-12/C_consumers.md) | 5 |
| [docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md](../../docs/architecture/knowledge_structure_review_2026-09-12/E_concepts.md) | 6 |
| [docs/architecture/six_lenses_2026-09-10/01_learner.md](../../docs/architecture/six_lenses_2026-09-10/01_learner.md) | 5 |
| [docs/architecture/six_lenses_2026-09-10/03_knowledge.md](../../docs/architecture/six_lenses_2026-09-10/03_knowledge.md) | 2 |
| [docs/architecture/six_lenses_2026-09-10/04_community.md](../../docs/architecture/six_lenses_2026-09-10/04_community.md) | 2 |
| [docs/architecture/six_lenses_2026-09-10/05_ai.md](../../docs/architecture/six_lenses_2026-09-10/05_ai.md) | 4 |
| [docs/architecture/six_lenses_2026-09-10/06_coldstart.md](../../docs/architecture/six_lenses_2026-09-10/06_coldstart.md) | 2 |
| [docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md](../../docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md) | 8 |
| [docs/architecture/six_lenses_2026-09-10/known_issues_features.md](../../docs/architecture/six_lenses_2026-09-10/known_issues_features.md) | 2 |
| [docs/architecture/trust_boundary_pdf_input.md](../../docs/architecture/trust_boundary_pdf_input.md) | 2 |
| [docs/architecture/user_assistant_agents_survey_2026-07.md](../../docs/architecture/user_assistant_agents_survey_2026-07.md) | 6 |
| [docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md](../../docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md) | 19 |
| [docs/architecture/vision_ux_gap_survey_2026-07-17.md](../../docs/architecture/vision_ux_gap_survey_2026-07-17.md) | 10 |
| [docs/architecture/vision_ux_gap_survey_2026-07.md](../../docs/architecture/vision_ux_gap_survey_2026-07.md) | 6 |
| [docs/backend/rag-chat.md](../../docs/backend/rag-chat.md) | 1 |
| [docs/development_checklist.md](../../docs/development_checklist.md) | 8 |
| [docs/features/assistant_common_infra_design.md](../../docs/features/assistant_common_infra_design.md) | 2 |
| [docs/features/assistant_screen_adapter_design.md](../../docs/features/assistant_screen_adapter_design.md) | 1 |
| [docs/features/atlas_node_correspondence_design.md](../../docs/features/atlas_node_correspondence_design.md) | 1 |
| [docs/features/atlas_vector_anchoring_design.md](../../docs/features/atlas_vector_anchoring_design.md) | 1 |
| [docs/features/concept_registry_design.md](../../docs/features/concept_registry_design.md) | 4 |
| [docs/features/decision_context_design.md](../../docs/features/decision_context_design.md) | 3 |
| [docs/features/disclosure_axes_design.md](../../docs/features/disclosure_axes_design.md) | 1 |
| [docs/features/element_deliberation_workspace_design.md](../../docs/features/element_deliberation_workspace_design.md) | 1 |
| [docs/features/graph_dialogue_review_design.md](../../docs/features/graph_dialogue_review_design.md) | 6 |
| [docs/features/image_pipeline_knowledge_library_design.md](../../docs/features/image_pipeline_knowledge_library_design.md) | 2 |
| [docs/features/knowledge_objects_design.md](../../docs/features/knowledge_objects_design.md) | 10 |
| [docs/features/knowledge_transfer_design.md](../../docs/features/knowledge_transfer_design.md) | 3 |
| [docs/features/learning_chat_entry_unification_design.md](../../docs/features/learning_chat_entry_unification_design.md) | 1 |
| [docs/features/learning_units_design.md](../../docs/features/learning_units_design.md) | 3 |
| [docs/features/lecture_audio_generation_readiness.md](../../docs/features/lecture_audio_generation_readiness.md) | 1 |
| [docs/features/lecture_slide_sync_design.md](../../docs/features/lecture_slide_sync_design.md) | 1 |
| [docs/features/llm_response_streaming_design.md](../../docs/features/llm_response_streaming_design.md) | 2 |
| [docs/features/paper_discovery_design.md](../../docs/features/paper_discovery_design.md) | 1 |
| [docs/features/paper_radar_design.md](../../docs/features/paper_radar_design.md) | 3 |

調査・レビュー系（ファイル名に survey / review / findings / issues / debate / audit / proposal を含む）で、まだ 1 件もエントリの出典になっていない文書:

- [docs/architecture/ai_assistant_personalization_debate_2026-08-15.md](../../docs/architecture/ai_assistant_personalization_debate_2026-08-15.md)
- [docs/architecture/vision-debate-commons-of-questions-2026-09-04.md](../../docs/architecture/vision-debate-commons-of-questions-2026-09-04.md)
- [docs/architecture/vision-debate-education-research-direction-2026-09-04.md](../../docs/architecture/vision-debate-education-research-direction-2026-09-04.md)
- [docs/architecture/vision_future_debate_2026-09-03.md](../../docs/architecture/vision_future_debate_2026-09-03.md)
- [docs/architecture/vision_review_debate_2026-08-29.md](../../docs/architecture/vision_review_debate_2026-08-29.md)

