---
id: IK-0811
title: "導出チェーンの境界が広すぎ、derivation_eq_post_laplace に AP・多重極・尤度の無関係な式が入って 1 本 16 式になり、主グラフの要点でベイズ周辺化の出発点が AP の式になる"
status: open
recorded_at: 2026-10-17
resolved_at: null
sources:
  - "docs/pipeline/theory-graph.md"
  - "docs/pipeline/agents.md"
feature_context:
  realizing: "式間の導出チェーンを組み、理論操作グラフの段と部品の導出の材料にする"
  layers: [pipeline_a, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: DerivationChainAgent（非LLM）がチェーンの境界を記号の共有や文書順の連続で決め、共通の記号（d・Θ・P 等）を持つだけの
    別の議論の式（AP の座標変換・多重極・尤度）まで 1 本の導出に繋ぐ。構造軸 decomposition = 導出 1 本の単位（どこから始まりどこで
    終わるか）が議論の境界で定義されていない。接続軸 target = 導出の出発点として別の議論の式が選ばれ、主グラフの要点・部品の振り分けが
    誤った対象を指す。処理軸 none / 統制軸 none。refiner の子部品の重複（IK-0728）や段の集約（IK-0753）より上流の、チェーン自体の
    境界の問題として分けた。確定には derivation_chain がチェーンを繋ぐ条件（記号一致・順序・ラベル）と、post_laplace の 16 式が繋がった
    経路を読む。
generalization:
  level: general
  general_form: "連鎖の境界を共有記号や並び順だけで決めるため、別の議論の要素が 1 本の連鎖に入り、連鎖の出発点や要約が誤った対象を指す"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W2 が component_assembly の子振り分け（2610.08751・seq 3284）で derivation_eq_post_laplace の equation_ids に AP（b68/b70）・多重極・尤度（b96）の式が混ざる 16 式・confidence 0.0 を見、narrative_annotator（seq 3290・応答は後任が採用）で teaching_takeaways の導出の出発点が AP の式 eq_tex_b68/b70 になっていること、整合関係の段に主張 175 件・式 16 が集中し辺が循環することを確かめた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md）。TeX マクロ是正前の素材。"
resolution:
  perspective: [pending]
  note: "未解決。チェーンの境界を、式間の明示の参照（本文の Eq. 参照・ラベル）と同じ節・同じ議論の範囲で決め、共有記号だけでは繋がない。繋がりの根拠が弱い式は別チェーンか review_required に残す案。"
  landed_in:
    - "docs/pipeline/theory-graph.md"
related: [IK-0728, IK-0753, IK-0717]
view_of: []
history: []
---

## 課題

症状: derivation_eq_post_laplace（ラプラス近似による周辺化の導出）の equation_ids に AP の座標変換（b68/b70）・多重極・尤度（b96）の無関係な式が入り、1 本 16 式・confidence 0.0。
主グラフの teaching_takeaways は「Derive eq_post_laplace from eq_tex_b68 via define -> substitute -> …」で、ベイズ周辺化の出発点が AP の式になる。整合関係の段には主張 175 件・式 16 が集中し、辺が循環する。
原因（仮説）: 導出チェーンの境界が共有記号や並び順で決まり、議論の境界を見ていない。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3284・3290）。

追加の出所（頭脳 X1・是正前の run（seq 3289〜3310・2026-10-17 03:00〜03:11））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3289 — comp_004「Jeffreys prior cancels Laplace term」の入出力に eq_Pgg / eq_multipoles / eq_tex_b70（AP・多重極）が混入し、comp_011（合成データ設定）も同じ式群。seq 3298 — comp_011 の op4 の下位式がラプラス周辺化の導出一式で上位と噛み合わない。部品の式割り当てが節の境界を越える）。

## 発見の観点

導出チェーンの式の集合と出発点を、論文の議論（周辺化・AP・多重極・尤度の節）と突き合わせた（data_inspection）。

## 解決の観点

未解決。

## 一般化

連鎖の境界を共有記号で決めると、別の議論が 1 本に入る。辞書の型 extent-decided-by-proxy-signal。
