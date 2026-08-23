import type { Metadata } from "next";

import { AdminPayments } from "@/components/AdminPayments";
import { Footer, Header } from "@/components/Chrome";

export const metadata: Metadata = { title: "Approve payments", robots: { index: false } };

/**
 * Where you turn a received transfer into a licence.
 *
 * The admin token is **typed into this page and held in memory only** — never in a cookie, never in
 * localStorage, never in the URL. So a shared or forgotten browser leaves nothing behind, and this page
 * is worthless to anyone who loads it without the token. It also means refreshing asks again, which is
 * a fair price for the only screen here that can mint a licence.
 */
export default function AdminPage() {
  return (
    <>
      <Header />
      <main id="main" className="mx-auto max-w-3xl px-6 py-14">
        <p className="eyebrow">Admin</p>
        <h1 className="mt-2 text-[clamp(1.6rem,4vw,2.1rem)] font-semibold tracking-[-0.02em]">
          Payments waiting
        </h1>
        <p className="mt-3 text-ink-2">
          EasyPaisa and bank transfers that have been submitted but not yet turned into a licence.
        </p>
        <div className="mt-8">
          <AdminPayments />
        </div>
      </main>
      <Footer />
    </>
  );
}
