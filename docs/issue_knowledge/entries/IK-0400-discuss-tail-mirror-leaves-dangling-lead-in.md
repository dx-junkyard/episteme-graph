---
id: IK-0400
title: "discuss の回答で鏡（〔鏡〕…〔/鏡〕）が前置き「次に、あなたの予想についてです。」の後ろに出ると、鏡は回答の先頭の枠へ移るのに前置きだけが本文の末尾に残り、回答が途中で切れたように読める"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が論文と議論し、AI が学習者の予想を言い直して確かめる（discuss の鏡面化）
  layers: [discuss, frontend_learning_ui]
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
    本文は失われていなかった。第 8 周の transcript（st-06 seq 32 / 68）では、応答 JSON の mirror に鏡文が入って届いており
    （ハーネスは body.mirror を出典行の後ろに「〔鏡〕…」として投影する）、本文は前置き「次に、あなたの予想についてです。」で
    終わっていた。LLM がプロンプトの「冒頭に〔鏡〕」に反して鏡を末尾に置き、extract_mirror が鏡の位置を何も残さずに取り除いた
    （処理軸 logic）。鏡は学習画面では回答の先頭の「AIによる言い直し」枠に出るので、前置きの先が消えたように見える。さらに
    会話履歴に鏡文は含めない（EX-3b ④）ため、次のターンの AI は自分の前置きの先が「途切れた」と誤認して詫びた（seq 33
    「途切れが2回続いたこと、失礼しました」）。「鏡がどこへ移ったか」という情報が本文にも履歴にも運ばれなかった（接続軸
    information。medium: 学習画面の枠の配置と鏡の取り出しの両方が関わる）。
generalization:
  level: repo_pattern
  general_form: 本文の一部を構造化フィールドへ抜き出すとき、抜いた位置に何も残さないと、残った本文の前後が不自然につながり、本文だけを読む次段（人・履歴・次の LLM）が欠落と誤認する
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, trace_walk]
  note: 同じ終わり方が 2 回続いた学生の報告を、transcript の応答 JSON（本文と mirror）で切り分けた。
resolution:
  perspective: [single_point_fix]
  note: >-
    鏡が本文の先頭に無い（前置きの後ろ・末尾・途中に出た）ときは、取り出した位置に事実文 MIRROR_MOVED_NOTE
    「（言い直しは、この回答の「AIによる言い直し」の枠に示しています。）」を残す。鏡の後ろの本文はそのまま続け、鏡文の中身は
    写さない（窓の外へ持ち出さない）。先頭の鏡・逐語検査に落ちた鏡は従来どおり（事実文を残さない）。枠の名前は app.js の
    鏡ラベルと同じ語であることをテストで固定。LLM が鏡を末尾に置くこと自体（プロンプトの「冒頭に」の不遵守）は直していない
    — プロンプトの正本は routes/learning.py の _get_discuss_system_prompt（別担当）。
  landed_in:
    - backend/core/discuss/mirroring.py
    - backend/tests/test_wave6b_mirror_lecture_recon.py
    - backend/api/routes/learning.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での再演（事実文が残った本文を次のターンの AI が「途切れ」と誤認しないか）
      - 鏡の後ろに本文が続いていて、それが失われた回は第 8 周の transcript には見当たらなかった（2 例とも鏡が回答の最後だった）
related: [IK-0403]
view_of: []
history:
  - date: 2026-09-28
    field: landed_in
    from: 取り出し側の事実文のみ
    to: 取り出し側の事実文 + プロンプトの配置規則
    reason: >-
      routes/learning.py _get_discuss_system_prompt のルール2に「〔鏡〕…〔/鏡〕 は返答の先頭に置く・鏡の前に前置き文や
      予告文を書かない・鏡の後に本文を続ける（鏡で返答を終えない）・ルール4で先に質問へ答える場合だけ答えの直後に置く」を
      足した（既存の契約フレーズは不変。実 LLM が守るかは未確認 — 取り出し側の事実文は残す）
---

## 課題

鏡が末尾にあると、取り出したあとに前置きだけが残り、回答が切れたように読める。

## 発見の観点

学生 2 名の「途切れた」という報告を transcript の応答 JSON で切り分け、本文の欠落ではないことを確かめた。

## 解決の観点

抜き出した位置に在処の事実文を残す。

## 一般化

本文から抜き出すときは、抜いた跡を本文に残す。
