/**
 * `POST /api/auth/login`.
 *
 * ## Lockout, and why it lives in the database
 *
 * Vercel functions do not share memory, so an in-process attempt counter resets on every cold start
 * and protects nothing. The count is a column on the user. After `MAX_FAILED_LOGINS` the account
 * locks for `LOCKOUT_MINUTES` — long enough to make an online guessing attack pointless, short enough
 * that a tester who mistyped their password four times is not writing to support.
 *
 * ## The messages are specific, deliberately
 *
 * "No account with that email" and "that password does not match" are different sentences, because they
 * need different actions from the person reading them. That is a knowing trade against account
 * enumeration — see the note in `lib/errors.ts`. Lockout and rate limiting are what actually stop someone
 * working through a list of addresses, and both are still here.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import {
  LOCKOUT_MINUTES,
  MAX_FAILED_LOGINS,
  createSession,
  isLockedOut,
  normaliseEmail,
  verifyPassword,
} from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { errorBody } from "@/lib/errors";

export const runtime = "nodejs";

const Body = z.object({
  email: z.string().email().max(200),
  password: z.string().min(1).max(200),
});

export async function POST(request: Request) {
  const raw = await request.json().catch(() => null);
  const parsed = Body.safeParse(raw);
  if (!parsed.success) {
    // Which field is wrong, rather than "invalid request". The email is the one people mistype.
    const hasEmail = typeof (raw as { email?: unknown })?.email === "string";
    return NextResponse.json(errorBody(hasEmail ? "BAD_EMAIL" : "MISSING_FIELDS"), { status: 400 });
  }

  const email = normaliseEmail(parsed.data.email);

  let user;
  try {
    user = await db.user.findUnique({ where: { email } });
  } catch {
    return NextResponse.json(errorBody("UNAVAILABLE"), { status: 503 });
  }

  if (!user) {
    return NextResponse.json(errorBody("NO_ACCOUNT"), { status: 404 });
  }

  if (isLockedOut(user)) {
    return NextResponse.json(errorBody("LOCKED_OUT"), { status: 429 });
  }

  if (!verifyPassword(parsed.data.password, user.passwordHash)) {
    const failed = user.failedLogins + 1;
    const nowLocked = failed >= MAX_FAILED_LOGINS;
    await db.user.update({
      where: { id: user.id },
      data: {
        failedLogins: failed,
        lockedUntil: nowLocked ? new Date(Date.now() + LOCKOUT_MINUTES * 60_000) : null,
      },
    });
    await logEvent("login.failed", `${email} attempt ${failed}`);

    // Tell them how many tries are left once it starts to matter. Silence until the lockout lands is
    // how someone ends up locked out with no idea why.
    const remaining = MAX_FAILED_LOGINS - failed;
    return NextResponse.json(
      nowLocked
        ? errorBody("LOCKED_OUT")
        : errorBody(
            "WRONG_PASSWORD",
            remaining <= 3
              ? `That password does not match. ${remaining} ${remaining === 1 ? "try" : "tries"} left before this account locks for ${LOCKOUT_MINUTES} minutes.`
              : undefined,
          ),
      { status: nowLocked ? 429 : 401 },
    );
  }

  await db.user.update({
    where: { id: user.id },
    data: { failedLogins: 0, lockedUntil: null },
  });
  await createSession({ userId: user.id, email });
  await logEvent("login", email);
  return NextResponse.json({ ok: true });
}
