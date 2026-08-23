"use client";

import { useState } from "react";

/**
 * The licence key, with a copy button.
 *
 * `user-select: all` on the key so a tap selects the whole thing on a phone, where selecting a
 * 21-character string by dragging is genuinely difficult. The clipboard API can be unavailable on an
 * insecure origin or refused outright, so a failure says "select it and copy" instead of pretending
 * the copy worked.
 */
export function LicenceKey({ licenceKey }: { licenceKey: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  async function copy() {
    try {
      await navigator.clipboard.writeText(licenceKey);
      setState("copied");
      setTimeout(() => setState("idle"), 1800);
    } catch {
      setState("failed");
    }
  }

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <code className="flex-1 select-all rounded-[8px] border border-line-strong bg-sunken px-4 py-3.5 font-mono text-[1.15rem] tracking-[1px]">
          {licenceKey}
        </code>
        <button type="button" className="btn" onClick={copy}>
          {state === "copied" ? "Copied" : "Copy"}
        </button>
      </div>
      {state === "failed" ? (
        <p className="mt-2 text-[13px] text-ink-3">
          Your browser would not let us copy it. Tap the key to select it, then copy.
        </p>
      ) : null}
    </div>
  );
}
