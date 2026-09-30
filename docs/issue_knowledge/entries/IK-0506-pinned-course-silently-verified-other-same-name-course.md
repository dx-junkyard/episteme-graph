---
id: IK-0506
title: 検証 campaign が course_id を固定していても、痕跡リセットで受講が外れた学生ペルソナが同名の別コース（再生成していない方）を受講し、検証週が丸ごと別コースを読んだのに何の印も残らなかった
status: resolved
recorded_at: 2026-09-29
resolved_at: 2026-09-29
sources:
  - docs/architecture/persona_enactment_testing_design.md §17.10
feature_context:
  realizing: ペルソナ通し受講テストで、是正したコースを固定して砂場で再現検証する（第 12 周 c-verify-wave456）
  layers: [cycle_verification]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [target, condition]
    governance: [review]
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
    接続軸: campaign の `course_id: 1bad7d9f` は runner の `prefetch_courses`（uxsim/runner/actions_exec.py）で
    「受講中なら session.course_id に置く／受講可能なら scratch.default_course_id に候補として置くだけ」に留まる。
    `reset_learner_state` の後は受講中でないので候補にしかならず、固定という条件がペルソナの選択（一覧に同じ題名が
    2 行 = 6be229e1 と 1bad7d9f）まで届かない（condition）。ペルソナは seq 4 で `learning.course.enroll
    {'course_id': '6be229e1'}` を選び、以降の教材・チャットはすべて再生成していない 6be229e1 を読んだ（target。
    run 20260928T125344Z-98728 の transcript で確認）。統制軸: 固定したコースと実際に読んだコースを突き合わせる
    検査が runner・meta・審判・報告のどこにも無く、「`![[source:topic_summary]]` がまだ残っている」という報告が
    是正の失敗として読める状態だった（review）。処理軸: 個々の処理は仕様どおり（none）。構造軸: 題名が一意でない
    こと自体は製品の許す状態で、ハーネスの表現の欠陥ではないと判断した（none・medium）。
generalization:
  level: repo_pattern
  general_form: 検証対象を固定したつもりでも、固定が「既定の候補」に留まり操作主体が別の同名対象を選べるなら、検証は黙って別の対象を見る。固定と実際の対象を突き合わせる検査が無いと、結果は検証の失敗ではなく是正の失敗として読まれる
pattern: same-name-different-referents
discovery:
  perspective: [data_inspection, trace_walk]
  note: >-
    第 12 周でペルソナが「是正済みのはずの埋め込みがまだ出ている」と報告したのを受け、transcript の seq 2（一覧に同名 2 行・
    固定コースは受講可能な候補止まり）→ seq 4（別コースを受講）を辿って判明した。
resolution:
  perspective: [explicit_contract, carry_through]
  note: >-
    ペルソナの選択は差し替えない（同名が並ぶこと自体が画面の観測）。代わりに①砂場準備 `uxsim/sandbox/pin_course.py`
    （既定 dry-run・`--yes` で製品 API `PUT /api/admin/courses/{id}/visibility` により同名の別の公開コースを private に。
    所有者でログインして呼ぶ・DB は所有者の SELECT のみ）で取り違えの余地を消し、②runner が固定と異なる course_id の
    受講・表示を `pinned_course_mismatch:` の runner_note・`meta.json` の `pinned_course_mismatch`（途中停止でも書く）・
    審判 A（dialogue f、仮説の頭に「ハーネス:」・層 cycle_verification）・報告の注意行に残すようにした（検証が
    対象を見ていないことを明示する契約）。次の周のスクリプト `uxsim/runs/run13_verify.sh` の冒頭に準備手順を置いた。
  landed_in:
    - uxsim/sandbox/pin_course.py
    - uxsim/runner/actions_exec.py
    - uxsim/runner/api.py
    - uxsim/schema.py
    - uxsim/oracles/dialogue.py
    - uxsim/report/campaign_report.py
    - uxsim/tests/test_pinned_course_and_embeds.py
  verification:
    methods: [guardrail]
    unverified:
      - 砂場での pin_course.py の実行（砂場が停止中のため dry-run も未実施）
      - 第 13 周の再走で meta.pinned_course_mismatch が空になること
      - 所有者が管理者でも教員ペルソナでもないコースへの対処（スクリプトは失敗を報告して止まるだけ）
related: [IK-0507]
view_of: []
history: []
---

## 課題

第 12 周（c-astro-verify-wave456）は、第 12・13 波の是正を再生成したコース 1bad7d9f で再現する検証だった。campaign は
`course_id: 1bad7d9f` を固定していたが、runner はこれを「受講中なら使う／受講可能なら候補に置く」だけにしていた。
痕跡リセット（`reset_learner_state`）で受講が外れた学生ペルソナから見ると、一覧には同じ題名のコースが 2 行並び、
ペルソナは再生成していない 6be229e1 を受講した。以降の教材・チャットはすべて古い内容を読み、「`![[source:topic_summary]]`
がまだ残っている」等の報告は是正の失敗のように見えたが、実際は検証が別のコースを見ていた。これを示す印は
transcript・meta・審判・報告のどこにも無かった。

## 発見の観点

ペルソナの報告内容と是正の中身が食い違ったため、transcript を一覧取得から受講登録まで辿った（trace_walk）。実際の
run ログ（data_inspection）で、固定コースが「受講可能な候補」に留まり、別の同名コースが受講されたことを確かめた。

## 解決の観点

固定を強制してペルソナの選択を上書きする案は取らなかった（上書きすると画面の観測ではなくなる）。取り違えの余地は
砂場の準備側で消し（同名の別コースを製品 API で private にする）、それでも取り違えたら検証が対象を見ていないことを
runner・meta・審判・報告の 4 か所に明示する契約にした（explicit_contract）。固定という条件を最初の一覧取得から
受講・表示の各手まで運んで突き合わせる（carry_through）。

## 一般化

「対象を固定した」は、固定が既定値・候補に留まり、操作主体が同じ名前の別の対象を選べるなら成立しない。同名が
並ぶ環境（題名の重複・同名ユーザー・同名ブランチ）では、固定と実際の対象の突き合わせを検証の結果に含めないと、
検証の失敗が対象の失敗として読まれる。型は `same-name-different-referents`。
