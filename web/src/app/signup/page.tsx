import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { BackLink } from "@/components/BackLink";
import { AuthForm } from "@/components/AuthForm";
import { Footer, Header } from "@/components/Chrome";
import { sessionState } from "@/lib/auth";

export const metadata: Metadata = { title: "Create your account" };

export default async function SignupPage() {
  // Already signed in: the account page is what they actually wanted. A cookie left over from a deleted
  // account is not "signed in" and gets cleared instead of redirected.
  const { state } = await sessionState();
  if (state === "ok") redirect("/account");
  if (state === "stale") redirect("/api/auth/stale");

  return (
    <>
      <Header />
      <main id="main" className="mx-auto max-w-md px-6 py-16">
        <BackLink href="/" className="mb-6">
          Back to Nimbus
        </BackLink>
        <Suspense fallback={null}>
          <AuthForm mode="signup" />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
