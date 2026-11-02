import type { Metadata } from "next";
import Link from "next/link";
import { Archivo, Space_Mono } from "next/font/google";
import { LogoIcon } from "@/components/icons";
import "./globals.css";

// Archivo with its width axis: `.font-display` stretches it to 125% for the headings.
const archivo = Archivo({ variable: "--font-archivo", subsets: ["latin"], axes: ["wdth"] });
const spaceMono = Space_Mono({ variable: "--font-space-mono", subsets: ["latin"], weight: ["400", "700"] });

export const metadata: Metadata = {
  title: "Swave",
  description: "Feed it a few tracks, get a set",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className={`${archivo.variable} ${spaceMono.variable} h-full antialiased`}>
      <body className="min-h-full font-sans">
        <div className="top-rule" aria-hidden="true" />
        <div className="mx-auto w-full max-w-4xl px-4 sm:px-8">
          <header className="flex items-center justify-between py-5">
            <Link href="/" className="flex items-center gap-2.5 text-text hover:text-pink-soft" aria-label="Swave home">
              <LogoIcon className="h-5 w-5 text-pink" />
              <span className="font-display text-sm font-semibold tracking-[0.45em]">Swave</span>
            </Link>
            <span className="label flex items-center gap-2 text-pink" aria-hidden="true">
              <span className="on-air h-2 w-2 rounded-full bg-pink" />
              On air
            </span>
          </header>
          <main className="pb-10 pt-4 sm:pt-10">{children}</main>
        </div>
      </body>
    </html>
  );
}
