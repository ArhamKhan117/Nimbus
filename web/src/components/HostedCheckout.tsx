"use client";

/**
 * Hands the browser over to EasyPaisa's hosted checkout.
 *
 * It has to be a **form POST**, not a redirect: EasyPaisa's hosted page expects the signed parameters
 * as form fields, which is why `/api/easypaisa/initiate` returns fields rather than a URL. The form is
 * built here and submitted, so the parameters never end up in a URL, in browser history, or in a
 * referrer header.
 *
 * The button is what triggers it rather than an effect on mount: a page that navigates you off itself
 * before you have read it is hostile, and a payment page is the worst place for that.
 */
import { useRef, useState } from "react";

export function HostedCheckout() {
  const host = useRef<HTMLDivElement | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");

  async function go() {
    setBusy(true);
    setProblem("");
    try {
      const response = await fetch("/api/easypaisa/initiate", { method: "POST" });
      const body = (await response.json()) as {
        action?: string;
        fields?: Record<string, string>;
        error?: string;
      };
      if (!response.ok || !body.action || !body.fields) {
        throw new Error(body.error ?? "EasyPaisa checkout could not be started.");
      }

      const form = document.createElement("form");
      form.method = "POST";
      form.action = body.action;
      for (const [name, value] of Object.entries(body.fields)) {
        const input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value;
        form.append(input);
      }
      host.current?.append(form);
      form.submit();
    } catch (error) {
      setProblem(
        error instanceof Error ? error.message : "EasyPaisa checkout could not be started.",
      );
      setBusy(false);
    }
  }

  return (
    <div>
      <button type="button" className="btn btn-primary" onClick={go} disabled={busy}>
        {busy ? "Opening EasyPaisa\u2026" : "Continue to EasyPaisa"}
      </button>
      {problem ? (
        <p className="mt-3 text-[14px] text-danger">
          {problem}{" "}
          <a className="text-accent hover:text-accent-hover" href="/pay">
            Send a transfer instead
          </a>
          .
        </p>
      ) : null}
      <div ref={host} hidden />
    </div>
  );
}
