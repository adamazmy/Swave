"use client";

import { THEME_KEY } from "@/lib/theme";
import { MoonIcon, SunIcon } from "./icons";

// Light/dark switch. The theme lives on <html data-theme> (set before paint by the script in
// app/layout.tsx); the icon is picked in CSS, so nothing here depends on render-time state.

export default function ThemeToggle() {
  function toggle() {
    const root = document.documentElement;
    const next = root.dataset.theme === "light" ? "dark" : "light";
    root.dataset.theme = next;
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* storage blocked: the choice lasts until reload */
    }
  }
  return (
    <button
      type="button"
      onClick={toggle}
      className="rounded-md p-1.5 text-muted hover:bg-raised hover:text-text focus-visible:outline-2 focus-visible:outline-pink"
      aria-label="Switch between light and dark mode"
      title="Light / dark"
    >
      <SunIcon className="when-dark-show h-4 w-4" />
      <MoonIcon className="when-light-show h-4 w-4" />
    </button>
  );
}
