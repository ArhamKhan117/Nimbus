/**
 * `GET /api/auth/callback?token=…&purpose=verify|login` — the one email-link endpoint.
 *
 * Verification and sign-in links share a mechanism because they are the same thing with a different
 * consequence: proof that whoever clicked controls the mailbox. Two half-implementations of that would
 * be two places to get single-use, expiry and hashing wrong.
 *
 * A used, expired or unknown token redirects to the login page with a readable reason. It never says
 * *which* of the three, because "expired" versus "unknown" tells someone holding a stolen link
 * whether it is worth trying more.
 */
import { NextResponse } from "next/server";

import { consumeToken, createSession, siteUrl } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  const url = new URL(request.url);
  const raw = url.searchParams.get("token") ?? "";
  const purpose = url.searchParams.get("purpose") === "verify" ? "verify" : "login";

  const userId = raw ? await consumeToken(raw, purpose) : null;
  if (!userId) {
    return NextResponse.redirect(`${siteUrl()}/login?problem=link`, 302);
  }

  const user = await db.user.update({
    where: { id: userId },
    data: {
      emailVerified: true,
      failedLogins: 0,
      lockedUntil: null,
    },
  });

  await createSession({ userId: user.id, email: user.email });
  await logEvent(purpose === "verify" ? "email.verified" : "login.link", user.email);

  return NextResponse.redirect(
    `${siteUrl()}/account?${purpose === "verify" ? "verified=1" : "signedin=1"}`,
    302,
  );
}
