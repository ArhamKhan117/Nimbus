/**
 * `GET /download` — hand over the installer, to someone with an account.
 *
 * ## Why there is a gate, having removed one
 *
 * This required an account, then did not, and now does again. Both directions had a real argument and it
 * is worth keeping them both written down.
 *
 * The gate came off because **a licence key needs no account at all.** Anyone handed a key activates by
 * pasting it, with no email anywhere in the path, so for them a signup wall protected nothing and cost a
 * redirect away from the thing they had just clicked. A download button that navigates somewhere else is a
 * download button that failed.
 *
 * The gate is back because that describes a minority. Nearly everyone arrives on the **trial**, and the
 * trial needs a verified account and a six-digit code emailed to it before the application will open. So
 * an account is required within two minutes of launching regardless, and asking before a 152 MB download
 * is a better order than asking after it. The key holder is not really harmed either: they have an email
 * address, signing in takes one screen, and their key is then on the account page next to the download.
 *
 * What makes it tolerable is the **return target**. Signed-out visitors go to `/login?next=download`, and
 * `AuthForm` sends them straight back here once they are in, so the click they made is the click that
 * eventually happens. A gate without that is where the original complaint came from.
 *
 * ## Why the session read fails open
 *
 * `readSession` rather than `sessionState`: a cookie signature is enough to decide this, and the extra
 * database round trip `sessionState` makes to detect a deleted account would only add a way for the
 * download to break. A *throw* is treated as "let them through" for the same reason. Someone whose cookie
 * cannot be parsed is having a bad enough time already, and the licence gate in the application is still
 * there to enforce what actually needs enforcing.
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

import { readSession, siteUrl } from "@/lib/auth";
import { logEvent } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// The public source repository's own releases. GitHub does not serve release assets from a *private*
// repository to an unauthenticated visitor, so an earlier version of this pointed somewhere only the
// owner could load and the Download button 404'd for everyone else.
const FALLBACK =
  "https://github.com/ArhamKhan117/Nimbus/releases/latest/download/Nimbus-Windows-Setup.exe";

export async function GET() {
  // Three outcomes, and only the middle one is a redirect away from the installer.
  let who = "anonymous";
  let signedIn = false;
  try {
    const session = await readSession();
    if (session) {
      who = session.email;
      signedIn = true;
    }
  } catch {
    // Fails open on purpose. See the note above.
    who = "unreadable session";
    signedIn = true;
  }

  if (!signedIn) {
    await logEvent("download.gated", who);
    // Absolute, because `NextResponse.redirect` requires one. `siteUrl()` prefers SITE_URL and falls
    // back to the platform's own production hostname, so this is correct on a preview deployment too.
    return NextResponse.redirect(new URL("/login?next=download", siteUrl()), 302);
  }

  await logEvent("download.redirect", who);

  // `NIMBUS_DOWNLOAD_URL` is the one that matters in production. The fallback names a specific repository
  // and will be wrong the moment this moves, which is exactly why it is only a fallback.
  const target = process.env.NIMBUS_DOWNLOAD_URL ?? FALLBACK;
  return NextResponse.redirect(target, 302);
}
