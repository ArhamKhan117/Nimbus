/**
 * Provider selection and sender parsing.
 *
 * These two are worth pinning and the message bodies are not. Selection decides whether a six-digit
 * trial code reaches a stranger at all, and it is the kind of branch that reads correct and behaves
 * backwards. Sender parsing exists because the two providers disagree about the shape of a from-address:
 * Resend takes `"Name <address>"` whole, Brevo wants the name and the address as separate fields, so a
 * value that is fine for one is silently wrong for the other.
 *
 * The bodies are not tested because asserting on marketing copy is how a test suite becomes something
 * people edit to make green rather than read.
 */
import assert from "node:assert/strict";
import { afterEach, describe, it } from "node:test";

import { emailProvider, parseSender } from "./email-provider.ts";

const KEYS = ["BREVO_API_KEY", "RESEND_API_KEY"] as const;

function withEnv(values: Partial<Record<(typeof KEYS)[number], string>>): void {
  for (const key of KEYS) delete process.env[key];
  for (const [key, value] of Object.entries(values)) process.env[key] = value;
}

afterEach(() => {
  for (const key of KEYS) delete process.env[key];
});

describe("provider selection", () => {
  it("is none when nothing is configured, rather than throwing", () => {
    withEnv({});
    assert.equal(emailProvider(), "none");
  });

  it("uses Resend when only Resend is configured", () => {
    withEnv({ RESEND_API_KEY: "re_test" });
    assert.equal(emailProvider(), "resend");
  });

  it("uses Brevo when only Brevo is configured", () => {
    withEnv({ BREVO_API_KEY: "xkeysib-test" });
    assert.equal(emailProvider(), "brevo");
  });

  it("prefers Brevo when both are configured", () => {
    // The deployment that sets both is one migrating off Resend because Resend cannot reach a third
    // party without a verified domain. Preferring the one that works is the whole point.
    withEnv({ BREVO_API_KEY: "xkeysib-test", RESEND_API_KEY: "re_test" });
    assert.equal(emailProvider(), "brevo");
  });

  it("treats an empty key as absent", () => {
    // An unset Vercel variable and one set to "" are the same intent, and only one of them is falsy
    // by accident. Asserted so a blank value cannot select a provider that will reject every send.
    withEnv({ BREVO_API_KEY: "", RESEND_API_KEY: "re_test" });
    assert.equal(emailProvider(), "resend");
  });
});

describe("sender parsing", () => {
  it("splits a display-name address into its parts", () => {
    assert.deepEqual(parseSender("Nimbus <hello@example.com>"), {
      name: "Nimbus",
      email: "hello@example.com",
    });
  });

  it("accepts a bare address and supplies a name", () => {
    // Brevo rejects a sender with no name, so a bare address has to gain one rather than pass through.
    assert.deepEqual(parseSender("hello@example.com"), {
      name: "Nimbus",
      email: "hello@example.com",
    });
  });

  it("tolerates surrounding and internal whitespace", () => {
    assert.deepEqual(parseSender("  Nimbus Support < hello@example.com >  "), {
      name: "Nimbus Support",
      email: "hello@example.com",
    });
  });

  it("never returns an empty name", () => {
    for (const value of ["<hello@example.com>", " <hello@example.com>", "hello@example.com"]) {
      assert.notEqual(parseSender(value).name, "");
    }
  });
});
