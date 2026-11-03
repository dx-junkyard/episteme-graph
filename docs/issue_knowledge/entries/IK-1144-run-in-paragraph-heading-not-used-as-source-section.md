---
id: IK-1144
title: "段落頭の走り込み見出し（「Four-phase training.—」）は節として拾われず、その出典の行だけ節の見出しが付かない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/assistant_screen_adapter_design.md"
feature_context:
  realizing: "出典の行に、学習者が論文の中で場所を探せる節の見出しを添える"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    _source_section_note はチャンクの source_metadata.section_title だけを見る。REVTeX の走り込み見出しは文書構造の節にならず、チャンクの節は空。接続軸 information（抜粋の頭にある見出しが出典の行へ渡らない）。
generalization:
  level: repo_pattern
  general_form: "原文に書かれた見出しが構造化の単位にならず、後段の表示で欠ける"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: >-
    製品頭脳 B 4318: 出典7/出典1 は節「Dual-network architecture」、出典2（4 段階の交互学習）だけ節名なし。
resolution:
  perspective: [single_point_fix]
  note: >-
    第 25 周 第 10 波（2026-10-31）: section_title が空のときだけ、抜粋の頭の「語 1〜8 個 + ピリオド + ダッシュ（—―・空白なし）」を節として添える（_RUN_IN_HEADING_RE・決定論）。文書構造の側（節として拾う）は変えていない。
    第 25 周 第 11 波: 一覧の出典行（chunk_location_hint の meta）にも走り込み見出しが効いていなかった（judge_studio_r11 st-08 seq15・16）。判定を services.RUN_IN_HEADING_RE / run_in_heading の 1 箇所にし、本文の出典行と一覧の手がかりの両方が通る。
  landed_in:
    - "backend/api/services.py"
    - "backend/tests/test_round25_wave11_doubt_studio_chat.py"
    - "backend/api/routes/learning.py"
    - "backend/tests/test_round25_wave10_chat.py"
  verification:
    methods: [guardrail]
    unverified:
      - "抜粋が見出しの後ろから始まるチャンクでは付かない"
      - "文書構造（GROBID / TeX）の側で走り込み見出しを節にする是正は未着手"
related: [IK-1116]
view_of: []
history: []
---

## 課題

段落頭の走り込み見出し（「Four-phase training.—」）は節として拾われず、その出典の行だけ節の見出しが付かない。

## 発見の観点

製品頭脳 B 4318: 出典7/出典1 は節「Dual-network architecture」、出典2（4 段階の交互学習）だけ節名なし。

## 解決の観点

section_title が空のときだけ、抜粋の頭の「語 1〜8 個 + ピリオド + ダッシュ（—―・空白なし）」を節として添える（_RUN_IN_HEADING_RE・決定論）。文書構造の側（節として拾う）は変えていない。

## 一般化

原文に書かれた見出しが構造化の単位にならず、後段の表示で欠ける。型 information-dropped-as-unrepresentable。
