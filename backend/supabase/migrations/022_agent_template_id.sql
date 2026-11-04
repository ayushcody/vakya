-- 022_agent_template_id.sql: the front-desk type an agent was made from (clinic, hotel, salon, restaurant, office,
-- institute, service_centre or fitness; null for an agent made from scratch). Idempotent; existing agents read as null.

alter table public.agents add column if not exists template_id text;
