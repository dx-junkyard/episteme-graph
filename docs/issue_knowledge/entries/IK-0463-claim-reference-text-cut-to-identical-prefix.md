---
id: IK-0463
title: "授業用ドラフトの参照一覧で主張の本文が予算のため約 55 字で切られ、兄弟の atomic 子が同じ文になって「埋め込む前に text で確かめる」規則が守れなかった"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: コース内容の生成が、埋め込みに使ってよい主張を本文の抜粋付きでモデルに渡す
  layers: [course_builder]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [budget]
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
    統制軸: 文字数予算の段 shorten_reference_texts が主張の本文を 60 字まで詰め、件数は保った。atomic 子は親の書き出しを共有するので、切ると区別できない。処理軸: 同じ本文の主張の重複除去が無かった。
generalization:
  level: repo_pattern
  general_form: 予算を守るために各項目を短く切り、項目を区別する情報が消える
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [data_inspection]
  note: 再生成プロンプトの available_references で、別 ID の主張が同じ text になっている組を数えた。
resolution:
  perspective: [order_and_budget]
  note: >-
    主張の本文は CLAIM_REFERENCE_TEXT_LIMIT（240）で載せ（evidence_links の summary が 220 字なので実効はそれ）、件数を CLAIM_REFERENCE_LIMIT（12）で抑える。予算の段は主張の本文を切らず、下書きが埋め込んでいない主張を後ろから外す（下限 4）。同じ本文（正規化一致・一方が他方の切り詰め）は長い方を残す。外したときは omitted_claims_note。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0459_0465_course_draft_regen11.py
    - backend/tests/test_ik0438_0443_course_draft_context.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場でのコース内容の再生成（件数を抑えたことで必要な主張が外れていないか）
      - 参照一覧の指紋（draft_reference_key）が変わるので、既存の生成の注意点は次回の再生成で一度だけ持ち越されない（IK-0440 の規則どおり）
related: [IK-0429, IK-0438, IK-0440, IK-0443, IK-0454]
view_of: []
history: []
---

## 課題

主張の本文を短く切ったため兄弟の主張が同じ文になった。

## 発見の観点

参照一覧で別 ID が同じ本文になっている組を数えた。

## 解決の観点

本文の長さを保ち、件数で予算を守る。同じ本文は重複除去する。

## 一般化

予算は項目の数で守り、項目を区別する中身は切らない。
