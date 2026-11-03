---
id: IK-0781
title: "役割判定の span 境界が inline 数式の中（`$\\hat{N}_{ijk...}$` の「.」）で切れ、Previous Span が `$\\hat{N}_{ijk.` で途切れる。逆に「[ref].In」のように句点の後に空白が無いと 2 文が 1 span に結合する"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/extraction-units.md"
feature_context:
  realizing: "TeX 由来の本文ブロックを文単位の span に分け、span 単位で主張の採否を判定する"
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [decomposition]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（2026-10-16）: 共有の文分割器ではなく、rhetorical_role の LLM が数式区間 `$…$` の中（`\hat{N}_{ijk...}` の「.」）で span を
    切っており、サーバ側のオフセット解決もそれを数式の外へ寄せていなかった。「[ref].In」は原文の \cite の後ろに空白が無く、引用除去
    後に句点と次文が詰まったもの。「$ In the limit」は原文に句点が無い。構造軸 decomposition = span の単位（文）の境界規則が数式区間を
    考慮していなかった。処理軸 logic = 境界の解決に数式区間を避ける条件が無かった。接続軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "文の境界を句読点の字面だけで決め、数式区間の中の句点で文を割り、空白の無い句点では文を割り損ねる"
pattern: unit-of-work-undefined
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 S2 が 2609.35757（TeX 経路）の claim_qualification 入力を読み、Previous Span が「$\\hat{N}_{ijk.」で途切れること（seq 2750）を見て、同じブロックが equation_semantics 側の材料（seq 2855）では完全であることから span 分割の段と切り分けた。「[ref].In」で 2 文が 1 span に結合する例（seq 2789）も同じ段で見た。出所: uxsim/runs/radar_loop/BRAIN_NOTES_S2.md。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "rhetorical_role のオフセット解決に snap_span_out_of_math を足し、数式区間の中に落ちた境界を数式の外へ寄せる（offset_correction=\"math_boundary\" で記録）。数式区間の判定は agents/sentence_split.py の math_regions を正本にし、プロンプトに「数式の中で span を切らない」1 行を足した。「[ref].In」は tex_archive の _replace_citations が引用除去の後に空白を 1 字補う。「$ In the limit」は原文に句点が無いので補わない（原文に無いものを足さない）。"
  landed_in:
    - "src/episteme_graph/agents/rhetorical_role/repair.py"
    - "src/episteme_graph/agents/rhetorical_role/prompt.py"
    - "src/episteme_graph/agents/sentence_split.py"
    - "backend/core/document_pipeline/tex_archive.py"
    - "src/tests/agents/rhetorical_role/test_math_boundary_and_preceding_equation.py"
    - "backend/tests/test_latex_display.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "実 LLM での 2609.35757 の再演（数式内で切れる span が消えるか）"
related: [IK-0712, IK-0780]
view_of: []
history:
  - date: '2026-10-16'
    field: classification.cause_status
    from: hypothesis
    to: confirmed
    reason: "原因は共有の文分割器ではなく rhetorical_role の LLM が数式内で span を切っていたことを確認。「[ref].In」は原文の空白欠落、「$ In the limit」は原文に句点が無いことを確認"
---

## 課題

症状: claim_qualification の Previous Span が `$\hat{N}_{ijk.` で終わり、数式の途中で文が切れる（seq 2750）。別のブロックでは
「[ref].In」の後ろで文が切れず、2 文が 1 span になって span が過大になる（seq 2789）。「…$ In the limit」も 2 文が 1 span になる。

原因（確認済み）: 数式内の切断は rhetorical_role の LLM が数式区間の中で span を切り、サーバ側がそれを直さなかったため。
「[ref].In」は原文の \cite の後ろに空白が無いため。「$ In the limit」は原文に句点が無いため（取り込みの欠落ではない）。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2750・2789・2855）。

追加の出所（頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2745 — 「…^{\rm FoG}$ In the limit」と式末の句点が無く 2 文が 1 span に融合。S2 seq 2849 の「$\kappa_{nm}=…$ In the limit」と同じ。原文に句点が無いことを 2026-10-16 に確認 / seq 2790 — 「[ref].In the PBS」）。

追加の出所（頭脳 T0・是正前の run）: uxsim/runs/radar_loop/BRAIN_NOTES_T0.md（seq 2916・2919 — inline 式の span が「…$\hat{N}_{ijk.」で切れ、別の span は「…s_{ij}$. The latter…」で 2 文が融合）。これらは是正前の document c795b2cf の材料で、再発ではない（是正後の document 2a24b9e4 の役割判定 seq 2976〜3015 では同じ症状の記録が無い）。

## 発見の観点

同じブロックを 2 つのステージの材料で読み比べた（data_inspection）。

## 解決の観点

境界の解決で数式区間を避ける（single_point_fix）。数式区間の判定は sentence_split の math_regions を正本にして共有し、回帰テストで固定した（guardrail_fix）。引用除去後の空白は取り込みで 1 字補うが、原文に無い句点は足さない（情報を作らない）。

解消の確認（reproduction_rerun・頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3368 — 是正前に数式の内側で span が切れていた tex_b111 が、再解析では文単位で割れる）。TeX マクロ是正（IK-0804）と同じ run の結果で、どちらの是正が効いたかは切り分けていない。

解消の確認（reproduction_rerun・頭脳 Y2・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3476 — tex_b111 の span 境界が数式の外で正しく切れる）。

## 一般化

句読点の字面だけで文を切ると、数式区間や空白の欠けた句点で単位が崩れる。確かめ方は、`$…$` の中に「...」を含む文と「[ref].X」を
含む本文を通し、span が数式の途中で切れず、空白の無い句点でも文が分かれることを見る。辞書の型 unit-of-work-undefined。
