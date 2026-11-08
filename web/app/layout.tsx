import type { Metadata } from "next";
import Link from "next/link";
import { Archivo, Space_Mono } from "next/font/google";
import { LogoIcon } from "@/components/icons";
import ServiceMenu from "@/components/service-menu";
import ThemeToggle from "@/components/theme-toggle";
import { THEME_KEY } from "@/lib/theme";
import "./globals.css";

// Archivo with its width axis: `.font-display` stretches it to 125% for the headings.
const archivo = Archivo({ variable: "--font-archivo", subsets: ["latin"], axes: ["wdth"] });
const spaceMono = Space_Mono({ variable: "--font-space-mono", subsets: ["latin"], weight: ["400", "700"] });

export const metadata: Metadata = {
  title: "Swave",
  description: "Feed it a few tracks, get a set",
};

// Runs before first paint: saved choice, else the system setting (no flash of the wrong theme).
const themeScript = `try{var t=localStorage.getItem(${JSON.stringify(THEME_KEY)})}catch(e){}
document.documentElement.dataset.theme=t==="light"||t==="dark"?t:matchMedia("(prefers-color-scheme: light)").matches?"light":"dark"`;

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    // suppressHydrationWarning: the theme script sets data-theme on <html> before React hydrates.
    <html lang="en" className={`${archivo.variable} ${spaceMono.variable} h-full antialiased`} suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body className="min-h-full font-sans">
        <div className="top-rule" aria-hidden="true" />
        <div className="mx-auto w-full max-w-4xl px-4 sm:px-8">
          <header className="flex items-center justify-between py-5">
            <Link href="/" className="flex items-center gap-2.5 text-text hover:text-pink-soft" aria-label="Swave home">
              <LogoIcon className="h-5 w-5 text-pink" />
              <span className="font-display text-sm font-semibold tracking-[0.45em]">Swave</span>
            </Link>
            <div className="flex items-center gap-2 sm:gap-4">
              <ServiceMenu />
              <span className="label hidden items-center gap-2 text-pink sm:flex" aria-hidden="true">
                <span className="on-air h-2 w-2 rounded-full bg-pink" />
                On air
              </span>
              <ThemeToggle />
            </div>
          </header>
          <main className="pb-10 pt-4 sm:pt-10">{children}</main>
          <footer className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line/60 py-3 text-xs text-dim">
            <Link href="/data" className="inline-flex min-h-11 items-center hover:text-text">What Swave stores</Link>
            <span aria-hidden="true">·</span>
            <a href="https://www.instagram.com/swave.party/" target="_blank" rel="noopener noreferrer"
               className="inline-flex min-h-11 items-center hover:text-text">
              Instagram @swave.party<span className="sr-only"> (opens in a new tab)</span>
            </a>
            <span aria-hidden="true">·</span>
            <a href="mailto:joinswave@gmail.com" className="inline-flex min-h-11 items-center hover:text-text">
              joinswave@gmail.com
            </a>
          </footer>
        </div>
      </body>
    </html>
  );
}
