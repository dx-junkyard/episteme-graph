-- Migration 031: D層 — 疑義 challenges (D3-1)
--
-- 承認（component_endorsements, C層）と対になる行為として、疑義を帰属・理由・型・
-- 履歴つきの正式な研究行為にする（一級市民化）。
--
-- 設計原則:
--   - 匿名疑義なし: challenger_id NOT NULL + reason NOT NULL CHECK (reason <> '')。
--     匿名・理由なしの疑義は構造的に作れない（DX-1 ガードレールテスト対象）。
--   - withdraw は行削除ではなく status='withdrawn' への遷移（P4: 履歴保持）。
--   - 疑義カードの主語は常に型（challenge_type）で描き、人格対立の文面にしない（§8-3）。
--   - 疑義数を数値スコア化・ランキング化しない（表示は段階ラベルのみ）。
--   - 監査: theory_review_events（entity_type='challenge'）。
--
-- このファイルが正本。適用は `backend/core/migrations.py` のランナーが起動時に行う（冪等・毎起動再実行）。

CREATE TABLE IF NOT EXISTS challenges (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id      TEXT NOT NULL,
    target_type    TEXT NOT NULL CHECK (target_type IN ('assumption', 'claim')),
    challenger_id  UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    challenge_type TEXT NOT NULL
                       CHECK (challenge_type IN (
                           'scope_extrapolation', 'untested_in_domain',
                           'definitional', 'hidden_lemma'
                       )),
    reason         TEXT NOT NULL CHECK (reason <> ''),
    status         TEXT NOT NULL DEFAULT 'open'
                       CHECK (status IN ('open', 'answered', 'withdrawn', 'led_to_verification')),
    course_id      TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_challenges_target ON challenges(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_challenges_challenger ON challenges(challenger_id);
