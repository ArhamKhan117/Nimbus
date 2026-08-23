"use client";

/**
 * "I have sent the money" — the form that turns a transfer into a licence.
 *
 * The transaction ID is the only field that matters, so it is the only one that is required. Every
 * extra required field on this form is another chance for someone who has already paid to give up
 * before telling us.
 *
 * On success the page says a human will check it, because a human will. No fake progress bar.
 */
import { useState } from "react";

import { messageFromResponse, messageFromThrow } from "@/lib/errors";

type Props = { signedIn: boolean; easypaisaAvailable: boolean; bankAvailable: boolean };

export function ManualPaymentForm({ signedIn, easypaisaAvailable, bankAvailable }: Props) {
  const [method, setMethod] = useState<"easypaisa" | "bank">(
    easypaisaAvailable ? "easypaisa" : "bank",
  );
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [problem, setProblem] = useState("");

  if (!signedIn) {
    return (
      <div className="surface grain relative overflow-hidden p-6 sm:p-7">
        <p className="eyebrow">Step 2</p>
        <h2 className="mt-3 text-[1.1rem] font-semibold">Tell us it is sent</h2>
        <p className="mt-2 text-ink-2">
          Create an account first so we know whose licence to issue and where to email the key.
        </p>
        <div className="mt-5 flex flex-wrap gap-3">
          <a className="btn btn-primary" href="/signup?next=pay">
            <span>Create an account</span>
          </a>
          <a className="btn btn-ghost" href="/login?next=pay">
            <span>I already have one</span>
          </a>
        </div>
      </div>
    );
  }

  if (done) {
    return (
      <div className="surface grain relative overflow-hidden p-6 sm:p-7">
        <p className="eyebrow">Received</p>
        <h2 className="mt-3 text-[1.1rem] font-semibold">We are checking it now</h2>
        <p className="mt-2 text-ink-2">
          A person confirms the transfer by hand, usually within a few hours. Your licence key is
          emailed the moment it clears and appears on your{" "}
          <a className="text-accent hover:text-accent-hover" href="/account">
            account page
          </a>
          .
        </p>
        {/* Somewhere to go next. A terminal screen with no exit makes people wonder whether it worked. */}
        <div className="mt-6 flex flex-wrap gap-3">
          <a className="btn" href="/account">
            <span>Go to my account</span>
          </a>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => {
              setDone(false);
              setReference("");
              setNote("");
            }}
          >
            <span>Submit another reference</span>
          </button>
        </div>
      </div>
    );
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem("");
    try {
      const response = await fetch("/api/easypaisa/manual", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ method, reference: reference.trim(), note }),
      });
      const body = (await response.json().catch(() => null)) as { ok?: boolean } | null;
      if (!response.ok) {
        setProblem(messageFromResponse(body));
        setBusy(false);
        return;
      }
      setDone(true);
    } catch (error) {
      setProblem(messageFromThrow(error));
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="surface grain relative overflow-hidden p-6 sm:p-7">
      <p className="eyebrow">Step 2</p>
      <h2 className="mt-3 text-[1.1rem] font-semibold">Tell us it is sent</h2>

      <fieldset className="mt-5">
        <legend className="text-[14px] font-semibold text-ink-2">How did you send it?</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {([
            ["easypaisa", "EasyPaisa", easypaisaAvailable],
            ["bank", "Bank transfer", bankAvailable],
          ] as const)
            .filter(([, , available]) => available)
            .map(([value, label]) => (
              <button
                key={value}
                type="button"
                onClick={() => setMethod(value)}
                aria-pressed={method === value}
                className={`btn ${method === value ? "border-accent text-ink" : "text-ink-2"}`}
              >
                {label}
              </button>
            ))}
        </div>
      </fieldset>

      <label className="mt-5 block text-[14px] font-semibold text-ink-2" htmlFor="reference">
        Transaction ID from your receipt
      </label>
      <input
        id="reference"
        className="field mt-2 font-mono"
        required
        minLength={4}
        maxLength={64}
        value={reference}
        onChange={(event) => setReference(event.target.value)}
        placeholder={method === "easypaisa" ? "e.g. 41290387412" : "Bank reference number"}
      />

      <label className="mt-4 block text-[14px] font-semibold text-ink-2" htmlFor="note">
        Anything we should know <span className="font-normal text-ink-3">(optional)</span>
      </label>
      <input
        id="note"
        className="field mt-2"
        maxLength={300}
        value={note}
        onChange={(event) => setNote(event.target.value)}
        placeholder="Sent from a different number, paying for two people, etc."
      />

      {problem ? <p role="alert" className="mt-4 rounded-[8px] border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-[#ffb3b1]">{problem}</p> : null}

      <button type="submit" className="btn btn-primary mt-6 w-full sm:w-auto" disabled={busy}>
        {busy ? "Sending\u2026" : "I have sent the payment"}
      </button>
    </form>
  );
}
