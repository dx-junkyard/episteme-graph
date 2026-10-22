---
id: IK-0707
title: "論文レーダーの起点の題名が教材のファイル名のままで、arXiv 題名との一致による出所の自動記帳がファイル名の教材では原理的に起きない"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §19"
  - "docs/features/paper_radar_design.md §11"
feature_context:
  realizing: "手動アップロードした教材の arXiv 出所を推定し、題名の一致で自動記帳する"
  layers: [paper_radar, paper_discovery]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（起点の DTO と照合コード）: 照合相手が教材の表示名で、表示名がファイル名（arXiv-NNNN.NNNNNvN）だと arXiv の題名と一致する
    ことが無い。接続軸 meaning = 「教材のタイトル」が表示名と論文の題名の 2 つを指し、照合の段で取り違えていた。構造軸
    representation = 表示名と論文題名を区別する欄が起点の表現に無く、見出しもファイル名になっていた。処理軸 none = 正規化と
    完全一致の判定は正しい。統制軸 none = 自動記帳の条件（完全一致時のみ）は妥当で変えていない。
generalization:
  level: general
  general_form: "同じ呼び名が表示用の名前と内容の名前の 2 つを指し、照合や見出しが内容を表さない方を使う"
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, trace_walk]
  note: "起点の見出しが内容を表さない文字列で毎回手動確定を求められることを読み、照合相手を遡って表示名だったことを確かめた（出所: uxsim/runs/radar_loop/judge-scan-02.md §4 の 3・scan-02.json）。"
resolution:
  perspective: [representation_change, canonical_source]
  note: "照合相手に採用 run の文書構造の論文題名を加え、一致の経路を記録する。表示名がファイル名様なら arXiv の題名を見出しにし、教材名は添えて書き換えない。"
  landed_in:
    - backend/core/paper_discovery/radar.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §19"
  verification:
    methods: [guardrail]
    unverified:
      - "実機でファイル名の教材が自動記帳されること"
      - "文書構造の題名が取れていない教材での挙動"
related: []
view_of: []
history: []
---

## 課題

症状: 起点の見出しがファイル名のままで、arXiv から題名を引けても「照合できません」と手動確定を求められる。
原因: 照合相手が教材の表示名（ファイル名）で、論文の題名ではなかった。ファイル名だけの教材（出所の後付け登録が救おうとした典型）で
自動記帳が一度も起きない。

出所: uxsim/runs/radar_loop/judge-scan-02.md §2・§4 の 3、検索記録 scan-02.json。

## 発見の観点

実データの起点 DTO を読み（data_inspection）、照合相手を遡った（trace_walk）。

## 解決の観点

内容の名前（文書構造の論文題名）を照合相手と見出しの正本に加えた（representation_change / canonical_source）。教材名は
書き換えず並べて示す。自動記帳の条件（完全一致）は変えない。

## 一般化

「タイトル」「名前」が表示名と内容名を兼ねる箇所では、照合・検索・見出しが内容を表さない方を使いやすい。
