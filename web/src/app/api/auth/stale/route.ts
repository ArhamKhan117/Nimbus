/**
 * `GET /api/auth/stale` — throw away a session cookie whose account no longer exists.
 *
 * ## Why this is a route and not a line in the page
 *
 * A Next.js server component cannot modify cookies; only a route handler or a server action can. So the
 * page that *discovers* the problem cannot fix it, and has to redirect here to have the cookie removed
 * before landing on sign-in. One extra hop, and it terminates.
 *
 * ## Why GET is safe here when it is not safe for logout
 *
 * `/api/auth/logout` is POST-only because a GET that ends a session can be fired by an `<img>` tag on
 * another site. This route has that property removed by construction: it **re-checks the state itself**
 * and only clears when the account is genuinely gone. Pointed at a live session it does nothing at all,
 * so the worst a forged request achieves is deleting a cookie that had already stopped working.
 */
import { NextResponse } from "next/server";

import { clearSession, sessionState, siteUrl } from "@/lib/auth";
import { logEvent } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const { state, session } = await sessionState();

  if (state === "stale") {
    await clearSession();
    // Worth recording: in production a stale cookie means an account was deleted, and this is the only
    // trace that whoever held it was pushed back to sign-in.
    await logEvent("session.stale_cleared", session?.email ?? "");
    return NextResponse.redirect(`${siteUrl()}/login?problem=stale`, 303);
  }

  // Nothing to do. A signed-in visitor goes to their account, everyone else to the front page.
  return NextResponse.redirect(`${siteUrl()}/${state === "ok" ? "account" : ""}`, 303);
}
