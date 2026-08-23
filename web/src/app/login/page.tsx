import { Suspense } from "react";
import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { BackLink } from "@/components/BackLink";
import { AuthForm } from "@/components/AuthForm";
import { Footer, Header } from "@/components/Chrome";
import { sessionState } from "@/lib/auth";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage() {
  // Three states, not two. Bouncing on the mere presence of a cookie is what made this page and
  // /account redirect to each other forever once an account was deleted.
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
          <AuthForm mode="login" />
        </Suspense>
      </main>
      <Footer />
    </>
  );
}
