/**
 * `POST /api/auth/link` — email me a sign-in link.
 *
 * This is the forgot-password path, and it is deliberately not called that. There is no reset form, no
 * second token type and no "security question": clicking the link signs you in, and you can set a new
 * password from the account page once you are there. One mechanism instead of two.
 *
 * Always returns success, whether or not the address exists. Anything else is an enumeration oracle.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { issueToken, normaliseEmail, siteUrl } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { sendSignInLinkEmail } from "@/lib/email";

export const runtime = "nodejs";

const Body = z.object({ email: z.string().email().max(200) });

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Enter a valid email address." }, { status: 400 });
  }

  const email = normaliseEmail(parsed.data.email);
  const user = await db.user.findUnique({ where: { email } });

  if (user) {
    const raw = await issueToken(user.id, "login", 30);
    await sendSignInLinkEmail(email, `${siteUrl()}/api/auth/callback?token=${raw}&purpose=login`);
    await logEvent("login.link_requested", email);
  }

  return NextResponse.json({ ok: true });
}
