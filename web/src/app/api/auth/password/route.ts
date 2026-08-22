/**
 * `POST /api/auth/password` — set a new password from inside the account.
 *
 * Two ways in, both legitimate: you know your current password, or you arrived through a sign-in link
 * (which already proved you control the mailbox). The second is the whole reset flow, which is why
 * there is no separate reset page.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { hashPassword, readSession, verifyPassword } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";

export const runtime = "nodejs";

const Body = z.object({
  current: z.string().max(200).optional(),
  password: z.string().min(10).max(200),
});

export async function POST(request: Request) {
  const session = await readSession();
  if (!session) return NextResponse.json({ error: "Sign in first." }, { status: 401 });

  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Use at least 10 characters." }, { status: 400 });
  }

  const user = await db.user.findUnique({ where: { id: session.userId } });
  if (!user) return NextResponse.json({ error: "Sign in first." }, { status: 401 });

  // A verified email means the session came from a link we sent to that mailbox, so the current
  // password is not required. Otherwise it is: a borrowed browser must not be able to take an account.
  if (!user.emailVerified) {
    if (!parsed.data.current || !verifyPassword(parsed.data.current, user.passwordHash)) {
      return NextResponse.json({ error: "Your current password does not match." }, { status: 403 });
    }
  }

  await db.user.update({
    where: { id: user.id },
    data: { passwordHash: hashPassword(parsed.data.password), failedLogins: 0, lockedUntil: null },
  });
  await logEvent("password.changed", user.email);
  return NextResponse.json({ ok: true });
}
