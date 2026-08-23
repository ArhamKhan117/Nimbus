import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { BackLink } from "@/components/BackLink";
import { AuthForm } from "@/components/AuthForm";
import { Footer, Header } from "@/components/Chrome";
import { sessionState } from "@/lib/auth";

export const metadata: Metadata = { title: "Sign in" };

/** Where an already-signed-in visitor belongs, given what they were on their way to.
 *
 * Resolved from a fixed set rather than from the parameter itself. `next` arrives from a URL, and a URL is
 * whatever someone typed, so redirecting to its contents would be an open redirect: `/login?next=https://…`
 * would bounce a signed-in user off the site under our own domain's credibility.
 */
function destination(next: string | undefined): string {
  if (next === "download") return "/download";
  if (next === "pay") return "/pay";
  return "/account";
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  // Three states, not two. Bouncing on the mere presence of a cookie is what made this page and
  // /account redirect to each other forever once an account was deleted.
  const { state } = await sessionState();
  const { next } = await searchParams;
  // Signed in and pointed somewhere: go there. Landing on /account after clicking Download is the exact
  // dead end the return target exists to prevent, and it should not reappear just because the session
  // happened to already be valid.
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
          <AuthForm mode="login" />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
