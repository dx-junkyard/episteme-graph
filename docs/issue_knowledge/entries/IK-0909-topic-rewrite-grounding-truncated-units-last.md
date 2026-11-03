---
id: IK-0909
title: "トピックの AI 書き換えは根拠の辞書を JSON にしてから先頭 8000 字で切るため、IK-0874 で末尾に足した学ぶ単位の中身（と後ろのキー）が、コース内容生成済みのトピックでは切り落とされる疑いがある。根拠には解析の失敗状態の文（semantic_kind「Equation semantics could not be inferred.」）も入る"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/learning_units_design.md"
feature_context:
  realizing: "教員が学ぶ単位を根拠にトピックの授業用ドラフトを AI で書き換える"
  layers: [lecture_studio, learning_units, course_builder]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 処理軸 input_handling: backend/api/routes/lecture_studio/topics.py 749〜785 行は evidence 辞書（summary / source_excerpt / evidence_links / content_blocks / … / units の順）を json.dumps(indent=2) した文字列を [:8000] で切る。units は最後のキーなので、前のキー（主張の本文を持つ evidence_links・content_blocks）で 8000 字を超えるトピックでは切り落とされ、JSON も途中で壊れる。接続軸 information（medium）: IK-0874 で足した単位の中身が LLM に届かない。実入力の大きさは砂場で測っていないので cause_status は hypothesis。書き換えの注意点に「（式の）意味の推定もできていません」と出たのは content_blocks の semantic_kind に失敗状態の文が入っているため（IK-0873 で名前・説明からは外したが、プロンプトの材料からは外していない）。
generalization:
  level: repo_pattern
  general_form: "複数の材料を 1 つの文字列に直列化してから文字数で頭から切るため、後から末尾に足した材料ほど先に落ち、何が落ちたかも記録されない"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-02 の書き換え（t0・学ぶ単位 3 つ＝JCMT POL-2 / HARP 13CO の観測を含む）で、AI が注意点に「偏光が磁場の向きを…は原文抜粋の範囲では明示されておらず一般的な知識に基づく補足」と書いた。単位の中身が届いていれば根拠になった内容。プロンプトの組み立てを追って切り詰めを確認した。run: uxsim/runs/c-astro-r25-studio-r2/20261023T021041Z-70961（seq11）。
resolution:
  perspective: [order_and_budget, carry_through]
  note: "根拠の辞書を直列化してから頭で切るのをやめ、compact_rewrite_evidence が直列化の前にキーごとに予算を配る（学ぶ単位を先頭に必ず残す・リストは項目単位・文字列は文字単位・JSON を壊さない）。解析の失敗状態の文（is_uninformative_equation_summary に当たるもの）は材料から外す。省いた材料は応答の grounding_trimmed と日本語の grounding_notice で言う。 第 3 周（2026-10-23）の追記: 予算はリスト・ブロックを丸ごと落とさず項目単位で公平に配る（evidence_links / content_blocks / linked_component_ids が 1 つは残る。式の一覧の塊の中身も 1 つは残す）。"
  landed_in:
    - "backend/core/lecture_studio_state.py"
    - "backend/api/routes/lecture_studio/topics.py"
    - "backend/tests/test_lecture_studio_round25_r3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の固定コースの t0 で実際に予算を超えていたかは測っていない（合成した大きな根拠でプロンプトに学ぶ単位が残ることを確かめた）"
      - "学ぶ単位を材料にした書き換えの質は実機で見ていない（第 3 周 te-04）"
related: [IK-0874, IK-0873]
view_of: []
history:
  - date: '2026-10-23'
    field: resolution
    from: 第 2 周の是正
    to: 第 3 周の追記
    reason: "第 3 周（2026-10-23）の追記: 予算はリスト・ブロックを丸ごと落とさず項目単位で公平に配る（evidence_links / content_blocks / linked_component_ids が 1 つは残る。式の一覧の塊の中身も 1 つは残す）。"
---

## 課題

症状: 学ぶ単位を選んだトピックで書き換えても、AI が単位の内容を根拠として扱わない（根拠に無い一般知識の補足と書く）。

原因（仮説）: 根拠の辞書を直列化した後に文字数で切るので、末尾の units が落ちる。

## 解決の観点

解決（2026-10-23・第 25 周 面 D 第 2 波）: 根拠の辞書を直列化してから頭で切るのをやめ、compact_rewrite_evidence が直列化の前にキーごとに予算を配る（学ぶ単位を先頭に必ず残す・リストは項目単位・文字列は文字単位・JSON を壊さない）。解析の失敗状態の文（is_uninformative_equation_summary に当たるもの）は材料から外す。省いた材料は応答の grounding_trimmed と日本語の grounding_notice で言う。

当初の是正案（記録）:

- 直列化の前に各キーへ上限を配る（主張は本文を短く・件数上限、content_blocks は表示用の要約だけ）。units は前に置くか必ず残す。
- 切り落としたキーがあれば `stage` 相当の記録（応答の notes など）に事実として残す。
- content_blocks の semantic_kind が is_uninformative_equation_summary に当たるものはプロンプトの材料から外す（IK-0873 の正本を再利用）。
- 確かめ方: 6be229e1 の t0 で組み立てたプロンプトに units の各 label が含まれ、JSON として読めることをテストで固定する。

## 発見の観点

第 25 周 面 D 第 2 周（原稿スタジオ → 学生の受講）の通し受講で、ペルソナの観察と HTTP の生応答・製品コードを突き合わせた。

## 一般化

直列化してから切らない。確かめ方は、材料の多いトピックで組み立てたプロンプトを JSON として読み、末尾の材料が残っていることを見ること。

第 3 周の再演: 一部残存（学ぶ単位は残り、知らせ「学ぶ単位は省いていません」も出た = 末尾の単位が落ちる根は解消。ただし core/lecture_studio_state.py compact_rewrite_evidence（450 行）はキーの順に先着で予算を配り、リストの最初の項目が残りの予算に入らないとそのキーを丸ごと落とすため、te-04 の自分のコースでは「省いたもの: 根拠リンク・式・部品の一覧」と式の材料が全部落ち、下書きが埋め込む 4 式のうち AI は eq_ne_density の意味を「evidence_links に内容が無い」と書けなかった。下書きが埋め込んでいる式・図を優先して残す配分になっていない。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773 seq8）。
