---
id: IK-0475
title: "前の往復で引用したチャンクが次の往復のコンテキストに入らず、モデルが自分の前の [出典2] と食い違うことを言った（番号は同じ意味でも本文が提示されない）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: "会話の前の回答の出典番号を、あとの往復でもモデルが本文付きで参照できる"
  layers: [rag_chat]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: IK-0444 以降、番号の対応（citation_map）は会話で保たれるが、routes/learning.py の文脈に入るのは当該往復の検索結果だけで、直前の回答が
    引用したチャンクの本文はモデルに渡っていなかった（information）。履歴の assistant ターンには sources（index / chunk_id）が焼き込まれており、
    材料は手元にあった。処理軸 none: 番号の採番・検索の判定そのものは正しい。
generalization:
  level: general
  general_form: "識別子の対応だけを保ち、識別子が指す本文を後続の処理に渡していない"
pattern: last-mile-missing
discovery:
  perspective: [data_inspection]
  note: "odd 側の頭脳のメモ（seq 23 / 77 / 89）と even 側 00018 の history の引用番号を突き合わせた。"
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    直前の assistant ターンが本文で [出典N] と引用し、かつそのターンの sources にある出典のうち、今回の検索に無いものを最大3件・1件1200字まで、元の番号の
    まま文脈へ戻す（ラベル「（前の回答で引用した箇所）」）。番号が採番器の対応表と一致しないものは戻さない。読み出しは services.get_chunks_for_prompt で、
    当該往復の allowed_document_ids を SQL 内で強制する（範囲を広げない・空集合は SQL 非発行）。戻したチャンクは当該往復で採用した出典（cited_sources）に
    入るので tier・content_grounding・未踏ガードの判定に数え、学習者に見せる一覧には本文で引用したときだけ載る（IK-0494）。予想を引き出す往復（elicit）
    では戻さない。
  landed_in:
    - backend/api/routes/learning.py
    - backend/api/services.py
    - backend/tests/test_ik0492_0496_chat_turn_fixes.py
    - backend/tests/test_ik0432_0437_chat_turn_fixes.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（モデルが前の [出典2] と食い違わなくなったかは見ていない）"
      - "実 DB での get_chunks_for_prompt の SQL（uuid 配列の CAST）の実行"
      - "戻すのは直前の1往復の引用だけ（それより前の往復の引用は戻さない）"
related: [IK-0444, IK-0466, IK-0494]
view_of: []
history: []
---

## 課題

前の往復で引用したチャンクが次の往復のコンテキストに入らず、モデルが自分の前の [出典2] と食い違うことを言った（番号は同じ意味でも本文が提示されない）

## 発見の観点

odd 側の頭脳のメモ（seq 23 / 77 / 89）と even 側 00018 の history の引用番号を突き合わせた。

## 解決の観点

直前の回答が引用したチャンクのうち今回の検索に無いものを、元の番号・可視性を保ったまま最大3件文脈へ戻した。戻したチャンクは当該往復の採用出典として出所の分類・未踏ガードに数え、見せる一覧には引用したときだけ載せる。

## 一般化

識別子の対応だけを保ち、識別子が指す本文を後続の処理に渡していない。
