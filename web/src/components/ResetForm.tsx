"use client";

/**
 * Forgot password: ask for a code, then set a new password.
 *
 * Both steps in one component and one screen state, because they are one task. Sending the user to a
 * second page after the email is where reset flows lose people — they close the tab, then the link in the
 * email opens a third context and nobody knows which window is authoritative.
 *
 * The email stays visible and editable in step two, so someone who typed it wrong can fix it without
 * starting again.
 */
import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { ERRORS, messageFromResponse, messageFromThrow } from "@/lib/errors";

export function ResetForm() {
  const router = useRouter();
  const [step, setStep] = useState<"request" | "complete" | "done">("request");
  const [email, setEmail] = useState("");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  /** Throws with a readable message, so both callers can share one catch. */
  async function post(payload: Record<string, string>) {
    const response = await fetch("/api/auth/reset", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = (await response.json().catch(() => null)) as { ok?: boolean } | null;
    if (!response.ok) throw new Error(messageFromResponse(body));
    return body;
  }

  async function requestCode(event: React.FormEvent) {
    event.preventDefault();
    if (!email.includes("@")) {
      setProblem(ERRORS.BAD_EMAIL);
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await post({ email: email.trim() });
      setStep("complete");
    } catch (error) {
      setProblem(messageFromThrow(error));
    } finally {
      setBusy(false);
    }
  }

  async function setNewPassword(event: React.FormEvent) {
    event.preventDefault();
    if (code.length < 6) {
      setProblem(ERRORS.CODE_WRONG);
      return;
    }
    if (password.length < 10) {
      setProblem(ERRORS.WEAK_PASSWORD);
      return;
    }
    setBusy(true);
    setProblem("");
    try {
      await post({ email: email.trim(), code, password });
      setStep("done");
      setTimeout(() => router.push("/login"), 1600);
    } catch (error) {
      setProblem(messageFromThrow(error));
      setBusy(false);
    }
  }

  if (step === "done") {
    return (
      <div className="surface grain p-6 sm:p-8">
        <h1 className="text-[1.5rem]">Password changed</h1>
        <p className="mt-3 text-ink-2">Taking you to the sign-in page.</p>
      </div>
    );
  }

  if (step === "complete") {
    return (
      <form onSubmit={setNewPassword} className="surface grain p-6 sm:p-8">
        <h1 className="text-[1.5rem]">Check your email</h1>
        <p className="mt-3 text-ink-2">
          We sent a 6-digit code to <span className="text-ink">{email}</span>. It expires in 20 minutes.
        </p>

        <label className="mt-7 block text-[14px] font-semibold text-ink-2" htmlFor="code">
          Code
        </label>
        <input
          id="code"
          className="field mt-2 text-center font-mono text-[1.6rem] tracking-[0.5em]"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          required
          value={code}
          onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
          placeholder="000000"
        />

        <label className="mt-5 block text-[14px] font-semibold text-ink-2" htmlFor="password">
          New password
        </label>
        <input
          id="password"
          className="field mt-2"
          type="password"
          autoComplete="new-password"
          minLength={10}
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="At least 10 characters"
        />

        {problem ? <p role="alert" className="mt-4 rounded-[8px] border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-[#ffb3b1]">{problem}</p> : null}

        <button type="submit" className="btn btn-primary mt-7 w-full" disabled={busy}>
          <span>{busy ? "Saving\u2026" : "Set new password"}</span>
        </button>

        <button
          type="button"
          className="mt-4 text-[14px] text-ink-2 hover:text-ink"
          onClick={() => {
            setStep("request");
            setCode("");
            setProblem("");
          }}
        >
          Wrong email, or nothing arrived?
        </button>
      </form>
    );
  }

  return (
    <form onSubmit={requestCode} className="surface grain p-6 sm:p-8">
      <h1 className="text-[1.5rem]">Reset your password</h1>
      <p className="mt-3 text-ink-2">
        We will email you a 6-digit code. Your licence key is unaffected by any of this.
      </p>

      <label className="mt-7 block text-[14px] font-semibold text-ink-2" htmlFor="email">
        Email
      </label>
      <input
        id="email"
        className="field mt-2"
        type="email"
        autoComplete="email"
        required
        value={email}
        onChange={(event) => setEmail(event.target.value)}
        placeholder="you@example.com"
      />

      {problem ? <p role="alert" className="mt-4 rounded-[8px] border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-[#ffb3b1]">{problem}</p> : null}

      <button type="submit" className="btn btn-primary mt-7 w-full" disabled={busy}>
        <span>{busy ? "Sending\u2026" : "Email me a code"}</span>
      </button>

      <p className="mt-5 text-[14px]">
        <Link href="/login" className="text-ink-2 hover:text-ink">
          Back to sign in
        </Link>
      </p>
    </form>
  );
}
