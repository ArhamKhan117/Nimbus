/**
 * `POST /api/auth/signup` — create an account with an email and a password.
 *
 * ## Why a password at all, when a sign-in link is fewer moving parts
 *
 * Because email is the unreliable part for this audience. Testers were on mixed providers and unreliable
 * mobile data; a link-only login means one delayed message locks someone out of the licence they
 * just activated. A password works offline of email entirely, and the sign-in link
 * stays available as the forgot-password path — one mechanism, two uses.
 *
 * ## Signing up does not sign you in
 *
 * Creating the account emails a link, and **clicking that link is what creates the session** and lands you
 * on the account page. An earlier version signed you in immediately and left a "not confirmed yet" banner
 * running, which was friendlier but meant the site had two classes of signed-in user, and the app's own
 * trial already requires proving the mailbox with a code. One rule for both: an address is yours when you
 * have opened something we sent to it.
 *
 * The exception is a **failed send**. If the email never left, a session is created anyway, because an
 * account nobody can get into is worse than an unverified one. That is why `sendVerificationEmail`'s return
 * value is checked rather than ignored.
 *
 * ## An existing address is now told so
 *
 * It used to send a sign-in link and return the same "check your email" as a fresh signup, to avoid
 * confirming which addresses are registered. In practice that showed a success screen to someone who was
 * trying to create an account, which is a worse failure than the enumeration it prevented — and the
 * person typing the address is nearly always its owner having forgotten they already signed up. It now
 * returns 409 and points them at sign-in or password reset.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { createSession, hashPassword, issueToken, normaliseEmail, siteUrl } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { errorBody } from "@/lib/errors";
import { sendVerificationEmail } from "@/lib/email";

export const runtime = "nodejs";

const Body = z.object({
  email: z.string().email().max(200),
  // Length over composition rules. A 12-character passphrase beats "P@ss1" and nobody writes it on a
  // sticky note because a rule demanded a symbol.
  password: z.string().min(10).max(200),
  name: z.string().max(120).optional(),
});

export async function POST(request: Request) {
  const body = await request.json().catch(() => null);
  const parsed = Body.safeParse(body);
  if (!parsed.success) {
    // Say which field. "Invalid input" makes someone re-check the one that was already fine.
    const issue = parsed.error.issues[0];
    const code = issue?.path[0] === "password" ? "WEAK_PASSWORD" : "BAD_EMAIL";
    return NextResponse.json(errorBody(code), { status: 400 });
  }

  const email = normaliseEmail(parsed.data.email);

  let existing;
  try {
    existing = await db.user.findUnique({ where: { email } });
  } catch {
    return NextResponse.json(errorBody("UNAVAILABLE"), { status: 503 });
  }

  if (existing) {
    // Now says so, rather than silently emailing a sign-in link and showing "check your email" to
    // someone who was trying to *create* an account. That looked like success and was not, and the
    // person who typed the address is nearly always its owner having forgotten they signed up.
    await logEvent("signup.duplicate", email);
    return NextResponse.json(errorBody("EMAIL_TAKEN"), { status: 409 });
  }

  const user = await db.user.create({
    data: {
      email,
      name: parsed.data.name?.trim() || null,
      passwordHash: hashPassword(parsed.data.password),
    },
  });

  const raw = await issueToken(user.id, "verify", 60);
  const sent = await sendVerificationEmail(
    email,
    `${siteUrl()}/api/auth/callback?token=${raw}&purpose=verify`,
  );
  await logEvent("signup", email);

  // No session yet. Clicking the emailed link is what signs you in — see the note at the top.
  //
  // **Unless the email failed.** Then a session is created anyway, because the alternative is an account
  // that exists, is paid for in a minute's time, and has no way in: the one thing worse than an unverified
  // session is a stranded tester. That is a fallback, not the path.
  if (!sent) {
    await createSession({ userId: user.id, email });
    await logEvent("signup.session_without_verification", email);
  }

  return NextResponse.json({ ok: true, emailSent: sent, verificationRequired: sent });
}
