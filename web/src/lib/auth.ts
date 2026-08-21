/**
 * Accounts: password hashing, session cookies, and single-use email links.
 *
 * ## Why scrypt from the standard library
 *
 * Node ships `crypto.scrypt`, which is memory-hard and the same primitive bcrypt alternatives are
 * measured against. Using it means no password dependency to keep patched in the one place where a
 * supply-chain problem is worst. Parameters below are the OWASP-referenced starting point
 * (N=2^16, r=8, p=1), which costs ~100ms per hash — slow enough to matter to an attacker and
 * invisible on a login.
 *
 * ## Why a signed cookie and not a session table
 *
 * A session row per login is one database round trip on every request, and this site's pages are
 * mostly public. The cookie is a JWT signed with `AUTH_SECRET`, `httpOnly`, `secure`, `sameSite=lax`
 * — so it survives the redirect back from Stripe, which a `strict` cookie would not.
 *
 * The tradeoff, stated: a signed cookie cannot be revoked server-side before it expires. Sessions
 * last 30 days and password changes rotate `AUTH_SECRET`-independent state, so the realistic worst
 * case is a stolen cookie working until it expires. For a tool of this scope that is the right
 * side of the trade; for anything holding money it would not be.
 */
import crypto from "node:crypto";
import { cookies } from "next/headers";
import { SignJWT, jwtVerify } from "jose";

import { db } from "./db";

const SCRYPT = { N: 2 ** 16, r: 8, p: 1, keylen: 64, maxmem: 192 * 1024 * 1024 };
/**
 * `maxmem` is not optional, and leaving it out is a live failure rather than a slow path.
 *
 * scrypt needs `128 * N * r` bytes — 64 MB at these parameters — and Node caps it at 32 MB unless told
 * otherwise, so every hash threw `RangeError: Invalid scrypt params: memory limit exceeded`. Reads worked,
 * writes did not, and signup returned 500 while login happily reported "no account". Measured: 274 ms per
 * hash with the cap raised, which is the right order for a password hash and invisible on a login.
 *
 * 192 MB of headroom rather than exactly 64 MB, so a future bump to N=2^17 does not silently reintroduce
 * this. Vercel's default function memory is 1 GB, so there is room.
 */
const SESSION_COOKIE = "nimbus_session";
const SESSION_DAYS = 30;
export const MAX_FAILED_LOGINS = 8;
export const LOCKOUT_MINUTES = 15;

function secret(): Uint8Array {
  const value = process.env.AUTH_SECRET;
  if (!value || value.length < 32) {
    throw new Error("AUTH_SECRET must be set to at least 32 characters.");
  }
  return new TextEncoder().encode(value);
}

export function hashPassword(password: string): string {
  const salt = crypto.randomBytes(16);
  const derived = crypto.scryptSync(password, salt, SCRYPT.keylen, SCRYPT);
  return `scrypt$${SCRYPT.N}$${SCRYPT.r}$${SCRYPT.p}$${salt.toString("base64url")}$${derived.toString("base64url")}`;
}

export function verifyPassword(password: string, stored: string): boolean {
  const parts = stored.split("$");
  if (parts.length !== 6 || parts[0] !== "scrypt") return false;
  const [, N, r, p, salt, expected] = parts;
  const expectedBuffer = Buffer.from(expected, "base64url");
  const derived = crypto.scryptSync(password, Buffer.from(salt, "base64url"), expectedBuffer.length, {
    N: Number(N),
    r: Number(r),
    p: Number(p),
    // Same cap as `hashPassword`, and needed for the same reason: the parameters are read back from the
    // stored hash, so verification allocates exactly as much memory as hashing did.
    maxmem: SCRYPT.maxmem,
  });
  // Constant-time: a fast `===` on hashes leaks how much of the digest matched.
  return derived.length === expectedBuffer.length && crypto.timingSafeEqual(derived, expectedBuffer);
}

export type Session = { userId: string; email: string };

export async function createSession(session: Session): Promise<void> {
  const token = await new SignJWT({ email: session.email })
    .setProtectedHeader({ alg: "HS256" })
    .setSubject(session.userId)
    .setIssuedAt()
    .setExpirationTime(`${SESSION_DAYS}d`)
    .sign(secret());

  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: SESSION_DAYS * 86_400,
  });
}

export async function readSession(): Promise<Session | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, secret());
    if (!payload.sub) return null;
    return { userId: payload.sub, email: String(payload.email ?? "") };
  } catch {
    return null;
  }
}

export async function clearSession(): Promise<void> {
  (await cookies()).delete(SESSION_COOKIE);
}

/** What a session cookie is actually worth right now. */
export type SessionState = "none" | "stale" | "ok";

/**
 * Distinguish "no cookie", "a valid cookie for an account that no longer exists", and "signed in".
 *
 * ## The bug this exists to prevent
 *
 * `readSession` verifies a signature and nothing else — by design, that is the point of a stateless
 * cookie. But it means a cookie stays cryptographically perfect after its account is deleted, and pages
 * that only ask "is there a session" disagree with pages that go on to load the user:
 *
 * * `/login` saw a session and redirected to `/account`;
 * * `/account` found no user row and redirected to `/login`;
 * * neither cleared the cookie, so the browser bounced between them until it gave up on a blank page.
 *
 * A deleted account is rare in production and routine in development, which is exactly the kind of bug
 * that ships. The cure is for every page to branch on all three states rather than two.
 *
 * ## Why a database outage counts as "ok"
 *
 * If the lookup throws we trust the cookie. A stale cookie is a nuisance for one person; treating an
 * unreachable database as "nobody is signed in" would sign out everybody at once for the duration of a
 * Neon blip, and send them to a login page that also cannot reach the database. Failing towards the
 * cookie keeps the failure proportional.
 */
export async function sessionState(): Promise<{ state: SessionState; session: Session | null }> {
  const session = await readSession();
  if (!session) return { state: "none", session: null };
  try {
    const found = await db.user.findUnique({
      where: { id: session.userId },
      select: { id: true },
    });
    return { state: found ? "ok" : "stale", session };
  } catch {
    return { state: "ok", session };
  }
}

export const CODE_LENGTH = 6;
export const MAX_CODE_ATTEMPTS = 6;
export const CODE_MINUTES = 20;

/**
 * Issue a 6-digit email verification code and return it for emailing.
 *
 * ## Why a code as well as a link
 *
 * The desktop app is where the trial starts, and a link cannot get someone from their inbox back into a
 * native window — clicking it opens a browser, which then has to hand off to an application that may not
 * be listening. A code is typed into the window that is already open and asking for it. The link stays
 * for the browser, where it is the better answer.
 *
 * Six digits is a million possibilities: plenty against a person, nothing against a script. So this is
 * only safe *with* `MAX_CODE_ATTEMPTS`, and the attempt counter is on the row rather than in memory
 * because serverless functions do not share memory.
 *
 * Any earlier unused code for the same purpose is deleted first. Two live codes means "it says the code
 * is wrong" from someone reading the first of two emails, which is indistinguishable from a bug.
 */
export async function issueCode(userId: string, purpose: "verify" | "reset"): Promise<string> {
  const code = String(crypto.randomInt(0, 10 ** CODE_LENGTH)).padStart(CODE_LENGTH, "0");
  await db.token.deleteMany({ where: { userId, purpose, usedAt: null } });
  await db.token.create({
    data: {
      userId,
      purpose,
      tokenHash: crypto.createHash("sha256").update(`${userId}:${code}`).digest("hex"),
      expiresAt: new Date(Date.now() + CODE_MINUTES * 60_000),
    },
  });
  return code;
}

export type CodeResult = "ok" | "wrong" | "expired" | "too-many";

/**
 * Check a code. Consumes it on success, counts the attempt on failure.
 *
 * The hash is salted with the user id, so a code cannot be checked against a different account and the
 * same six digits for two people are two different hashes.
 */
export async function checkCode(
  userId: string,
  code: string,
  purpose: "verify" | "reset",
): Promise<CodeResult> {
  const token = await db.token.findFirst({
    where: { userId, purpose, usedAt: null },
    orderBy: { createdAt: "desc" },
  });
  if (!token) return "expired";
  if (token.expiresAt < new Date()) return "expired";
  if (token.attempts >= MAX_CODE_ATTEMPTS) return "too-many";

  const expected = crypto
    .createHash("sha256")
    .update(`${userId}:${code.replace(/\D/g, "")}`)
    .digest("hex");

  if (expected !== token.tokenHash) {
    await db.token.update({ where: { id: token.id }, data: { attempts: token.attempts + 1 } });
    return token.attempts + 1 >= MAX_CODE_ATTEMPTS ? "too-many" : "wrong";
  }

  await db.token.update({ where: { id: token.id }, data: { usedAt: new Date() } });
  return "ok";
}

/**
 * Create a single-use email link and return the raw token to email.
 *
 * Only the SHA-256 is stored. A leaked database must not hand out working sign-in links, and there is
 * no reason for us to be able to read one — we only ever need to check whether the one presented
 * matches.
 */
export async function issueToken(
  userId: string,
  purpose: "verify" | "login" | "reset",
  minutes = 60,
): Promise<string> {
  const raw = crypto.randomBytes(32).toString("base64url");
  await db.token.create({
    data: {
      userId,
      purpose,
      tokenHash: crypto.createHash("sha256").update(raw).digest("hex"),
      expiresAt: new Date(Date.now() + minutes * 60_000),
    },
  });
  return raw;
}

/** Consume a token, returning its user id, or `null` if it is unknown, expired or already used. */
export async function consumeToken(
  raw: string,
  purpose: "verify" | "login" | "reset",
): Promise<string | null> {
  const tokenHash = crypto.createHash("sha256").update(raw).digest("hex");
  const token = await db.token.findUnique({ where: { tokenHash } });
  if (!token || token.purpose !== purpose || token.usedAt || token.expiresAt < new Date()) {
    return null;
  }
  await db.token.update({ where: { id: token.id }, data: { usedAt: new Date() } });
  return token.userId;
}

export function normaliseEmail(email: string): string {
  return email.trim().toLowerCase();
}

/** True while the account is locked out after repeated failures. */
export function isLockedOut(user: { lockedUntil: Date | null }): boolean {
  return Boolean(user.lockedUntil && user.lockedUntil > new Date());
}

export function siteUrl(): string {
  const configured = process.env.SITE_URL?.replace(/\/$/, "");
  if (configured) return configured;
  const vercel = process.env.VERCEL_PROJECT_PRODUCTION_URL ?? process.env.VERCEL_URL;
  return vercel ? `https://${vercel}` : "http://localhost:3000";
}
