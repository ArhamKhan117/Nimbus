import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { BackLink } from "@/components/BackLink";
import { Footer, Header } from "@/components/Chrome";
import { HostedCheckout } from "@/components/HostedCheckout";
import { readSession } from "@/lib/auth";
import { hostedCheckoutAvailable } from "@/lib/easypaisa";

export const metadata: Metadata = { title: "EasyPaisa checkout" };
export const dynamic = "force-dynamic";

/**
 * EasyPaisa's own hosted checkout, when a merchant account exists.
 *
 * Redirects to the manual route while it does not, rather than showing a button that 503s. A page whose
 * only outcome is an error should not be reachable.
 */
export default async function EasypaisaCheckoutPage() {
  if (!hostedCheckoutAvailable()) redirect("/pay");
  if (!(await readSession())) redirect("/signup?next=easypaisa");

  return (
    <>
      <Header />
      <main id="main" className="mx-auto max-w-xl px-6 py-14">
        <BackLink href="/pay" className="mb-6">
          Back to payment options
        </BackLink>
        <p className="eyebrow">EasyPaisa</p>
        <h1 className="mt-2 text-[clamp(1.6rem,4vw,2.1rem)] font-semibold tracking-[-0.02em]">
          Continue in EasyPaisa
        </h1>
        <p className="mt-4 text-ink-2">
          You will be taken to EasyPaisa to approve the payment, then brought straight back here.
        </p>
        <div className="mt-8">
          <HostedCheckout />
        </div>
      </main>
      <Footer />
    </>
  );
}
