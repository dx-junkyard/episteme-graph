# 演目（campaign）

campaign は「分野 × snapshot × ペルソナ × 経路 × flags × 予算」を束ねる単位です（正本:
`docs/architecture/persona_enactment_testing_design.md` §8.1・§11.3・§12）。分野ごとの campaign は
`campaigns/<domain_key>/` に、分野をまたぐもの（回帰の一括 replay）は `campaigns/` 直下に置きます。

## ファイルの形

```yaml
id: c-astro-first-course            # 課題エントリの sources から参照される。改名しない
domain: astrophysics                # domains/<key> が存在すること
snapshot: course-astro-v1           # domains/<key>/corpus/manifest.yaml の planned_snapshots か snapshots に載っていること
personas: {teachers: [...], students: [...]}   # domains/<key>/personas/ の id
scenarios: {teacher: [...], student: [...]}   # scenarios/goal/ の id（教員段 → 学生段の順に走る）
runner: api                         # api | browser | both（both は学生 1 名を browser で並走）
flags: {ENV_NAME: "value"}          # 砂場の api-server に渡す環境変数（製品コードは変えない）
sessions_per_student: 2             # 2 回目以降は「翌日」として再ログインで近似する（§7.4）
budget: {product_llm_calls: 400, persona_llm_calls: 600, wall_clock_minutes: 90}   # 3 キーとも必須
notes: |                            # 任意。前提・意図を事実文で
  ...
```

- `budget` の値は上限であって目標ではない。実測はレポートと manifest にだけ残す（PE7）。
- 経路の `session:` が 2 の経路（`s-return-next-day`）は、`sessions_per_student` が 2 以上の campaign でだけ走る。
- 学生 campaign は既定で `course-astro-v1` から始める（教員段の費用を毎回払わない）。教員段そのものを
  見たいときだけ `corpus-astro-v1` から始める（§5.2）。
- campaign 中に arXiv を呼ばない（論文レーダー・ディスカバリーはブロック中の見え方だけを検査する — P-0007）。

## 一覧

| id | 分野 | snapshot | 目的 |
|---|---|---|---|
| `c-astro-first-course` | astrophysics | course-astro-v1 | 教員 1 名 + 学生 4 名で、初めての受講の一通り |
| `c-astro-corpus-to-course` | astrophysics | corpus-astro-v1 | 教員が教材からコースを作り、学生 2 名がそれを受講する |
| `c-astro-quota-edge` | astrophysics | course-astro-v1 | 日次上限に当たったときの見え方 |
| `c-astro-all-students-smoke` | astrophysics | course-astro-v1 | 学生 10 名全員で受講の入口だけを通す |
| `c-astro-teachers-review` | astrophysics | corpus-astro-v1 | 行動型の違う教員 4 名で、グラフ確認から公開まで |
| `c-astro-structure-30` | astrophysics | corpus-astro-v1 | 論理構造の見やすさ・辿れる理解・対話支援の 30 場面（§18。教員は既存教材を読むだけ・学生はコース 1bad7d9f 固定） |
| `c-regression-all` | （全分野） | 各回帰経路の指定 | `scenarios/regression/` 全件を replay で再演する |
