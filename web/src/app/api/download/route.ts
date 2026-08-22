/**
 * `GET /download` — hand over the installer. No account, no interstitial.
 *
 * ## Why the account gate was removed
 *
 * It used to require one, and the reasoning was sound at the time: the trial needs an email and a
 * six-digit code on first launch, so a download that skips the account is a download that stops working
 * two minutes later at a screen the person did not expect. Better to ask before the 152 MB than after it.
 *
 * What that reasoning missed is that **a licence key needs no account at all.** Anyone handed a key
 * activates by pasting it, with no email anywhere in the path, so for them the signup wall protected
 * nothing and cost a redirect away from the thing they had just clicked. A download button that navigates
 * somewhere else is a download button that failed.
 *
 * The old cost is still real for someone who wants the trial: they meet the account screen inside the
 * application instead of before the download. That is the better order. The person who cannot use it yet
 * finds out from the app, and the person who already has a key is not stopped on the way in.
 *
 * ## Why a redirect and not a proxy
 *
 * Serving a 152 MB installer through a serverless function would be slow, expensive and pointless when
 * GitHub Releases already has a CDN in front of it. Vercel also caps a function response well below this
 * size, so proxying is not merely wasteful, it does not work. The redirect additionally means the download
 * location moves with an environment variable rather than a deploy.
 *
 * A 302 straight to a release asset **is** a download: the browser follows it and saves the `.exe` without
 * ever showing a GitHub page. When people report "it takes me to GitHub", the redirect target is wrong and
 * they are looking at GitHub's 404 page. That happened here — the workflow uploaded
 * `Nimbus-Windows-Setup-v1.0.10.exe` while this pointed at `latest/download/Nimbus-Windows-Setup.exe`, and
 * `latest/download` matches on the exact filename. The workflow now publishes both names.
 */
import { NextResponse } from "next/server";

import { readSession } from "@/lib/auth";
import { logEvent } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The public source repository's own releases. GitHub does not serve release assets from a *private*
// repository to an unauthenticated visitor, so an earlier version of this pointed somewhere only the
// owner could load and the Download button 404'd for everyone else.
const FALLBACK =
  "https://github.com/ArhamKhan117/Nimbus/releases/latest/download/Nimbus-Windows-Setup.exe";

export async function GET() {
  // Read for the log, never to decide. `readSession` rather than `sessionState` on purpose: this no longer
  // gates anything, so the extra database round trip `sessionState` makes to detect a deleted account
  // would buy nothing and could only add a way for the download to fail.
  //
  // Wrapped because a signed-out visitor is the normal case now, and a cookie problem must not be able to
  // turn "give them the installer" into a 500.
  let who = "anonymous";
  try {
    const session = await readSession();
    if (session) who = session.email;
  } catch {
    who = "unreadable session";
  }
  await logEvent("download.redirect", who);

  // `NIMBUS_DOWNLOAD_URL` is the one that matters in production. The fallback names a specific repository
  // and will be wrong the moment this moves, which is exactly why it is only a fallback.
  const target = process.env.NIMBUS_DOWNLOAD_URL ?? FALLBACK;
  return NextResponse.redirect(target, 302);
}
