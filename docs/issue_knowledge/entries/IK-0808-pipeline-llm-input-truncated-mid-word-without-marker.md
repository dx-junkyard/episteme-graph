---
id: IK-0808
title: "A層 agent の LLM 入力で長い Source Block / 近傍ブロックが語の途中（「…evidence of the robustnes」）や先頭側（「…factor already defined…」）から印なしで切り詰められ、主語や結論が落ちる"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
  - "docs/features/paper_radar_autonomous_loop_design.md"
feature_context:
  realizing: "主張採否・式の意味判定で、対象ブロックと前後のブロックの本文を LLM の入力に載せる"
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（2026-10-17）: claim_qualification の source_block_text は先頭 1,800 字の素の切り出し（語の途中・印なし、上限より後ろの span では対象が原文から落ちる）。rhetorical_role の近傍文脈は先頭 280 字の素の切り出し。equation_semantics の文脈は語境界・印ありだが前文脈は文の途中から始まり主語が落ちていた。いずれも切った事実が coverage に残っていなかった（rhetorical_role の対象ブロックだけ記録済み）。 以下は起票時の仮説:
    仮説: claim_qualification / equation_semantics の input_builder がブロック本文を文字数で切り詰め、語・文の境界を見ず、切ったことを
    明示する場合としない場合がある（「…」の有無が揺れる）。先頭側を切る経路もある。処理軸 input_handling = 切り詰めの境界と印の扱い。
    接続軸 information = 主語・結論が後段の材料に渡らない。構造軸 none / 統制軸 none。学習者向けの出典表示では切り詰めの印と境界の
    規則が是正済み（IK-0514・IK-0393）で、A層の LLM 入力の面にはその規則が無い可能性。確定には各 builder の切り詰め関数と上限を読む。
generalization:
  level: general
  general_form: "入力の本文を文字数で切るとき語・文の境界と切った印を扱わないため、主語や結論の欠けた断片が完全な本文として読まれる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W0 が claim_qualification（seq 3201・3225）と equation_semantics（seq 3252・3279）の材料を読み、Source Block が「…evidence of the robustnes」「…with the 」で印なしに切れ、tex_b103 は先頭が切られて主語（A_AP is the）が落ちていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md）。頭脳 T1・T2 も「…」の切り詰めを記している（BRAIN_NOTES_T1.md seq 2881、BRAIN_NOTES_T2.md seq 2897・2900）。"
resolution:
  perspective: [canonical_source, carry_through]
  note: "切り詰めの正本 src/episteme_graph/agents/text_truncation.py（truncate_text = 文境界 → 語境界・$…$ の外・切った側に「…」・空白は正規化しない / excerpt_around = 対象を切らずに前後を切る）を新設し、学習者向け backend/core/text_excerpt.excerpt と同じ流儀にした（A層は backend を import しない）。equation_semantics.context_excerpt と rhetorical_role.truncate_block_text はこれへ委譲し、前文脈は窓の中の最初の文の頭から始める。rhetorical_role の近傍文脈・claim_qualification の原文ブロック（対象 span を含む窓）もこれを通す。切ったブロックは 3 ステージとも coverage details.truncated_blocks に {block_id, reason}（block_text_over_char_limit / source_block_over_char_limit / context_block_over_char_limit）で残す（件数は書かない）。上限値は不変。"
  landed_in:
    - "src/episteme_graph/agents/text_truncation.py"
    - "src/episteme_graph/agents/rhetorical_role/input_builder.py"
    - "src/episteme_graph/agents/claim_qualification/input_builder.py"
    - "src/episteme_graph/agents/equation_semantics/input_builder.py"
    - "src/tests/agents/test_text_truncation.py"
    - "src/tests/agents/rhetorical_role/test_input_builder.py"
    - "src/tests/agents/claim_qualification/test_input_builder.py"
    - "src/tests/agents/equation_semantics/test_candidate_text_spans.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "seq 3201・3225・3252・3279 の材料を再生成して断片が消えたことの実測"
      - "rhetorical_role で 1,800 字を超えるブロックの後ろ側の span が作られない件（X2 seq 3374・3389）"
      - "日本語本文（空白の無い文）での文境界の扱い — 規則は英文の . ! ? のみで、日本語は語境界も無く文字数で切れる"
related: [IK-0514, IK-0393, IK-0793]
view_of: []
history:
  - date: '2026-10-17'
    field: resolution.verification.unverified
    from: "seq 3201・3225・3252・3279 の材料の再生成"
    to: "claim_qualification の窓は回帰テストで固定・rhetorical_role の対象ブロック本文の末尾切りは残る"
    reason: "頭脳 X2 seq 3374・3389（run 40ecf8df）は rhetorical_role の Current Block Text（1,800 字）の末尾が「…」で切れ、脚注・結論の続きの span がそもそも作られない（BRAIN_NOTES_X2.md）。claim_qualification の Source Block は既に対象 span を含む窓（excerpt_around）で、末尾側の span が落ちないことを test_adjacent_equations.py::test_tail_span_of_long_block_stays_in_source_block_window で固定した。役割判定は span を作る段なので対象を含む窓が定義できず、上限を変えずに後ろを拾うにはブロックを複数窓に割って LLM 呼び出しを増やす必要がある — 未是正（判断待ち）。"
---

## 課題

症状: Source Block が「…evidence of the robustnes」（語の途中）や「…with the 」で印なしに切れる。tex_b103 は先頭が切られ「…factor already defined…」から始まり、
主語が落ちる。「…」の印が付く場合と付かない場合がある。
原因（仮説）: 入力の切り詰めが語・文の境界と印を扱っていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3201・3225・3252・3279）。

追加の出所（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3200 — Source Block が「…robustnes」で語の途中で切れる。seq 3251・3254 — 同じ tex_b50 が前では末尾「…for a…」、後では先頭「…is the profiled」で切られ、窓の上限で前後どちらかが落ちる）。

確認事項（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3322・3373 — 原文では完結している文のブロック本文の末尾に「…」が付く。切ったときに印が付く側の規則が、実際には切っていない本文にも印を付けている可能性があり、印を付ける条件（実際に切ったときだけか）を確かめる）。

確認事項（頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3374・3389 — 是正後の run で切り詰めの印「…」は付くが、ブロック本文の上限（1695 字）そのもので脚注の続きや段落末尾が落ち、span がその部分を持てない。上限値が役割判定の対象ブロックに対して足りているかを確かめる）。

残存の確認（頭脳 Y0/Y1・再解析 run 40ecf8df）: 印は claim_qualification の Source Block と equation_semantics の前後ブロックに付く（uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md seq 3486・3537）。未是正の面: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3528 — paper_skeleton の要旨・代表・結論の抜粋が「line-of-」「cluste」「Howeve」と語の途中で印なしに切れる。seq 3549 — 印は付くが \left / \right の対の途中で切れ、表示に流すと崩れる）、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3538 — ブロック本文そのものに「…」が付き、span の逐語規則で「…」を含めるか曖昧。seq 3485・3533・3551 — 上限で後続 span の材料が欠ける）。

追加の出所（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3584・3614 — Preceding Equation の TeX が「…」で切れ（aligned 環境の後半が欠ける）、label が空のとき「label: 」の空行が出る）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3720 — 役割判定の対象ブロック自体（Current Block Text）が末尾「…」で切られ、図 4 右パネルの説明の途中で終わる。印は付くが末尾の内容が span にならず主張候補から落ちる。seq 3609 — 先行式の頭だけを渡す切り方で、where の「in the last line」が指す最終行が材料に無い）。

追加の出所（頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3761 — Adjacent Equations の長い式が末尾「…」で切れる（定義の参照先は切れの前に出るので判定には足りた）。seq 3842 — Next Text Blocks の途中切れ）。

## 発見の観点

LLM 入力のブロック本文を、元のブロックの全文と突き合わせた（data_inspection）。

## 解決の観点

切り詰めの正本 src/episteme_graph/agents/text_truncation.py（truncate_text = 文境界 → 語境界・$…$ の外・切った側に「…」・空白は正規化しない / excerpt_around = 対象を切らずに前後を切る）を新設し、学習者向け backend/core/text_excerpt.excerpt と同じ流儀にした（A層は backend を import しない）。equation_semantics.context_excerpt と rhetorical_role.truncate_block_text はこれへ委譲し、前文脈は窓の中の最初の文の頭から始める。rhetorical_role の近傍文脈・claim_qualification の原文ブロック（対象 span を含む窓）もこれを通す。切ったブロックは 3 ステージとも coverage details.truncated_blocks に {block_id, reason}（block_text_over_char_limit / source_block_over_char_limit / context_block_over_char_limit）で残す（件数は書かない）。上限値は不変。

解消の確認（reproduction_rerun・頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3363 — 是正前に先頭から切られていた tex_b103 のブロックが全文で渡る）。

解消の確認（reproduction_rerun・第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3582 — Next Context が式の途中で切れるところに印「…」が付く）。

解消の確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3591・3594・3606 — 前後のブロックの切り詰めには印「…」が付き、対象式の位置に応じて窓を切る）。

## 一般化

学習者向けの面で直した切り詰めの規則が、LLM 入力の面には効いていない。辞書の型 rule-enforced-per-surface。
