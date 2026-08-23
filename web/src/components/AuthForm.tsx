"use client";

/**
 * Sign up and sign in.
 *
 * ## The layout, and what was wrong with it
 *
 * "Create an account", "Forgot password" and "Email me a sign-in link" were three bare links crammed onto
 * one row under the button — three different weights of action treated identically, so none of them read
 * as the obvious next step. They are now separated by what they *are*:
 *
 * * the **primary button** submits the form;
 * * **"Email me a sign-in link"** is a full-width secondary button below a labelled divider, because it is
 *   an alternative way to do the same thing rather than a footnote;
 * * **"Forgot password"** and **"Create an account"** sit in a footer row outside the card's action area,
 *   which is where people look for navigation rather than for a decision.
 *
 * ## Error handling
 *
 * Every failure resolves to a sentence from `lib/errors.ts`, including the two that hand-rolled forms
 * usually miss: a thrown `fetch` (no response to read a message from) and being offline. What was typed
 * is never cleared, except a password after it has been used.
 *
 * `next=buy` and `next=pay` carry someone straight on to the rail they came for after signing up, so a
 * click on either does not dead-end on an account screen.
 *
 * `next=download` is kept for old links only. The download no longer requires an account, so nothing
 * produces that parameter any more — but a bookmark or a shared URL still might, and honouring it costs
 * one branch and saves someone landing on an account page for no reason.
 */
import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { ERRORS, messageFromResponse, messageFromThrow } from "@/lib/errors";

type Mode = "signup" | "login";

export function AuthForm({ mode }: { mode: Mode }) {
  const router = useRouter();
  const parameters = useSearchParams();
  const next = parameters.get("next");
  // Why they were sent here, if they did not arrive under their own steam.
  const arrivedWith = parameters.get("problem");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState<"" | "form" | "link">("");
  const [problem, setProblem] = useState(
    arrivedWith === "link"
      ? ERRORS.LINK_USED
      : arrivedWith === "stale"
        ? ERRORS.STALE_SESSION
        : "",
  );
  const [notice, setNotice] = useState("");
  const [sentLink, setSentLink] = useState(false);
  const [sentVerification, setSentVerification] = useState(false);

  const signingUp = mode === "signup";

  async function submit(event: React.FormEvent) {
    event.preventDefault();

    // Checked here as well as on the server, so the answer is instant and costs no round trip on a
    // connection where a round trip is the slow part.
    if (!email.includes("@") || email.trim().length < 4) {
      setProblem(ERRORS.BAD_EMAIL);
      return;
    }
    if (signingUp && password.length < 10) {
      setProblem(ERRORS.WEAK_PASSWORD);
      return;
    }

    setBusy("form");
    setProblem("");
    setNotice("");
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: email.trim(), password }),
      });
      const body = (await response.json().catch(() => null)) as
        | { error?: string; code?: string; emailSent?: boolean; verificationRequired?: boolean }
        | null;

      if (!response.ok) {
        setProblem(messageFromResponse(body));
        setBusy("");
        return;
      }

      if (signingUp && body?.verificationRequired) {
        // The account exists but there is no session until the emailed link is opened. Show the
        // "check your email" screen rather than pushing to /account, which would only redirect back.
        setSentVerification(true);
        setBusy("");
        return;
      }

      if (signingUp && body?.emailSent === false) {
        // The email failed, so the server signed them in as a fallback. Say why no email arrived.
        setNotice(ERRORS.EMAIL_FAILED);
      }

      if (next === "buy") {
        const checkout = await fetch("/api/checkout", { method: "POST" });
        const payload = (await checkout.json().catch(() => null)) as { url?: string } | null;
        if (payload?.url) {
          window.location.href = payload.url;
          return;
        }
      }
      if (next === "download") {
        window.location.href = "/download";
        return;
      }
      if (next === "pay") {
        // Back to the payment page they were on, with the form now usable rather than a sign-up prompt.
        router.push("/pay");
        return;
      }
      router.push("/account");
    } catch (error) {
      setProblem(messageFromThrow(error));
      setBusy("");
    }
  }

  async function emailLink() {
    if (!email.includes("@")) {
      setProblem(ERRORS.BAD_EMAIL);
      return;
    }
    setBusy("link");
    setProblem("");
    try {
      const response = await fetch("/api/auth/link", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ email: email.trim() }),
      });
      if (!response.ok) {
        setProblem(messageFromResponse(await response.json().catch(() => null)));
        setBusy("");
        return;
      }
      setSentLink(true);
    } catch (error) {
      setProblem(messageFromThrow(error));
    } finally {
      setBusy("");
    }
  }

  if (sentVerification) {
    return (
      <div className="surface grain p-6 sm:p-8">
        <h1 className="text-[1.45rem]">Confirm your email to finish</h1>
        <p className="mt-3 text-ink-2">
          Your account is created. We sent a confirmation link to{" "}
          <span className="text-ink">{email.trim()}</span> &mdash; open it and you are signed in.
        </p>
        <p className="mt-4 text-[14px] leading-relaxed text-ink-3">
          The link works once and lasts an hour. Check spam if it is not there in a minute or two: it is a
          new sending domain, so the first message sometimes lands there.
        </p>
        <div className="mt-6 flex flex-wrap gap-3">
          <button type="button" className="btn" onClick={emailLink} disabled={busy !== ""}>
            <span>{busy === "link" ? "Sending\u2026" : "Send a sign-in link instead"}</span>
          </button>
          <Link href="/login" className="btn btn-ghost">
            <span>Go to sign in</span>
          </Link>
        </div>
        <p className="mt-5 text-[13.5px] text-ink-3">
          Wrong address?{" "}
          <button
            type="button"
            className="text-accent hover:text-accent-hover"
            onClick={() => {
              setSentVerification(false);
              setProblem("");
            }}
          >
            Go back and change it
          </button>
        </p>
      </div>
    );
  }

  if (sentLink) {
    return (
      <div className="surface grain p-6 sm:p-8">
        <h1 className="text-[1.45rem]">Check your email</h1>
        <p className="mt-3 text-ink-2">
          If there is an account for <span className="text-ink">{email.trim()}</span>, a sign-in link is
          on its way. It works once and expires in 30 minutes.
        </p>
        <p className="mt-5 text-[14px] text-ink-3">
          Nothing after a few minutes? Check spam, then write to{" "}
          <a className="text-accent hover:text-accent-hover" href="mailto:wolfhoghd@gmail.com">
            wolfhoghd@gmail.com
          </a>{" "}
          and a person will sort it out.
        </p>
        <button
          type="button"
          className="btn mt-6 w-full"
          onClick={() => {
            setSentLink(false);
            setProblem("");
          }}
        >
          <span>Back</span>
        </button>
      </div>
    );
  }

  return (
    <form onSubmit={submit} className="surface grain p-6 sm:p-8" noValidate>
      <h1 className="text-[1.55rem]">{signingUp ? "Create your account" : "Sign in"}</h1>
      <p className="mt-2.5 text-[0.98rem] leading-relaxed text-ink-2">
        {signingUp
          ? "You need one to start the free trial. Your licence key lives here too, and arrives by email when it is issued."
          : "Your licence key and your devices are both on the other side of this."}
      </p>

      <div className="mt-7 space-y-4">
        <div>
          <label className="block text-[13.5px] font-medium text-ink-2" htmlFor="email">
            Email
          </label>
          <input
            id="email"
            className="field mt-1.5"
            type="email"
            autoComplete="email"
            autoCapitalize="none"
            spellCheck={false}
            required
            value={email}
            onChange={(event) => {
              setEmail(event.target.value);
              setProblem("");
            }}
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label className="block text-[13.5px] font-medium text-ink-2" htmlFor="password">
            Password
          </label>
          <input
            id="password"
            className="field mt-1.5"
            type="password"
            autoComplete={signingUp ? "new-password" : "current-password"}
            required
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              setProblem("");
            }}
            placeholder={signingUp ? "At least 10 characters" : ""}
          />
          {signingUp ? (
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-3">
              Length beats symbols. Three words you will remember is stronger than one word with a
              punctuation mark in it.
            </p>
          ) : null}
        </div>
      </div>

      {problem ? (
        <p
          role="alert"
          className="mt-5 rounded-[8px] border border-danger/40 bg-danger/10 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-[#ffb3b1]"
        >
          {problem}
        </p>
      ) : null}
      {notice ? (
        <p className="mt-5 rounded-[8px] border border-warn/40 bg-warn/10 px-3.5 py-2.5 text-[13.5px] leading-relaxed text-[#f0cd92]">
          {notice}
        </p>
      ) : null}

      <button type="submit" className="btn btn-primary mt-6 w-full" disabled={busy !== ""}>
        <span>
          {busy === "form"
            ? "One moment\u2026"
            : signingUp
              ? "Create account"
              : "Sign in"}
        </span>
      </button>

      {/* The alternative route, separated by a labelled rule so it reads as "or do it this way" rather
          than as a link someone forgot to style. */}
      <div className="my-5 flex items-center gap-3" aria-hidden>
        <span className="h-px flex-1 bg-line" />
        <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-3">or</span>
        <span className="h-px flex-1 bg-line" />
      </div>

      <button type="button" className="btn w-full" onClick={emailLink} disabled={busy !== ""}>
        <span>{busy === "link" ? "Sending\u2026" : "Email me a sign-in link"}</span>
      </button>
      <p className="mt-2 text-center text-[12.5px] text-ink-3">
        No password needed. The link signs you in and expires in 30 minutes.
      </p>

      {/* Navigation, outside the action area. */}
      <div className="mt-7 border-t border-line pt-5 text-[14px]">
        {signingUp ? (
          <p className="text-center text-ink-3">
            Already have an account?{" "}
            <Link href="/login" className="text-accent transition-colors hover:text-accent-hover">
              Sign in
            </Link>
          </p>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3 text-ink-3">
            <span>
              No account?{" "}
              <Link href="/signup" className="text-accent transition-colors hover:text-accent-hover">
                Create one
              </Link>
            </span>
            <Link href="/forgot" className="transition-colors hover:text-ink">
              Forgot password
            </Link>
          </div>
        )}
      </div>
    </form>
  );
}
