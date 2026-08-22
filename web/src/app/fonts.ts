/**
 * Typography. Three faces, each doing one job.
 *
 * The app itself is stuck with Segoe UI — that is what Windows has, and `theme.py` uses it so the app
 * never renders in a fallback. The site is not stuck with anything, and using Segoe UI here would have
 * been the safest and dullest possible choice.
 *
 * | Face | Job | Why this one |
 * |---|---|---|
 * | **Sora** | Headlines | Geometric with slightly squared terminals: it reads as engineered rather than friendly, which is what a tool that draws on your screen should read as. Tight at display sizes without collapsing. |
 * | **Instrument Sans** | Body | A grotesque with more character than Inter at the same legibility. Inter is the default of every dev-tool landing page in existence; that is exactly the reason not to use it. |
 * | **JetBrains Mono** | Keys, licence keys, numbers | Designed for code, so `Ctrl + Alt + Space` and `NIMBUS-XXXX` look deliberate. Zero is slashed, which matters on a licence key someone reads aloud. |
 *
 * `next/font` self-hosts these at build time — no request to Google's servers at runtime, no
 * layout-shift flash, and one less third party watching who reads the page. `display: "swap"` so text
 * is visible immediately on a slow connection, which is the whole audience here.
 */
import { Instrument_Sans, JetBrains_Mono, Sora } from "next/font/google";

export const display = Sora({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

export const body = Instrument_Sans({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});

export const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono-web",
  display: "swap",
  weight: ["400", "500"],
});

export const fontClassNames = `${display.variable} ${body.variable} ${mono.variable}`;
