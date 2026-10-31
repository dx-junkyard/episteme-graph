---
id: IK-0946
title: "トピック教材がスライド 1 枚にしか割れないとき、受講画面の「読む」は区切りの剥がれていない全文をそのまま配るため、教員が入れた末尾・先頭の区切り「===」が学生の教材に生で出る（同じ教材の講義では区切りが落ちる）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/lecture_slide_sync_design.md"
feature_context:
  realizing: "学習者がトピック教材を読む表示で読み、教員の区切りの記号を見ずに本文だけを読む"
  layers: [lecture_player, frontend_learning_ui, lecture_studio]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [contract]
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
    処理軸 logic: backend/core/lecture.py topic_material_delivery_segments（1307 行）は build_topic_slides が 2 枚以上のときだけスライドの display_text（区切りを
    除いた本文）を使い、1 枚以下では whole_text（区切りを含む全文）を使う（1340〜1344 行）。split_slides は空の区画を捨てる（_split_marker_segments 515 行）ので、
    末尾・先頭・連続の区切りだけのトピックは 1 枚になり、全文の「===」が残る。講義は slides[0].display_text を使うので落ちる。接続軸 contract（medium）:
    「区画の本文は build_topic_slides のページ」という配信の契約が 1 枚の分岐でだけ守られない。構造軸 none（正本は 1 つで、分岐の中で別の値を使っている）。
    統制軸 none。
generalization:
  level: repo_pattern
  general_form: "分割で区切り記号を剥がす規則を、分割が 2 つ以上になったときの分岐でだけ通すため、1 つにしかならない入力では記号が素通しで利用者に届く"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-03 が見出し 1 行の t0 の末尾に区切りを入れて保存・公開し、st-01 が読む表示の最後に「===」を見て「これは何ですか」と書いた。講義ではスライド 1/1 で
    区切りが出なかった。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157（seq8〜10）/ uxsim/runs/c-astro-r25-studio-r3/20261023T071646Z-49888（seq6・8）。
resolution:
  perspective: [carry_through, guardrail_fix]
  note: "topic_material_delivery_segments は 1 枚にしか割れないときもスライドの本文（区切り === を剥がした display_text）を配り、全文へ落ちるのはスライドが作れないとき（教材なし・例外）だけにした。掃除を分割の分岐に閉じない。"
  landed_in:
    - "backend/core/lecture.py"
    - "backend/tests/test_lecture_reading_lecture_contract.py"
    - "backend/tests/test_ik0455_source_embed_delivery.py"
  verification:
    methods: [guardrail]
    unverified:
      - "末尾に区切りだけを足したトピックの読む表示は実画面で見ていない"
related: [IK-0914, IK-0878]
view_of: []
history: []
---

## 課題

症状: 学生の読む表示の最後に「===」が出る。同じトピックの講義では出ない。

原因: 配信の区画は「2 枚以上ならスライドの本文、1 枚なら全文」で、全文には区切りが残っている。

## 発見の観点

教員の保存 → 公開 → 学生の受講を同じ周でつないだら、教員の区切りが学生の教材に出た。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。topic_material_delivery_segments は 1 枚にしか割れないときもスライドの本文（区切り === を剥がした display_text）を配り、全文へ落ちるのはスライドが作れないとき（教材なし・例外）だけにした。（以下は起票時の見立て）1 枚の分岐でも区切りを剥がした本文を配る。

## 一般化

分割のときだけ掃除する規則は、分割されない入力で掃除されない。確かめ方は、末尾に区切りだけを足したトピックを読む表示で開き、区切りが出ないことを
見ること。
