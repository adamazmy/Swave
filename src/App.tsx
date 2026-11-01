import { useEffect } from 'react'
import { supabase } from './lib/supabase'

export default function App() {
  // Dev-only connectivity check: logs to the browser console, renders nothing.
  useEffect(() => {
    if (!import.meta.env.DEV) return
    supabase.auth.getSession().then(({ error }) => {
      if (error) console.error('[supabase] connection failed:', error.message)
      else console.info('[supabase] connected to', import.meta.env.VITE_SUPABASE_URL)
    })
  }, [])

  return <main className="app" />
}
