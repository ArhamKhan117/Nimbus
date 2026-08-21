/**
 * Licence keys and Ed25519 token signing.
 *
 * This is the security core of the whole product: the desktop app trusts a licence because of a
 * signature made here, and for no other reason. A spoofed or compromised copy of this site cannot
 * grant a licence without the private key.
 *
 * ## Why Node's own crypto and no library
 *
 * Node has supported Ed25519 in `crypto` since v12. Signing needs a PKCS#8 wrapper round the raw
 * 32-byte key, which is a fixed 16-byte DER prefix — small enough that reaching for a dependency
 * here would add supply-chain surface to the one file that must not have any.
 *
 * ## The token format
 *
 * `<base64url payload>.<base64url signature>`, matching `licensing.verify_token` in the desktop app
 * byte for byte. Deliberately **not a JWT**: a JWT carries an algorithm field, and algorithm
 * negotiation is where JWT libraries get broken. One algorithm, no header, nothing to negotiate.
 *
 * The payload is `JSON.stringify` over **sorted keys with no whitespace**, because the client
 * verifies the signature over the exact bytes it received. Any difference in key order between what
 * was signed and what was sent breaks every licence.
 */
import crypto from "node:crypto";

export const PLAN_NAME = "Nimbus";
export const PLAN_PRICE_USD = 10;
export const TRIAL_DAYS = 7;

export const DEFAULT_SEATS = 2;
/**
 * Two devices: a desktop and a laptop. Enough for how one person actually works, small enough that a
 * key passed round a classroom runs out immediately — which is the entire point of counting seats.
 */

export const TOKEN_TTL_DAYS = 30;
/**
 * How long a signed token lasts before the app must refresh it. Shorter than the billing period on
 * purpose: a cancelled subscription simply stops being re-signed, so this is the longest a lapsed
 * tester keeps working. Comfortably longer than the 7-day revalidation interval, so nobody honest
 * notices.
 */

const PKCS8_ED25519_PREFIX = Buffer.from("302e020100300506032b657004220420", "hex");

export function b64url(input: Buffer | string): string {
  const buffer = typeof input === "string" ? Buffer.from(input, "utf8") : input;
  return buffer.toString("base64url");
}

function privateKey(): crypto.KeyObject {
  const encoded = process.env.NIMBUS_LICENCE_PRIVATE_KEY?.trim();
  if (!encoded) {
    throw new Error("NIMBUS_LICENCE_PRIVATE_KEY is not set. Licences cannot be signed.");
  }
  const raw = Buffer.from(encoded, "base64url");
  if (raw.length !== 32) {
    throw new Error(`NIMBUS_LICENCE_PRIVATE_KEY decodes to ${raw.length} bytes; Ed25519 needs 32.`);
  }
  return crypto.createPrivateKey({
    key: Buffer.concat([PKCS8_ED25519_PREFIX, raw]),
    format: "der",
    type: "pkcs8",
  });
}

export type Claims = {
  kind: "trial" | "subscription";
  plan: string;
  expires_at: string;
  issued_at: string;
  email?: string;
  seats_used?: number;
  seats_total?: number;
  device_id?: string;
};

/** Sign claims into the token the desktop app expects. */
export function signClaims(claims: Claims): string {
  const sorted = Object.keys(claims)
    .sort()
    .reduce<Record<string, unknown>>((accumulated, key) => {
      const value = (claims as Record<string, unknown>)[key];
      if (value !== undefined && value !== null && value !== "") accumulated[key] = value;
      return accumulated;
    }, {});
  const payload = Buffer.from(JSON.stringify(sorted), "utf8");
  const signature = crypto.sign(null, payload, privateKey());
  return `${b64url(payload)}.${b64url(signature)}`;
}

/**
 * Verify a token with the public half. Used by the tests and by `/api/health`, so a deployment with
 * a mismatched pair is caught by a health check rather than by a tester.
 */
export function verifyToken(token: string, publicKeyB64: string): Claims {
  const [payloadB64, signatureB64] = token.split(".");
  if (!payloadB64 || !signatureB64) throw new Error("Malformed token.");
  const publicKey = crypto.createPublicKey({
    key: Buffer.concat([
      Buffer.from("302a300506032b6570032100", "hex"),
      Buffer.from(publicKeyB64, "base64url"),
    ]),
    format: "der",
    type: "spki",
  });
  const payload = Buffer.from(payloadB64, "base64url");
  if (!crypto.verify(null, payload, publicKey, Buffer.from(signatureB64, "base64url"))) {
    throw new Error("Bad signature.");
  }
  return JSON.parse(payload.toString("utf8")) as Claims;
}

const KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
/** I, O, 0 and 1 are missing on purpose: these keys get read aloud and typed by hand. */

/** `NIMBUS-XXXX-XXXX-XXXX` from a CSPRNG. A guessable licence key is a licence key everyone has. */
export function newLicenceKey(): string {
  const block = () =>
    Array.from(crypto.randomBytes(4))
      .map((byte) => KEY_ALPHABET[byte % KEY_ALPHABET.length])
      .join("");
  return `NIMBUS-${block()}-${block()}-${block()}`;
}

export function addDays(days: number, from: Date = new Date()): Date {
  return new Date(from.getTime() + days * 86_400_000);
}

/** The token expiry: the paid period, capped at `TOKEN_TTL_DAYS`. */
export function tokenExpiry(periodEnd: Date): Date {
  const cap = addDays(TOKEN_TTL_DAYS);
  return periodEnd < cap ? periodEnd : cap;
}
