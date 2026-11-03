---
id: IK-0806
title: "TeX 取り込みで図表の float を書かれた位置の節に入れ、付録の表が結論節のブロック列に挟まって前後文脈・代表ブロックに表の行が流れる"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX 原文の図表を、本文が参照する節の中の表ブロック（caption + 表本体 raw.in_table）にして後段に渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    確定: tex_archive は float（table / figure 環境）の節を出現位置で決めていた。2610.08751 では付録 A の表 tab:fiducial_nuisance が結論節の中に書かれ、結論節の段落の前後文脈に表の行が挟まった。表本体は既に raw.in_table の表由来ブロック（GROBID 経路と同じ形）で役割判定の母集合からは外れていたが、paper_skeleton の代表・結論ブロックは raw.in_table を見ていなかった。接続軸 information = float の参照関係（\\ref）が節の決定に渡っていない。他の軸は none。
generalization:
  level: repo_pattern
  general_form: "組版上の配置位置を所属の代理に使い、本文の参照関係という所属の根拠が並べ方に渡らない"
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 U1 が document 1471ff7f（2610.08751）の paper_skeleton（seq 3019）と rhetorical_role（seq 3058・3070・3100・3109）の材料を読み、表の行が代表ブロック・結論ブロック・Previous/Next Context に流れ、付録の表が結論節の位置にあることを見た。出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "_relocate_floats_to_referencing_section: float の置かれた節の本文がその label を参照していなければ、最初に参照する本文段落の直後へ移し section_id を付け替え raw.relocated_from_section_id に元の節を残す（参照の無い float は動かさない・block_id と order は振り直す）。paper_skeleton の代表・結論ブロックから raw.in_table を外した。原本 4 本で 4 / 0 / 3 / 12 個の float が移り、他 3 本はブロックのテキスト集合が不変。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
    - "src/episteme_graph/agents/paper_skeleton/input_builder.py"
    - "src/episteme_graph/agents/rhetorical_role/input_builder.py"
    - "src/tests/agents/rhetorical_role/test_input_builder.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "節の決定を取り込みの 1 箇所（ブロック確定後の 1 パス）に置いた。rhetorical_role の前後文脈は _neighbor_text / _neighbor_crosses_section が _is_table_body を飛ばす（2026-10-17 追補）。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "再解析 run で X0 seq 3351・3357・3399 の Previous / Next Context から表本体が消えたことの実測（回帰テストのみ）"
related: [IK-0696, IK-0799]
view_of: []
history:
  - date: '2026-10-17'
    field: resolution.landed_in
    from: "tex_archive と paper_skeleton のみ"
    to: "rhetorical_role の前後文脈から表本体を外す"
    reason: "rhetorical_role/input_builder.py の _neighbor_text / _neighbor_crosses_section が _is_table_body のブロックを飛ばす（回帰 test_input_builder.py::test_neighbor_context_skips_table_body_blocks）。"
  - date: '2026-10-17'
    field: resolution.verification.unverified
    from: "rhetorical_role の前後文脈に表本体が載る件は未変更"
    to: "tex_archive 側の種別変更では解かない（判断の記録）"
    reason: "再解析 run 40ecf8df（X0 seq 3351・3357・3399）でも Previous / Next Context に表本体が caption なしで載る。表本体を本文でない種別にすれば rhetorical_role の _CONTEXT_BLOCK_TYPES から外れるが、BLOCK_TYPES に表本体の種別は無く、GROBID 経路と同じ body_paragraph + raw.in_table の形を paper_skeleton / claim_qualification / equation_semantics / rhetorical_role の 4 つの後段が前提にしているため、TeX 経路だけ種別を変えると契約が割れる。是正は rhetorical_role/input_builder.py の _neighbor_text / _neighbor_crosses_section が _is_table_body を飛ばす 1 行（fixRejectFlow の編集が終わってから）。種別が body_paragraph + raw.in_table のままであることは test_latex_display.py のテストで固定した。"
---

## 課題

症状: 付録の表（tex_b145）が結論節の中に配置され、結論段落の Previous Context が表の行になる。表本体が paper_skeleton の代表・結論ブロックにも入る。

原因: float の節を出現位置で決めていた。代表ブロックの選定が表本体を除いていなかった。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3019・3058・3070・3100・3109）。

残存の確認（頭脳 Y1・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3551 — equation_semantics の Next Text Blocks で表 1 の本体が caption なしに「|」区切りで並び、表の意味が読めない）。

## 発見の観点

節の所属と本文の参照（\ref）を突き合わせた（data_inspection）。

## 解決の観点

参照する節へ寄せる 1 パスを置いた。

未是正の範囲（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3351・3357・3399 — 役割判定の Previous / Next Context には、まだ表本体（Table 1・2・DESI のトレーサ表）の行が caption なしで載る。float の節の是正後も、表の行を前後文脈に入れない扱いは入っていない）。

解消の確認（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3603 — 表 tex_b100（Table 3 の事前分布）が行テキストで渡る）。残る観察: 表見出し・caption と推定される tex_b99・tex_b170 が窓から欠ける。

## 一般化

配置位置を所属の代理にすると所属を誤る。辞書の型 extent-decided-by-proxy-signal の近傍。

