---
id: IK-0426
title: "discuss 開幕のバックボーンで stage / stage_label が空になり、ノードの表示名が英語の段階名（Theory basis 等）のまま出ていた（理論操作グラフの主グラフのノードは stage キーを持たず、段階は label の英語表示名だけに載っていた）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 論文と議論する開幕画面で、理論の組み立ての段階を学習者向けの日本語で並べる
  layers: [discuss]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 開幕の投影（core/discuss/opening.py project_backbone）はノードの stage キーを読む契約だったが、A層の
    主グラフのノードは stage キーを持たず、#308 の規約で label が段階の英語表示名そのものになっている（contract）。
    処理軸: stage が空なので stage_label も空になり、表示名が英語の段階名のまま学習者に出た（wording）。構造軸・統制軸は
    none。確認: 第 9 周の開幕 DTO で、バックボーンの stage / stage_label が空文字だった。
generalization:
  level: repo_pattern
  general_form: 下流の投影が上流の出力に無いキーを読み、上流が別の場所に載せている同じ情報を拾わない
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction]
  note: 第 9 周のペルソナが開幕 DTO をそのまま読んだ記録を、投影のコードと突き合わせた。
resolution:
  perspective: [single_point_fix, canonical_source]
  note: >-
    stage が無ければ label から element_vocab.theory_stage_key（stage の逆引きの正本）で段階を引き、stage_label を
    element_vocab の日本語ラベルで埋める。英語の段階名だけの label は日本語の段階ラベルに置き換え、表示ラベルを内部 ID に
    縮退させない（脆い箇所の投影も同じ）。node_id は表示しないが、学習画面が「ここから話す」の構造帰属の anchor id として
    送り返す参照キーなので DTO に残した。document_id も論文の選択に使うので残した。central_question・中心命題の本文は
    A層が論文の言語で生成したもので、ここでは訳さない（多言語化は別件）。
  landed_in:
    - backend/core/discuss/opening.py
    - backend/tests/test_ik0422_prerequisite_gate_once.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - 開幕画面の見た目（学習画面の描画は変えていない）
      - 論文の言語で書かれた中心命題・問いの本文の多言語化
related: []
view_of: []
history: []
---

## 課題

開幕のバックボーンの段階ラベルが空で、表示名が英語の段階名のまま。

## 発見の観点

開幕 DTO と投影のコードを突き合わせた。

## 解決の観点

label から段階を逆引きし、日本語の段階ラベルで埋める。

## 一般化

下流の投影が上流の出力に無いキーを読む型。
