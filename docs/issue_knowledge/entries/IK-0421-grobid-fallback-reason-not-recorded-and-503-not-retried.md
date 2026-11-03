---
id: IK-0421
title: "GROBID が失敗して PyMuPDF に縮退したとき、失敗の理由（接続失敗か・混雑の 503 か・タイムアウトか）を記録せず再試行もしないため、一括投入で 4 論文中 3 本が理由不明の縮退になり、題名・式・図の層がまるごと落ちた原因を後から辿れない"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: PDF を GROBID で TEI に変換し、失敗時は PyMuPDF へ縮退して解析を続ける
  layers: [pipeline_a]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
    governance: [budget]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: _stage_grobid_parse が例外を捕まえてログに出すだけで、artifact と stage payload には status=fallback しか残さず、
    理由（例外の型・HTTP ステータス）を落としていた（error_handling）。接続軸: 縮退の理由が後段（reference_health の欠落の
    事実文・G層・教員の再実行判断）へ運ばれない（information）。統制軸: GROBID は同時要求がプールを超えると 503 を返すが、
    再試行が無く一括投入の 2〜4 本目が落ちる（budget。砂場の 4 本は同時投入で 3 本が fallback・1 本だけ TEI 経路 —
    503 が原因かは記録が無いため未確認）。確認: 砂場 DB の grobid_parse artifact は status=fallback / tei_bytes=0 のみで
    理由が無い（IK-0420 の調査で判明）。
generalization:
  level: repo_pattern
  general_form: 非致命の縮退で例外を握りつぶし、理由を成果物に残さないため、後段の欠落の原因が再構成できない
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection]
  note: IK-0420 の原因追跡で grobid_parse artifact を読み、縮退の理由が一切残っていないことに当たった。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    orchestrator に _grobid_parse_with_retry を置き、(tei_xml, fallback_reason, attempts) を返す。requests 系の接続失敗・
    タイムアウトと HTTP 429/502/503/504 は GROBID_RETRY_DELAYS_S（5 秒・20 秒）で最大 3 回まで試し、それ以外は即縮退。
    理由は「例外の型: 先頭 200 字」（secrets を含まない）で grobid_parse artifact の fallback_reason / attempts と
    stage payload に残す。resume 経路は artifact の理由をそのまま payload に写す。組み込み ConnectionError は
    再試行しない（テストの seam を壊さない）。
  landed_in:
    - backend/core/document_pipeline/orchestrator.py
    - backend/tests/test_ik0421_grobid_retry_reason.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再解析（GROBID の 503 が実際に再試行で通ること・理由が artifact に残ること）
      - 砂場の 3 本が縮退した理由そのもの（記録が無いため、再解析するまで不明）
related: [IK-0420, IK-0366]
view_of: []
history: []
---

## 課題

GROBID の縮退が理由なしに記録され、再試行もない。

## 発見の観点

砂場 DB の artifact を読む。

## 解決の観点

理由を成果物に残し、混雑・接続失敗だけ短く再試行する。

## 一般化

非致命の縮退は理由を成果物に残す。
