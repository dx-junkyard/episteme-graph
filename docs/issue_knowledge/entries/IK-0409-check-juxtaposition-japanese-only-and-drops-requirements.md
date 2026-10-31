---
id: IK-0409
title: "英語で答えた受講者の確認問題の並置が日本語だけで返り、要件 5 つのうち 3 つにしか観点が付かなかった（観点の上限が固定 3 件で、プロンプトも回答の言語を指定していなかった）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 確認問題の回答を、出題の要件ごとに学習者の言語で並置する
  layers: [rag_chat]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [information]
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
    処理軸: 観点の正規化が上限 MAX_OBSERVATIONS=3 で打ち切り、要件が 4
    つ以上の出題で残りを落としていた。プロンプトは日本語の指示文だけで回答の言語を指定していなかった（logic）。接続軸: LLM
    が返さなかった要件は並置から黙って消え、「観点が得られなかった」事実も残らなかった（information）。構造軸は none（観点の表現は要件ごとに足りている）。統制軸は none。確認: 第 8
    周の英語の留学生ペルソナの並置が日本語・観点 3 件。
generalization:
  level: repo_pattern
  general_form: 出力の件数上限と言語を入力の実態（要件の数・利用者の言語）と無関係に固定する
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: 第 8 周で英語の回答に対する並置の言語と観点数を要件と突き合わせた。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: >-
    観点の上限を要件の数まで広げ（check_review.parsed_observations の limit）、LLM が返さなかった要件は fill_missing_requirements が
    unclear
    +「この要素についての観点は得られませんでした。」で出題の順に補う（判定を捏造しない）。プロンプトに「受講者の回答と同じ言語で」と、ラテン文字が大半の回答には「英語で書く」の1行（_answer_language_line・決定論）を足した。英語の観点にも判定語
    denylist（correct / wrong / score / grade 等）を掛ける。
  landed_in:
    - backend/core/check_review.py
    - backend/api/routes/learning.py
    - backend/tests/test_learning_chat_wave6_turns.py
    - docs/features/learning.md
  verification:
    methods: [guardrail]
    unverified:
      - 並置の固定文（「あなたは「…」と述べました。」等）と UI のラベルは日本語のまま（英語利用者への日本語固定部分は §17.7 の i18n の構造課題として起票せず残した観測）
      - 実 LLM が英語で返すか（砂場の再演）
      - 英語の判定語 denylist が内容の文（score function 等）の観点文を落とす誤り
related: []
view_of: []
history: []
---

## 課題

英語の回答に日本語だけ・要件の一部だけの並置が返る。

## 発見の観点

言語と観点数を要件と突き合わせた。

## 解決の観点

全要件に観点を付け（無ければ事実で補う）、回答の言語で書かせる。

## 一般化

件数上限と言語を入力の実態と無関係に固定する型。
