---
id: IK-0413
title: "学習者の「論文の位置づけ」（GET /api/learning/courses/{id}/landscape）と論文の海の分野一覧で、表示名が登録されていない分野（particle_physics）が内部キーのまま表示名として出る"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がコースの論文が分野の地図のどこに置かれているかを読む（知識ランドスケープ・コーパス回遊層）
  layers: [knowledge_landscape, corpus_roaming]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: _domain_facts / list_corpus_domains が表示名の空を domain_key で埋めていた（wording）。構造軸: 表示名の正本（atlas_domain_meta）はあり、カートリッジ由来の分野に行が無いだけ（none。medium: particle_physics に名前を入れる経路が教員操作にしか無い点は運用側）。接続軸: 落ちている値は無い（none）。確認: 第 8 周の学生が「particle_physics だけ英語のまま」と記録。同じ観測の「出所ラベルが無い」「件数が出ている」「document UUID が出ている」はコードを確認した結果、設計どおり（provenance_label は各配置に既にある / corpus.source_document_count は LS8 が表示を要求する事実行の材料 / document_id は表示ラベルではなく結合キー）で課題として成立しない。未配置の論文は unplaced_documents（題名）と学習画面の事実文で既に出ており、題名が arXiv 番号なのは IK-0419。
generalization:
  level: repo_pattern
  general_form: 表示名が無いとき内部キーで穴埋めし、利用者には内部キーが名前として見える
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: 学生ペルソナが位置づけと分野一覧を読み、英語の内部キーを名前として受け取った。
resolution:
  perspective: [single_point_fix]
  note: >-
    学習者投影だけ、表示名が空または内部キーそのものの分野を atlas_state.learner_domain_label で「名前が登録されていない分野」（label_vocab.ATLAS_DOMAIN_UNNAMED_LABEL）に倒した。domain_key は取得キーとして残す。教員側の一覧は従来どおり。出所ラベル・コーパス件数・document_id は設計どおりとして変えていない。particle_physics の表示名を atlas_domain_meta に登録するのは教員（または同梱シード）の操作で、この課題の範囲外。
  landed_in:
    - backend/api/routes/landscape.py
    - backend/core/corpus_view.py
    - backend/core/atlas_state.py
    - backend/tests/test_landscape_api.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演
      - particle_physics の表示名の登録（atlas_domain_meta への行・または cartridge 由来分野のメタシード）は未対応
related: [IK-0412, IK-0419]
view_of: []
history: []
---

## 課題

名前の無い分野が内部キーで表示される。

## 発見の観点

実ペルソナの位置づけの読み（第 8 周）。

## 解決の観点

学習者投影で内部キーを名前の代わりに使わない。

## 一般化

表示名の欠落を内部キーで埋めると、内部キーが名前として流出する。
