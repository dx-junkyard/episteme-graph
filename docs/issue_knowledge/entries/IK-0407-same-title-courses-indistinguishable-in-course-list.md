---
id: IK-0407
title: "別々の教員が作った同じ題名のコースが、学習画面のコース選択で題名だけの選択肢として並び、どちらがどちらか見分けられない（一覧 DTO が見分けの材料を description しか持たない）"
status: open
recorded_at: 2026-09-28
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 学習者が受講したいコースを一覧から選ぶ
  layers: [frontend_learning_ui, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸: 選択肢の表示が題名だけで、人が見分けるための区別（作成者・出典の論文・説明）を表せない
    （representation）。一覧 DTO（LearningCourseOut）も id / title / 公開状態 / description しか持たない。
    接続軸: description は DTO にあるが、select の選択肢には title 属性（ホバー）としてしか運ばれて
    いなかった（information。medium: 説明が空のコースどうしでは運んでも区別できない）。処理・統制は none。
generalization:
  level: general
  general_form: 人向けの呼称（題名）だけで一覧を並べ、同じ呼称の別物を見分ける材料が一覧に無い
pattern: same-name-different-referents
discovery:
  perspective: [reproduction]
  note: 第 8 周の学生が、別々の教員が作った同名コース 2 件を一覧で見分けられなかった。
resolution:
  perspective: [single_point_fix, pending]
  note: >-
    フロント側は、題名が一覧の中で重複する選択肢にだけ description の冒頭（40 字まで・空白を畳む）を
    「題名 — 説明」の形で添えた（重複しない選択肢は題名だけのまま・数値は描かない）。説明が空のコース
    どうしは依然として見分けられない。残りは一覧 DTO に見分けの材料を足すこと（例: 出典の論文の題名や
    作成者の表示名）で、backend/api/routes/learning.py の list_courses（own / enrolled / public / group の
    4 つの SELECT）と backend/api/schemas.py の LearningCourseOut を持つ担当の範囲。
  landed_in:
    - frontend/public/js/app.js
    - docs/manual/student/02-student.md
    - backend/tests/test_learner_facts_wave6_ui_static.py
related: []
view_of: []
history: []
---

## 課題

同じ題名のコースがコース選択に題名だけで並び、見分けられない。

## 発見の観点

ペルソナの操作の再現で、同名コース 2 件を選び分けられないことが見えた。

## 解決の観点

画面側は重複する題名にだけ説明の冒頭を添えた。説明が空のときの区別には一覧 DTO の拡張が要る。

## 一般化

人向けの呼称だけで一覧を作ると、同じ呼称の別物が区別できない。

## 第 3 周の再演（第 25 周・面 A 第 3 周、2026-10-23）

残存。学生 st-09・st-04 の受講可能な一覧に同じ題名のコースが 2 行（TeX 版 1bad7d9f・PDF 版 6be229e1）並び、違いは末尾の id だけ
（説明・担当教員・論文名が一覧に無い — 応答の description は空）。run: uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784 seq3、uxsim/runs/c-astro-r25-share-r3/20261023T033353Z-33782 seq3。

## 第 25 周 第 3 周の再観測（2026-10-23・campaign c-astro-r25-atlas-r3）

- 同じ題名のコース（説明文が空）が受講可能なコースに並び、学生が区別できずに上を選んだ（run 20261023T070701Z-16885
  seq 3）。app.js courseOptionLabel は重複時に二行目（説明）を添えるが、説明が空なので見分けの材料が無い。
  一覧の「id=…」はハーネスの投影の印（製品の select は題名だけ）。

## 第 4 周の再演（第 25 周・面 A 第 4 周、2026-10-25）

第 4 周の再演: 残存（学生 st-08・st-01 の受講可能なコースに同じ題名が 2 行、説明・担当教員とも空で区別できず、上のほうを当て推量で選んだ。run: uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482 seq3・uxsim/runs/c-astro-r25-share-r4/20261024T233359Z-43631 seq2）

第 25 周 第 4 周の再観測（2026-10-25）: 学習者のコース一覧に同題の「宇宙物理 論文紹介ゼミ：…」が 2 件、見分ける手掛かりなしで並んだ（変化なし）。3 番の題名「宇宙論入門 補助教材（第 23 周・堀）」は砂場材料の運営名。run: uxsim/runs/c-astro-r25-atlas-r4/20261024T233738Z-85320 seq 2 / 3。

第 5 周の再演（2026-10-27・campaign c-astro-r25-atlas-r5）: STILL。学生 2 名とも同題のコースが 2 件並び見分けられないと記した。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071720Z-78305 seq 3、uxsim/runs/c-astro-r25-atlas-r5/20261027T071949Z-96124 seq 2。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 残存（学生 st-10・st-07 とも同題名 2 行から上を当て推量。教員 te-02 のコース管理でも同題名 2 行の違いは「地図: …（一部のトピックに対応あり）／（トピックの対応なし）」だけで、どちらが TeX 版の素材か分からず PDF 版のコースを開いて部品が見つからなかった）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq3、uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789 seq3、uxsim/runs/c-astro-r25-share-r5/20261027T071859Z-89584 seq2・3


## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

STILL。学生 st-03 は同題名 2 行の違いが読めず上を当て推量で開いた（審判 C）。st-08 も同じ 2 行の一覧。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123201Z-9456 seq 3、uxsim/runs/c-astro-r25-atlas-r6/20261027T123339Z-10273 seq 2。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: STILL。学生 st-08・st-01 とも同題名 2 行から当て推量で 1 番を選んだ（ランナーの固定で 1bad7d9f に入った）。教員 te-01 のコース管理でも同題名 2 行の違いは〔所有〕/〔共有（編集可）〕と地図の対応だけで共有元の所有者名が出ず、引用の行「『宇宙物理 論文紹介ゼミ：…』（根拠として使う）」も同題名で、自分のどのコースに入ったか読めない。run: uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq2-4、uxsim/runs/c-astro-r25-share-r6/20261027T123326Z-10141 seq2-4、uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458 seq2・5

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

STILL。st-03 は同題名 2 行を見分けられず 1 番目を当て推量で開き、campaign が固定した 1bad7d9f ではなく 6be229e1 に入った（審判 A の pinned_course_mismatch）。地図の是正の確かめが固定コースから外れた直接の原因。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 3〜4。

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: STILL。学生 st-05・st-04 とも同題名 2 行から当て推量で 1 番を選んだ。教員 te-04 のコース管理では〔所有〕/〔共有〕で区別できたが、［別コースで引用］の選択肢（admin-lecture-studio.js lsOpenCiteSelector）は題名だけの option で同題名 2 つが並ぶ（uxsim/runs/c-astro-r25-share-r7/20261027T173130Z-60105 seq5）。

第 25 周 面 D 第 7 周（2026-10-28）: STILL。4 run とも同名コース 2 つ。共有教員 te-05 では同じ札・同じ開示範囲・同じボタンの 2 行で、所有者の名前も版の有無も一覧に無い。run: uxsim/runs/c-astro-r25-studio-r7/20261027T171857Z-53968 seq2、uxsim/runs/c-astro-r25-studio-r7/20261027T172130Z-55411 seq3、uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213 seq3。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**STILL**。st-03・st-01 ともコース一覧で同題名 2 行を見分けられないと記した（審判 C）。今周は場面の course_hint（H43）で固定コース 1bad7d9f を開いたので確かめは外れなかった。

第 8 周の再演: STILL（2026-10-28）。te-02・te-03 の前提の地図のコース選択に、同題名の 6be229e1 / 1bad7d9f が同じ役割「共有（編集可・トピックの保存は所有者）」で 2 つ並び、いま開いているのがどちらか読めない（te-01 は所有 / 共有で区別できた）。学生のマイコース・コース一覧も同題名 2 つ。run: uxsim/runs/c-r25-doubt-r8/20261027T223539Z-94356（seq2・3）/ 20261027T223914Z-96688（seq3）/ 20261027T224211Z-98239（seq2・3）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

STILL（第 8 周 面 A）。学生 3 人とも同題名 2 行を見分けられず、te-02 のコース管理でも同じ札・同じ開示範囲の 2 行。引用の列挙（IK-0924）も題名だけで同じ題名 2 行（uxsim/runs/c-astro-r25-share-r8/20261027T224703Z-1148 seq5）。st-10 に返ったマニュアルは「説明文の冒頭が添えられます」と言うが、説明文が未登録なので何も添えられない（規則どおり・材料側の空）。

第 8 周（面 D・2026-10-28）: **STILL**。te-01 は札（所有 / 共有）で区別できたが、te-05 の共有 2 行は題名・札まで同じ（C 所見 ec6a3213…）。st-08 はマイコースと受講可能に同名の 2 行（C 所見 8b8f0a5b…）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T223730Z-95543 seq2、uxsim/runs/c-astro-r25-studio-r8/20261027T224929Z-2551 seq3。

## 第 25 周 面 C 第 9 周（2026-10-31・judge_atlas_r9）

**STILL**。st-03・st-01 とも同題名のコース 2 行を見分けられないと記した（run uxsim/runs/c-astro-r25-atlas-r9/20261031T015252Z-38248 seq 3・20261031T015432Z-39319 seq 3、審判 C）。st-03 は 2 行目・st-01 は 1 行目が固定コースで、学生ごとに一覧の並びも違う。確かめは course_hint の固定で外れていない。

第 9 周（2026-10-31）: STILL。te-02 の前提の地図のコース選択に同題名・同役割の共有コースが 2 つ、st-05・st-08 のマイコースにも同題名 2 コース。run: uxsim/runs/c-r25-doubt-r9/20261031T003210Z-30688（seq3）/ 20261031T015310Z-38509（seq3）。

## 第 9 周の再演（第 25 周・面 A 第 9 周、2026-10-31・judge_share_r9）

STILL。学生 2 人（st-04・st-07）のコース一覧、te-04 のコース管理（所有 / 共有（編集可）の札でしか区別できない）、説明カードの引用の列挙 2 行がいずれも同じ題名。run: uxsim/runs/c-astro-r25-share-r9/20261031T020038Z-42487 seq3・uxsim/runs/c-astro-r25-share-r9/20261031T015336Z-38765 seq3・uxsim/runs/c-astro-r25-share-r9/20261031T015134Z-37470 seq2・7。

## 第 9 周の再演（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

STILL。te-05 の教員のコース管理は共有（編集可）の同名 2 行が題名・札まで同じ（原稿スタジオを開くと所有者名で区別できた）。学生 st-01 / st-08 のマイコースに同名 2 件。run: uxsim/runs/c-astro-r25-studio-r9/20261031T003210Z-30686 seq2、uxsim/runs/c-astro-r25-studio-r9/20261031T015559Z-39886 seq3、uxsim/runs/c-astro-r25-studio-r9/20261031T020200Z-43015 seq3。

## 第 10 周の再演（第 25 周・面 C 第 10 周、2026-10-31・judge_atlas_r10）

STILL。st-03・st-01 のマイコースに同じ題名 2 行（審判 C 2 件）。run: uxsim/runs/c-astro-r25-atlas-r10/20261031T053524Z-69161 seq3・uxsim/runs/c-astro-r25-atlas-r10/20261031T053658Z-69836 seq3。

第 10 周（2026-10-31・judge_doubt_r10）: STILL。te-02・te-03 の前提の地図のコース選択肢と、st-05・st-08 のコース一覧に同題名「宇宙物理 論文紹介ゼミ：4本の論文の主結果と未検証点を読む」が 2 つ（6be229e1 / 1bad7d9f）で区別の手がかりなし。run: uxsim/runs/c-r25-doubt-r10/20261031T052922Z-65583（seq3）/ uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq3）。

## 第 10 周の再演（第 25 周・面 A 第 10 周、2026-10-31・judge_share_r10）

STILL。学生のマイコース・教員のコース管理とも同じ題名のコース 2 つ（6be229e1 / 1bad7d9f）が区別できない。引用の一覧も題名だけ。run: uxsim/runs/c-astro-r25-share-r10/20261031T053407Z-68377（seq2）・uxsim/runs/c-astro-r25-share-r10/20261031T054227Z-72202（seq3）・uxsim/runs/c-astro-r25-share-r10/20261031T053828Z-70459（seq2・6）。

## 第 10 周（第 25 周・面 D 第 10 周、2026-10-31・judge_studio_r10）

STILL（学生側）。st-01・st-08 のマイコースに同じ題名が 2 件で、教員名・版・論文の手掛かりが無い（C 所見 2 件）。教員の一覧は〔所有〕/〔共有（編集可・トピックの保存は所有者）〕の札で区別できる（te-01 は札を読んで選んだ）。run: uxsim/runs/c-astro-r25-studio-r10/20261031T053843Z-70580 seq3、uxsim/runs/c-astro-r25-studio-r10/20261031T054537Z-74536 seq3、uxsim/runs/c-astro-r25-studio-r10/20261031T052922Z-65584 seq3。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

第 11 周（judge_doubt_r11）: STILL。te-02・te-03 の前提の地図のコース選択肢と、st-05・st-08 のコース一覧に同題名のゼミ 2 つ（4 ペルソナとも confused）。run: 98534 seq3 / 99946 seq3 / 1116 seq3 / 2368 seq3。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

STILL。st-03 / st-01 のマイコースに同じ題名が 2 件で区別の手掛かりが無い（審判 C 2 件、run uxsim/runs/c-astro-r25-atlas-r11/20261031T102103Z-2572 seq 3・uxsim/runs/c-astro-r25-atlas-r11/20261031T102321Z-3474 seq 3）。

## 第 11 周の再演（第 25 周・面 A 第 11 周、2026-10-31・judge_share_r11）

STILL（面 A）。st-04・st-05 のマイコースに同じ題名の 2 コース、te-02 のコース管理に同じ題名の 2 行（uxsim/runs/c-astro-r25-share-r11/20261031T101956Z-2094 seq3・uxsim/runs/c-astro-r25-share-r11/20261031T102623Z-4737 seq3・uxsim/runs/c-astro-r25-share-r11/20261031T102323Z-3501 seq2）。

第 11 周（judge_studio_r11）: STILL。st-01・st-08 のマイコースに同題名 2 件（C 所見 2 件）。教員 te-01 は〔所有〕/〔共有（編集可・トピックの保存は所有者）〕の札で区別できた。run: uxsim/runs/c-astro-r25-studio-r11/20261031T102539Z-4317 seq3・uxsim/runs/c-astro-r25-studio-r11/20261031T102933Z-6346 seq3。
