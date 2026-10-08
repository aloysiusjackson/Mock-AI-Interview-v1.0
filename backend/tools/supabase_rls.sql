-- ============================================================================
-- Optional: lock the Supabase tables down to server-only access.
-- ============================================================================
-- Run this in the Supabase SQL editor AFTER `pg_schema.py` has created the
-- tables and AFTER `migrate_to_postgres.py` has copied the data.
--
-- Read the reasoning before you run it, because "enable RLS and add no
-- policies" is deliberate here:
--
--   * The application never talks to Supabase from the browser. Every query
--     goes through the Flask backend, which connects with the
--     DATABASE_URL role. No anon/publishable key and no service-role key exist
--     anywhere in the frontend or the repository.
--
--   * That connection role OWNS the tables, and a table's owner is exempt from
--     its own row-level policies. So enabling RLS with no policies denies
--     access to every *other* client (PostgREST / supabase-js with the anon
--     key, a leaked key, a curious dashboard query) while leaving the app
--     untouched.
--
--   * No permissive policies are created on purpose. A policy like
--     `USING (true)` for anon would undo all of this: anyone holding the
--     publishable key could then read every user, interview and transcript
--     straight from the REST endpoint. If you ever add a client-side data
--     path, write a policy that scopes rows to the signed-in user
--     (`auth.uid() = user_id`) instead.
--
-- Idempotent: safe to run more than once.

DO $$
DECLARE
    target text;
    tables text[] := ARRAY[
        'users',
        'questions',
        'interviews',
        'answers',
        'activity_logs',
        'feedback'
    ];
BEGIN
    FOREACH target IN ARRAY tables LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = target
        ) THEN
            -- Enable RLS and force it for the table owner too? No: the app
            -- connects as the owner, so FORCE ROW LEVEL SECURITY would break
            -- every query the application makes. Leave the owner exempt.
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', target);
            RAISE NOTICE 'RLS enabled on public.% (no policies = deny by default)', target;
        END IF;
    END LOOP;
END
$$;

-- Verify: this must list the six tables with rowsecurity = true.
--
--     SELECT relname, relrowsecurity
--     FROM pg_class
--     WHERE relnamespace = 'public'::regnamespace
--       AND relkind = 'r'
--     ORDER BY relname;
--
-- And this must return zero rows (there should be no policies to find):
--
--     SELECT tablename, policyname FROM pg_policies WHERE schemaname = 'public';


-- ── Defence in depth for the "leaked key" case ──────────────────────────────
-- Even with RLS on, revoke the privileges the API roles do not need:
-- the browser-facing roles have no business touching these tables directly.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon;
        RAISE NOTICE 'Revoked table/sequence access from role anon';
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        REVOKE ALL ON ALL TABLES IN SCHEMA public FROM authenticated;
        REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM authenticated;
        RAISE NOTICE 'Revoked table/sequence access from role authenticated';
    END IF;
END
$$;

-- The application's own role keeps full access, so nothing in the app changes.
