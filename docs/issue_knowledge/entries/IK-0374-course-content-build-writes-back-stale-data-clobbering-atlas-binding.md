---
id: IK-0374
title: "コース登録直後に走るコース内容の生成が、開始時点の data のコピーを完了時に書き戻すため、その間に保存した地図の紐付け（cartridge_id・topics[].atlas_node_id）が消え、学習者全員の分野の地図が 404 になる"
status: resolved
recorded_at: 2026-09-27
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 教員がコースを登録し、内容生成の間に地図の紐付け・公開を行う
  layers: [course_builder, field_atlas_s]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [ordering]
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
    接続軸: 内容生成は開始時に読んだ data（版 A）を完了時にそのまま書き戻し、その間に別経路が書いた版 B
    （地図の紐付け）を失う（version）。統制軸: 登録 → 紐付け → 公開 の教員の操作順と、非同期の生成完了の順序が
    統制されていない（ordering）。確認: 砂場 DB で course fcae61c4 は 14:08:39 登録・14:09:16 紐付け保存
    （監査 atlas_binding: bindings_applied 4・応答 cartridge_id astrophysics）・14:22:04 生成完了
    （updated_topics 22）の後、data.cartridge_id が NULL・atlas_node_id 付きトピック 0。学生 4 名の
    GET /api/atlas が全員 404「atlas skeleton not available for this course」。
generalization:
  level: general
  general_form: 長い非同期処理が開始時の全体コピーを完了時に書き戻し、その間の他経路の更新を上書きする
pattern: full-update-clobbers-unrelated-fields
discovery:
  perspective: [reproduction, data_inspection]
  note: 教員 2 名・学生 4 名の通し受講で学生全員の地図が 404 になり、砂場 DB の監査行と data を時刻で突き合わせた。
resolution:
  perspective: [order_and_budget, representation_change]
  note: >-
    完了時の書き戻しを「行を FOR UPDATE で読み直し、生成が持つキーだけを併合、トピックは id で突合」に変えた。開始時に processing、失敗時に failed を jsonb_set で記帳する。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/tests/test_ik0374_content_writeback_merge.py
  verification:
    methods: [reproduction_rerun, docker_e2e, guardrail]
    unverified:
      - 逆向きの競合（atlas-binding / PUT courses が生成完了後に古い data で書く）
      - 途中で落ちたプロセスが残す processing の掃除
related: []
view_of: []
history: []
---

## 課題

内容生成の完了時の書き戻しが、その間に保存された地図の紐付けを消す。学習者の地図は 404 になる。

## 発見の観点

学生 4 名の同じ症状（地図 404）から、監査行と data の時刻を突き合わせて原因に遡った。

## 解決の観点

未解決。書き戻しを併合にする。

## 一般化

非同期処理の全体コピー書き戻し（full-update-clobbers-unrelated-fields）。
