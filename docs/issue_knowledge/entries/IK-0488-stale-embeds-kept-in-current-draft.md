---
id: IK-0488
title: "前の生成の埋め込み（「![[source:topic_summary]]」・解析し直しや上限で一覧から外れた主張）が「現在の下書き」に残ったままモデルに渡っていた"
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の再生成が、前回の下書きを材料として渡しつつ、埋め込みはいまの参照一覧の (kind, id) だけに限る
  layers: [course_builder]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: 配信側（_sanitize_topic_evidence_embeds）は引けない埋め込みを落とすが、次の生成へ渡す「現在の下書き」は前の出力の本文をそのまま渡し、いまの参照一覧（閉世界）と照合していなかった。主張の件数の上限（IK-0463）は下書きが埋め込んでいる主張を優先せず、埋め込み済みの主張が上限で一覧から消えることもあった。前の版の参照が次の版の入力に残る。
generalization:
  level: repo_pattern
  general_form: 前の版の出力を次の版の入力に渡すとき、いまの閉世界と照合しない
pattern: stale-derivative-served
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプトの「現在の下書き」の埋め込みを、同じプロンプトの available_references の (kind, id) に突き合わせた（11 本で一覧に無い埋め込みがあった）。
resolution:
  perspective: [single_point_fix, order_and_budget]
  note: >-
    strip_embeds_outside_references が、参照一覧に無い (kind, id) の埋め込みを外す（埋め込みだけの行は行ごと・文中は記法だけ。未知の kind は触らない）。_topic_existing_draft が渡す前に通す。_dedupe_and_cap_claim_references は上限を超えたとき、下書きが埋め込んでいる主張を先に残す（並びは保つ）。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0484_0489_course_draft_regen12.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成
      - 下書きが文字列（dict でない student_material）の旧形式は外していない
      - 外した埋め込みの説明文（前後の文）は残るので、埋め込みを前提にした言い回しが残ることがある
related: [IK-0455, IK-0461, IK-0463, IK-0440]
view_of: []
history: []
---

## 課題

前の生成の埋め込みが、いまの参照一覧に無いまま下書きに残っていた。

## 発見の観点

下書きの埋め込みを参照一覧の (kind, id) と突き合わせた。

## 解決の観点

渡す前にいまの閉世界と照合し、上限では埋め込み済みの主張を優先する。

## 一般化

前の版の出力を次の版の入力にするときは、いまの閉世界で照合してから渡す。
