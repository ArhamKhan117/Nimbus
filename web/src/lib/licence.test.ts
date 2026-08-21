/**
 * Tests for the part of the web app the desktop app trusts.
 *
 *     npm test
 *
 * `node:test` and `node:assert` from the standard library, run through Node's own type stripping — no
 * test framework, no transpiler, nothing to keep in step with Next. What is worth pinning here is
 * narrow and load-bearing:
 *
 * * a token this signs is one the shipped Python client will accept, byte for byte;
 * * a tampered payload does not verify;
 * * the seat count is 2, because that is a business decision that should not drift silently;
 * * a long paid period cannot mint a long token, or cancellation stops being able to bite;
 * * licence keys avoid characters that get misread, and do not repeat.
 *
 * The private key is generated per run. No real key goes anywhere near a test.
 */
import assert from "node:assert/strict";
import crypto from "node:crypto";
import { after, before, describe, it } from "node:test";

import {
  DEFAULT_SEATS,
  TOKEN_TTL_DAYS,
  addDays,
  newLicenceKey,
  signClaims,
  tokenExpiry,
  verifyToken,
} from "./licence.ts";

let publicKeyB64 = "";
const previousKey = process.env.NIMBUS_LICENCE_PRIVATE_KEY;

before(() => {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  process.env.NIMBUS_LICENCE_PRIVATE_KEY = privateKey
    .export({ format: "der", type: "pkcs8" })
    .subarray(16)
    .toString("base64url");
  publicKeyB64 = publicKey.export({ format: "der", type: "spki" }).subarray(12).toString("base64url");
});

after(() => {
  process.env.NIMBUS_LICENCE_PRIVATE_KEY = previousKey;
});

function claims(overrides: Record<string, unknown> = {}) {
  return {
    kind: "subscription" as const,
    plan: "Nimbus",
    email: "buyer@example.com",
    expires_at: addDays(30).toISOString(),
    issued_at: new Date().toISOString(),
    seats_used: 1,
    seats_total: DEFAULT_SEATS,
    device_id: "a".repeat(32),
    ...overrides,
  };
}

describe("token format", () => {
  it("is <payload>.<signature>, base64url, no padding", () => {
    const token = signClaims(claims());
    const parts = token.split(".");
    assert.equal(parts.length, 2, "exactly one dot: the client splits on it and rejects anything else");
    assert.ok(!token.includes("="), "padding would not survive the client's base64url decode");
    assert.match(token, /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
  });

  it("serialises with sorted keys and no whitespace", () => {
    // The client verifies the signature over the exact bytes it received. Key order is part of the
    // signed payload, so an unsorted or pretty-printed payload breaks every licence.
    const payload = Buffer.from(signClaims(claims()).split(".")[0], "base64url").toString("utf8");
    assert.ok(!payload.includes(" "), "no whitespace");
    const keys = Object.keys(JSON.parse(payload));
    assert.deepEqual(keys, [...keys].sort(), "keys are sorted");
  });

  it("drops empty fields rather than signing nulls", () => {
    const payload = JSON.parse(
      Buffer.from(signClaims(claims({ email: "" })).split(".")[0], "base64url").toString("utf8"),
    );
    assert.ok(!("email" in payload));
  });
});

describe("verification", () => {
  it("accepts what it signed", () => {
    const parsed = verifyToken(signClaims(claims()), publicKeyB64);
    assert.equal(parsed.kind, "subscription");
    assert.equal(parsed.seats_total, DEFAULT_SEATS);
  });

  it("rejects a tampered payload", () => {
    // The whole point of signing: a licence edited to extend its expiry must not verify.
    const [, signature] = signClaims(claims()).split(".");
    const forged = Buffer.from(
      JSON.stringify({ kind: "subscription", expires_at: addDays(3650).toISOString() }),
      "utf8",
    ).toString("base64url");
    assert.throws(() => verifyToken(`${forged}.${signature}`, publicKeyB64));
  });

  it("rejects a token signed by another key", () => {
    // Someone running their own licence service against our client.
    const other = crypto.generateKeyPairSync("ed25519");
    const previous = process.env.NIMBUS_LICENCE_PRIVATE_KEY;
    process.env.NIMBUS_LICENCE_PRIVATE_KEY = other.privateKey
      .export({ format: "der", type: "pkcs8" })
      .subarray(16)
      .toString("base64url");
    const token = signClaims(claims());
    process.env.NIMBUS_LICENCE_PRIVATE_KEY = previous;

    assert.throws(() => verifyToken(token, publicKeyB64));
  });

  it("rejects malformed tokens without throwing anything unhelpful", () => {
    for (const bad of ["", "no-dot", "!!!.???", "."]) {
      assert.throws(() => verifyToken(bad, publicKeyB64));
    }
  });

  it("refuses to sign without a key, rather than signing with a default", () => {
    const previous = process.env.NIMBUS_LICENCE_PRIVATE_KEY;
    delete process.env.NIMBUS_LICENCE_PRIVATE_KEY;
    assert.throws(() => signClaims(claims()), /NIMBUS_LICENCE_PRIVATE_KEY/);
    process.env.NIMBUS_LICENCE_PRIVATE_KEY = previous;
  });

  it("refuses a key of the wrong length instead of producing garbage", () => {
    const previous = process.env.NIMBUS_LICENCE_PRIVATE_KEY;
    process.env.NIMBUS_LICENCE_PRIVATE_KEY = Buffer.alloc(31).toString("base64url");
    assert.throws(() => signClaims(claims()), /32/);
    process.env.NIMBUS_LICENCE_PRIVATE_KEY = previous;
  });
});

describe("policy", () => {
  it("gives two seats", () => {
    // A business decision, pinned so it cannot drift in a refactor. Two: a desktop and a laptop.
    assert.equal(DEFAULT_SEATS, 2);
  });

  it("never mints a token longer than the revalidation cap", () => {
    // A 24-month licence must not produce a 24-month token, or a cancellation cannot bite.
    const capped = tokenExpiry(addDays(720));
    assert.ok(capped <= addDays(TOKEN_TTL_DAYS + 1));
  });

  it("uses the paid period when it ends sooner than the cap", () => {
    const soon = addDays(3);
    assert.equal(tokenExpiry(soon).getTime(), soon.getTime());
  });
});

describe("licence keys", () => {
  it("avoids characters that get misread when read aloud", () => {
    for (let attempt = 0; attempt < 500; attempt += 1) {
      const key = newLicenceKey();
      assert.match(key, /^NIMBUS(-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}){3}$/);
      assert.ok(!/[IO01]/.test(key.slice(7)));
    }
  });

  it("does not repeat", () => {
    // A guessable licence key is a licence key everyone has.
    const keys = new Set(Array.from({ length: 2000 }, () => newLicenceKey()));
    assert.equal(keys.size, 2000);
  });
});
