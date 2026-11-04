-- 006_consent_challenge.sql: consent hardening. Idempotent.
--   challenge_id  : the server-issued consent challenge the recording answered (null = legacy fixed sentence)
alter table public.consents add column if not exists challenge_id text;
