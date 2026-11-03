---
id: IK-0382
title: "学習チャットは表示中のトピック教材を毎ターン文脈に注入したことだけで出所を「教材から回答」（course_material）・根拠の格を原典にしていたため、教材と無関係な問い（別トピックの概念・一般知識、楽屋の初歩的な質問）で検索が 1 件も当たらなくても「教材に基づく」と表示された"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者がチャットの回答の出所（教材 / 別の資料 / AI の一般知識）を正しく読み分ける
  layers: [rag_chat, structure_descent]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    処理軸で、出所分類の条件式が「トピック教材を注入した（has_topic_material）」を根拠の有無として
    使っていた（routes/learning.py の RAG 本経路・出所分類ブロック。注入は問いを問わず毎ターン起きる）。
    接続軸で、「教材から回答」という表示の意味（回答が教材に基づく）と、判定に使った事実（教材を
    プロンプトに入れた）がずれていた（meaning。medium: 表示ラベルの意味の取り違えとも読める）。
    構造軸は none（出所の 3 値と cited_sources の表現は足りていた。medium: トピック教材が
    番号付き出典にならず回答側から引用できない表現は残るが、今回の誤表示の原因ではない）。統制軸は none。
    確認: 第 8 周の transcript で、Cep B のトピックでの「外部からのフィードバック」「銀河そのものの大きさも
    伸びるか」と楽屋の「ジーンズ質量とは」がいずれも sources 0 件・content_grounding=course_material。
    前提ゲートの往復（逆質問）は content_grounding=None で、出典ゼロの course_material ではなかった
    （lead の観測のうちこの 1 件は仮説が当たらなかった）。出典 8 件中の一部だけを [出典N] で引用する
    回答が course_material になるのは契約どおり（cited_sources は「文脈に採用した根拠」で、本文の引用
    有無ではない。音声・casual は引用マーカーを書かない指示なので引用ベースにはしない）。
generalization:
  level: repo_pattern
  general_form: 出所・根拠の表示を「材料を入力に入れた」ことから決め、その材料が出力に関わったかを確かめない
pattern: extent-decided-by-proxy-signal
discovery:
  perspective: [reproduction, data_inspection]
  note: ペルソナ通し受講 第 8 周の学生 4 名の transcript（sources 件数と content_grounding の突き合わせ）。
resolution:
  perspective: [single_point_fix]
  note: >-
    教材の注入（プロンプト）は変えず、出所分類と tier 下限だけを決定論の判定
    _topic_material_engages_message に従わせた。根拠に数えるのは「教材の箇所・要素を明示している /
    問いに内容語が無い（指示語だけ）/ 問いの内容語の過半が教材本文に逐語で現れる」のどれかのときだけ。
    満たさず検索も当たらなければ model_generated（tier は out_of_source のまま = 未踏ガードと注意書きが
    効く）。楽屋も同じ経路。前提説明の 3 段解決（① 同コース topic に一致）は前提名の完全一致なので対象外。
  landed_in:
    - backend/api/routes/learning.py
    - backend/tests/test_ik0382_topic_material_grounding.py
    - backend/tests/test_content_grounding.py
    - docs/backend/rag-chat.md
  verification:
    methods: [guardrail]
    unverified:
      - 砂場・ブラウザでの再操作（第 8 周の問いで出所バッジが「AIの一般知識」になるか）
      - 問いが英語で教材が日本語のとき（ASCII は略号しか内容語に数えないため、多くは従来どおり教材扱いに残る）
      - 教材に関わる問いが言い換えで逐語一致しないとき（model_generated に倒れ、未踏ガードが付く安全側の誤り）
related: [IK-0381, IK-0378]
view_of: []
history: []
---

## 課題

出典ゼロの一般知識の回答に「教材から回答」と出る。

## 発見の観点

transcript の sources 件数と出所分類の突き合わせ。

## 解決の観点

注入したことではなく、教材が問いに関わるかで出所を決める。

## 一般化

材料を入れたことを、材料に基づいたことと取り違える。
