import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { BackLink } from "@/components/BackLink";
import { AuthForm } from "@/components/AuthForm";
import { Footer, Header } from "@/components/Chrome";
import { sessionState } from "@/lib/auth";

export const metadata: Metadata = { title: "Create your account" };

/** Resolved from a fixed set, never from the parameter itself, so `?next=https://…` cannot make this an
 *  open redirect. Mirrors the same function on the sign-in page. */
function destination(next: string | undefined): string {
  if (next === "download") return "/download";
  if (next === "pay") return "/pay";
  return "/account";
}

export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  // Already signed in: send them on to whatever they were heading for, or the account page if they were
  // not heading anywhere. A cookie left over from a deleted account is not "signed in" and gets cleared
  // instead of redirected.
  const { state } = await sessionState();
  const { next } = await searchParams;
  if (state === "ok") redirect(destination(Array.isArray(next) ? next[0] : next));
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
