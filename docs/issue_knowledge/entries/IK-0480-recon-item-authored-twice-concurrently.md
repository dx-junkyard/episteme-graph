---
id: IK-0480
title: "同じ主張に再構成の問いが 2 件作られた（主張の承認ごとに起動するオーサリングが同じ文書で並走し、『item がまだ無い』の確認を候補の読み出し時にしかしていなかった）"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
  - docs/features/reconstruction_loop_design.md §4.2
feature_context:
  realizing: "教員が承認した主張 1 件につき、再構成の問いを 1 件だけ作る"
  layers: [reconstruction_r]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [none]
    governance: [ordering]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    統制軸: 主張を 1 件承認するたびにその文書のオーサリングが別スレッドで起動し、承認は数十ミリ秒おきに続いた。
    各スレッドは「非 retired の item が無い主張」を先に読んでから LLM を呼び、保存時には確認し直さないので、
    先行スレッドの保存より前に読んだ後続スレッドが同じ主張に 2 件目を作った（ordering）。砂場では live の主張は
    1 行だけで、問いの行が 2 つ・監査行が 2 つあった。処理軸: 同じ本文・親子の主張を 1 つとして扱う選別が無かった（logic）。
generalization:
  level: general
  general_form: "書く直前ではなく読んだ時点で「まだ無い」を確かめ、並走する実行が同じ対象に重複して書く"
pattern: gate-position-wrong
discovery:
  perspective: [data_inspection]
  note: "砂場の問いの行・主張の live 行・監査行の作成時刻を並べ、同じ主張 ID に 2 行あること、承認の監査行が短い間隔で続いていることを見た。"
resolution:
  perspective: [order_and_budget, single_point_fix]
  note: >-
    同じ文書のオーサリングをプロセス内で直列化し（後続は先行の保存後に候補を読み直すので LLM も呼ばない）、
    INSERT は主張単位の advisory lock 下で非 retired の問いの不在を再確認する（複数プロセスでも 1 件）。
    同じ本文の主張と、親または子が問いを持つ主張は対象から外し、候補どうしでは atomic child を先に採る。
  landed_in:
    - backend/core/reconstruction/worker.py
    - backend/core/reconstruction/claim_context.py
    - backend/tests/test_ik0479_0482_recon_authoring_inputs.py
  verification:
    methods: [guardrail]
    unverified:
      - "砂場での再演（承認を連続させても問いが重複しないことを実 DB で確かめていない。並走はテストの疑似 session で確かめた）"
      - "砂場に既にある重複した問いは残したまま（行は消さない。retire は教員の監査操作）"
related: [IK-0479, IK-0482]
view_of: []
history: []
---

## 課題

同じ主張に再構成の問いが 2 件作られた（主張の承認ごとに起動するオーサリングが同じ文書で並走し、『item がまだ無い』の確認を候補の読み出し時にしかしていなかった）

## 発見の観点

砂場の問いの行・主張の live 行・監査行の作成時刻を並べ、同じ主張 ID に 2 行あること、承認の監査行が短い間隔で続いていることを見た。

## 解決の観点

同じ文書のオーサリングをプロセス内で直列化し、INSERT は主張単位の advisory lock 下で問いの不在を再確認する。同じ本文の主張と、親または子が問いを持つ主張は対象から外し、atomic child を先に採る。

## 一般化

書く直前ではなく読んだ時点で「まだ無い」を確かめ、並走する実行が同じ対象に重複して書く。
