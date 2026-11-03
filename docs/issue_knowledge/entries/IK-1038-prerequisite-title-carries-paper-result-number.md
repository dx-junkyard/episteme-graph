---
id: IK-1038
title: "前提の逆質問の前提名が前のトピックの題名そのままで、論文の結果の値（「R1.4 ≈ 12.85 km を得た」）を含む"
status: rejected
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22"
  - "docs/features/focus_document_design.md"
feature_context:
  realizing: "学生がトピックの前提を確かめる逆質問を読み、前提の名前で前のトピックを思い出す"
  layers: [rag_chat, course_builder]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording（確信度 low）: 課題とされたのは前提名の書き方（題名の語）で、規律の違反ではない。前提名はコースの前提トピックの題名（教員がコースビルダーで確定した題名）をそのまま使う。値は論文が報告した結果（教材の内容）で、
    数値非表示の規律（confidence・類似度・件数などシステムが導いた数値を学習者に出さない）の対象ではない。逆質問が前の往復の回答に
    連結されて保存されるのは RS5 の保存形（設計どおり）。題名を表示の側で書き換えると、サイドバー・地図・前提の表示で同じトピックの
    呼び名が食い違う（正本の分裂）ので、課題として成立しないと判定した。
generalization:
  level: instance
  general_form: "人が確定した名前に内容上の数値が含まれ、それを数値非表示の規律の違反と読む"
pattern: wording-mismatch
discovery:
  perspective: [data_inspection]
  note: >-
    製品頭脳 A 4250 が、前の往復の回答に連結された前提の逆質問の前提名に結果の数値があると記した（uxsim/runs/mailbox/req-04250.json）。
resolution:
  perspective: [deferred_decision]
  note: >-
    コード変更なし。題名に結果の値を入れない書き方はコースビルダーの題名の付け方（教員の確定）の問題で、表示の側では変えない。
    題名の付け方の指針をコースビルダーに足すかは、題名に結果の値が入るコースが他にも出てから判断する。
  landed_in: []
related: [IK-0509]
view_of: []
history: []
---

## 課題

症状: 前提の逆質問が「PINNで中性子星EOSを非パラメトリックに推定し R1.4 ≈ 12.85 km を得た（問題設定）」を前提名として示す。

## 発見の観点

製品頭脳に渡ったプロンプトの点検。

## 解決の観点

課題として成立しないと判定（rejected）。数値非表示の規律はシステムが導いた数値を対象にし、教材の内容の値は対象外。題名の正本は 1 つに保つ。

## 一般化

規律の対象の読み違え。確かめ方: 数値の出所（システムの導出か、論文・教員の内容か）を確かめてから規律を当てる。
