-- Rollback: users and roles table
DROP POLICY IF EXISTS service_role_all ON public.users;
DROP POLICY IF EXISTS authenticated_read_own ON public.users;
ALTER TABLE public.users DISABLE ROW LEVEL SECURITY;
DROP TABLE IF EXISTS public.users;
