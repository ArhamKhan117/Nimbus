"use client";

/**
 * The card-payment button.
 *
 * Three states it has to handle honestly, because each is a real thing that happens:
 *
 * * **not signed in** → send them to sign up, and come back here afterwards. Buying without an
 *   account is what leaves a payment nobody can match to a person.
 * * **Stripe not configured** → say so and offer EasyPaisa, rather than throwing.
 * * **in flight** → disable, so a double click cannot open two checkout sessions.
 */
import { useState } from "react";
import { useRouter } from "next/navigation";

import { messageFromResponse, messageFromThrow } from "@/lib/errors";

type Props = {
  signedIn: boolean;
  cardsEnabled: boolean;
  className?: string;
  children?: React.ReactNode;
};

export function BuyButton({ signedIn, cardsEnabled, className = "", children }: Props) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const router = useRouter();

  async function buy() {
    if (!signedIn) {
      router.push("/signup?next=buy");
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      const response = await fetch("/api/checkout", { method: "POST" });
      const body = (await response.json().catch(() => null)) as
        | { url?: string; error?: string }
        | null;
      if (!response.ok || !body?.url) {
        setProblem(messageFromResponse(body, "CHECKOUT_UNAVAILABLE"));
        setBusy(false);
        return;
      }
      window.location.href = body.url;
    } catch (error) {
      setProblem(messageFromThrow(error));
      setBusy(false);
    }
  }

  if (!cardsEnabled) {
    return (
      <div className={className}>
        <a className="btn btn-primary w-full sm:w-auto" href="/pay">
          Pay by EasyPaisa or bank
        </a>
        <p className="mt-2 text-[13px] text-ink-3">
          Card payments are being switched on. Local transfer works today and gets you the same
          licence.
        </p>
      </div>
    );
  }

  return (
    <div className={className}>
      <button type="button" className="btn btn-primary w-full sm:w-auto" onClick={buy} disabled={busy}>
        {busy ? "Opening checkout\u2026" : (children ?? "Pay by card")}
      </button>
      {problem ? <p role="alert" className="mt-2.5 rounded-[8px] border border-danger/40 bg-danger/10 px-3 py-2 text-[13px] leading-relaxed text-[#ffb3b1]">{problem}</p> : null}
    </div>
  );
}
