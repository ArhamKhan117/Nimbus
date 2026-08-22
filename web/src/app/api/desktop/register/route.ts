/**
 * `POST /api/desktop/register` — create an account from inside the app and email a 6-digit code.
 *
 * ## Why the trial now needs an account
 *
 * It did not before, and "no account, no card, just run it" was genuinely the lowest-friction trial
 * anyone could offer. What it could not do is tell us *who* is trying Nimbus. A device hash is enough to
 * stop a second trial and useless for everything else: no way to email someone whose trial is ending, no
 * way to answer "I registered, where is my key" from a trial user, and no way to ask a tester who
 * stopped using it why. An anonymous funnel is a funnel you cannot see.
 *
 * The cost is one email and one code typed into a window, and the trial is otherwise unchanged: still
 * seven days, still no card, still device-bound so a second address earns nothing.
 *
 * ## Why this is a separate endpoint from the web signup
 *
 * Different outcome. The web signup creates a session cookie for a browser; this returns nothing but
 * "check your email", because the app has no cookie jar and the next step is a code, not a redirect. It
 * also carries the device, so the trial can start the instant the code is verified rather than needing a
 * third call.
 *
 * An existing, already-verified email is not an error here — it is someone reinstalling. They get a code
 * too, and `/api/desktop/verify` will hand them whatever they are entitled to.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { hashPassword, issueCode, normaliseEmail } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { errorBody } from "@/lib/errors";
import { sendCodeEmail } from "@/lib/email";

export const runtime = "nodejs";

const Body = z.object({
  email: z.string().email().max(200),
  password: z.string().min(10).max(200),
  device_id: z.string().min(8).max(128),
  device_name: z.string().max(120).optional().default(""),
});

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(
      { detail: "Enter a valid email and a password of at least 10 characters." },
      { status: 400 },
    );
  }

  const email = normaliseEmail(parsed.data.email);

  let user;
  try {
    user = await db.user.findUnique({ where: { email } });
    if (!user) {
      user = await db.user.create({
        data: { email, passwordHash: hashPassword(parsed.data.password) },
      });
      await logEvent("desktop.registered", email);
    } else {
      // Deliberately **not** an error, and deliberately not a password check either. Someone
      // reinstalling has no session and may have forgotten which password they used; the code proves
      // they own the mailbox, which is the thing that matters. It also does not update the stored
      // password, so this cannot be used to take over an account by knowing an address.
      await logEvent("desktop.register_existing", email);
    }

    const code = await issueCode(user.id, "verify");
    const sent = await sendCodeEmail(email, code, "verify");
    if (!sent) {
      // The account exists, but a code nobody received is a dead end: the very next screen asks for six
      // digits that will never arrive. Reported rather than swallowed, unlike the *verification* email on
      // the website — there, the account still works without it.
      await logEvent("desktop.code_email_failed", email);
      return NextResponse.json(errorBody("EMAIL_FAILED"), { status: 502 });
    }
  } catch {
    return NextResponse.json(errorBody("UNAVAILABLE"), { status: 503 });
  }

  return NextResponse.json({
    ok: true,
    email,
    // Said plainly so the app can show it, including where to look and how long it lasts.
    detail: `We sent a 6-digit code to ${email}. It expires in 20 minutes.`,
  });
}
