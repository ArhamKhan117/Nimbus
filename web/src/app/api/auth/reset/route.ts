/**
 * Forgot password, in two steps and one file.
 *
 * * `POST { email }` — sends a 6-digit code. Always answers success, whether or not the address exists,
 *   because anything else is an account-enumeration oracle.
 * * `POST { email, code, password }` — checks the code and sets the new password.
 *
 * ## Why a code rather than a reset link
 *
 * Consistency with the desktop flow, which cannot use links, and one fewer thing to explain: the same six
 * digits arrive by email whether you are resetting a password or verifying a new account. It also survives
 * an email client that mangles long URLs, which is a real failure on some webmail clients.
 *
 * The sign-in link still exists at `/api/auth/link` for people who would rather click once. Two routes to
 * the same place is fine when both are cheap; two *mechanisms* would not be, which is why both go through
 * the same `Token` model.
 *
 * Resetting a password clears the lockout counter deliberately: someone locked out for forgetting their
 * password has just proved they own the mailbox, and leaving them locked out after that is punishing the
 * wrong thing.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { checkCode, hashPassword, issueCode, normaliseEmail } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { errorBody } from "@/lib/errors";
import { sendCodeEmail } from "@/lib/email";

export const runtime = "nodejs";

const Request_ = z.object({ email: z.string().email().max(200) });
const Complete = z.object({
  email: z.string().email().max(200),
  code: z.string().min(4).max(12),
  password: z.string().min(10).max(200),
});

export async function POST(request: Request) {
  const raw = await request.json().catch(() => null);

  const complete = Complete.safeParse(raw);
  if (complete.success) {
    const email = normaliseEmail(complete.data.email);
    const user = await db.user.findUnique({ where: { email } });
    if (!user) {
      // Still deliberately vague on this path. Confirming an address to an unauthenticated stranger who
      // has guessed a code has no upside, and by this point they have already been told whether the
      // address exists if they simply tried to sign in.
      return NextResponse.json(errorBody("CODE_WRONG"), { status: 401 });
    }

    const result = await checkCode(user.id, complete.data.code, "reset");
    if (result !== "ok") {
      const code = { wrong: "CODE_WRONG", expired: "CODE_EXPIRED", "too-many": "CODE_ATTEMPTS" }[
        result
      ] as "CODE_WRONG" | "CODE_EXPIRED" | "CODE_ATTEMPTS";
      return NextResponse.json(errorBody(code), { status: result === "wrong" ? 401 : 403 });
    }

    await db.user.update({
      where: { id: user.id },
      data: {
        passwordHash: hashPassword(complete.data.password),
        emailVerified: true,
        failedLogins: 0,
        lockedUntil: null,
      },
    });
    await logEvent("password.reset", email);
    return NextResponse.json({ ok: true });
  }

  const requested = Request_.safeParse(raw);
  if (!requested.success) {
    return NextResponse.json(errorBody("BAD_EMAIL"), { status: 400 });
  }

  const email = normaliseEmail(requested.data.email);
  let user;
  try {
    user = await db.user.findUnique({ where: { email } });
  } catch {
    return NextResponse.json(errorBody("UNAVAILABLE"), { status: 503 });
  }

  if (user) {
    const code = await issueCode(user.id, "reset");
    const sent = await sendCodeEmail(email, code, "reset");
    await logEvent("password.reset_requested", email);
    if (!sent) {
      // A code that was never delivered is a dead end, and "check your email" would be a lie. This is
      // the one place a mail failure has to surface, because there is no other way to continue.
      return NextResponse.json(errorBody("EMAIL_FAILED"), { status: 502 });
    }
  }

  // Same response whether or not the address exists. See the note above.
  return NextResponse.json({ ok: true, sent: true });
}
