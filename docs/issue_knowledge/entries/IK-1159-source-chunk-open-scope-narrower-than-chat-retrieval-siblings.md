---
id: IK-1159
title: "出典本文を開く経路（source-chunk）の許可範囲がコースの教材文書だけで、学習チャットが検索し出典に出す同じ論文の別の版（TeX 版・PDF 版）のチャンクを含まないため、回答の出典を開くと 404 になる"
status: resolved
recorded_at: 2026-11-03
resolved_at: 2026-11-03
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/backend/rag-chat.md"
feature_context:
  realizing: "学生が学習チャットの回答に付いた出典を開き、式や数値の出所を論文の本文で確かめる"
  layers: [rag_chat, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定（コードの読み）: 学習チャットの検索（backend/api/routes/learning.py の②本人が閲覧できる document のチャンク）は IK-0987 以降
    paper_version_siblings で同じ論文の別の版も対象にし、その版のチャンクを出典に出す。一方 get_source_chunk_route の対象の判定
    （P0 オブジェクトスコープ是正）は list_course_source_document_ids(course_data) だけを許可集合にしていた。接続軸 condition（出典を出す段の
    対象範囲が、出典を開く段の判定に伝わらない）。構造軸 responsibility（「このコースで見てよい文書」の判定が検索側と開く側の 2 箇所に
    別々に置かれ、片方だけ広がった）。処理軸は判定そのものの誤りではないので none。統制軸は同時期の変更の照合手続の欠落とも読めるが、
    原因を変えずに再発を防ぐには判定の置き場所を変えるほうが効くので none とし確信度 medium。
generalization:
  level: repo_pattern
  general_form: "結果を出す段と、その結果を開く段が対象範囲を別々に判定し、片方だけ範囲を広げたため、出した結果の一部が開けない"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    ペルソナ通し受講（campaign c-sandbox-p01-eos・ペルソナ st-11-m1-neutron-first-sim・経路 s-ask-where-from・コース 1bad7d9f・対象版 5fd05d3c）。
    教材は PINN 論文の文書 ce667c63、回答の出典 1 は別の版 7ef9e487（TeX 再取り込み版）の chunk 72e3c290。手 12 で
    GET /api/learning/courses/1bad7d9f/source-chunk/72e3c290-… が 404、審判 C blocked（finding f-20261103T040912Z-81314-0006）。
    ペルソナは「本文を開けず、TOV に必要な式の出所をこの画面から確認できませんでした」で止まった。チャットの検索範囲と出典を開く経路の許可集合を突き合わせた。
    run: uxsim/runs/c-sandbox-p01-eos/20261103T040912Z-81314/report.md・transcript.jsonl。
resolution:
  perspective: [carry_through]
  note: >-
    get_source_chunk_route の許可集合に、course sources の paper_version_siblings(course_ids, allowed_document_ids=list_visible_document_ids(user_id))
    の結果を和集合で足した。本人の全域可視集合そのものは混ぜない（コース外の文書は開けないまま）。検索側と同じ兄弟の解決関数を使い、
    範囲を出典を出す段と揃えた。判定を 1 箇所へ寄せる統合は見送り（今回は兄弟版の運搬だけ）。
  landed_in:
    - "backend/api/routes/learning.py"
    - "backend/tests/test_source_chunk_visibility.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での実機確認（コース 1bad7d9f の出典 72e3c290 が開けること）は E-0003 に記録"
      - "修正は未コミット（コミット番号は後で付く）"
related: [IK-0987, IK-1117]
view_of: []
history: []
---

## 課題

症状: 学習チャットの回答に付いた出典を開くと 404 になり、学生は式の出所を本文で確かめられない。

原因: チャットの検索は同じ論文の別の版（IK-0987 で足した paper_version_siblings）のチャンクも出典に出すが、出典本文を開く経路の許可集合は
コースの教材文書だけで、別の版を含まない。「コースで見てよい文書」の判定が 2 箇所にあり、検索側だけ広がった。

## 発見の観点

ペルソナの通し受講で出典を開く手が 404 で止まった（reproduction）。出典の chunk の document が教材文書と別の版であることを確かめ、
検索の範囲と開く経路の許可集合を辿って突き合わせた（trace_walk）。

## 解決の観点

開く経路に、検索側と同じ兄弟版の解決（本人が閲覧できる範囲に限る）を運んだ（carry_through）。全域の可視集合を混ぜる案はコース外の
文書まで開けるので採らない。`backend/tests/test_source_chunk_visibility.py` に TestGetSourceChunkRouteSiblingVersions（2 件）を足し、
静的検査を更新。tests/test_source_chunk_visibility.py と tests/test_object_scope_authorization.py が pass。実機確認は E-0003 に記録。

## 一般化

出典・リンク・プレビューなど「出した結果を後で開く」経路では、出す段の対象範囲を広げたら開く段も同じ範囲になっているかを確かめる。
確かめ方: 別の版から出典が出る質問を投げ、回答のすべての出典が開けるかを見る。
