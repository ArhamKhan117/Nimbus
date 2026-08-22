import { NextResponse } from "next/server";

import { clearSession, siteUrl } from "@/lib/auth";

export const runtime = "nodejs";

/** POST, not GET: a link that logs you out can be triggered by an image tag on another site. */
export async function POST() {
  await clearSession();
  return NextResponse.redirect(`${siteUrl()}/`, 303);
}
