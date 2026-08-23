import type { Metadata } from "next";

import { Aurora } from "@/components/Atmosphere";
import { BackLink } from "@/components/BackLink";
import { Footer, Header } from "@/components/Chrome";
import { ResetForm } from "@/components/ResetForm";

export const metadata: Metadata = { title: "Reset your password" };

export default function ForgotPage() {
  return (
    <div className="grain-page">
      <Aurora />
      <Header />
      <main id="main" className="mx-auto max-w-md px-6 py-16">
        <BackLink href="/login" className="mb-6">
          Back to sign in
        </BackLink>
        <ResetForm />
      </main>
      <Footer />
    </div>
  );
}
