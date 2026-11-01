import { createClient } from '@supabase/supabase-js'

const url = import.meta.env.VITE_SUPABASE_URL
const anonKey = import.meta.env.VITE_SUPABASE_ANON_KEY

if (!url || !anonKey) {
  throw new Error('Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY. Add them to .env.local and restart the dev server.')
}

/**
 * Browser Supabase client. Uses the public anon key, so every query is subject to
 * Row Level Security. Never put the service role key in VITE_* variables.
 */
export const supabase = createClient(url, anonKey)
