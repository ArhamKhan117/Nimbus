/**
 * `POST /api/desktop/login` — activate Nimbus with the email and password you bought with.
 *
 * ## Why this exists alongside licence keys
 *
 * The key is the better credential — it is unguessable, it has no reset flow, and it never travels
 * with a password. But "where is my key" is the single most predictable support question a paid desktop
 * app gets, and the answer "check your email from three weeks ago" is not one. Since a tester already
 * has an account here, letting them sign in is strictly less friction for them and no extra risk for
 * their key: the app receives the key back and stores **that**, so the password is used once and never
 * persisted anywhere.
 *
 * ## The seat check is the same one, deliberately
 *
 * Logging in from a third machine is refused exactly as pasting a key on a third machine is refused,
 * because both go through `claimDevice`. Two active devices per licence, keyed on the salted hash of
 * the machine's hardware identifiers that the client computes. So "no more than two live" is enforced
 * by hardware, not by counting logins — a distinction that matters, because a login count would be
 * defeated by signing out, and a hardware seat is not.
 *
 * ## Failure posture
 *
 * A wrong password is 401. No licence is 402 with a link, not 403 — "you do not have a subscription" is
 * a different sentence from "you cannot use this", and a tester whose card expired needs the first
 * one. Anything of ours is 503, which the client treats as "keep what you have and try later".
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import {
  LOCKOUT_MINUTES,
  MAX_FAILED_LOGINS,
  isLockedOut,
  normaliseEmail,
  siteUrl,
  verifyPassword,
} from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { errorBody } from "@/lib/errors";
import { PLAN_NAME, signClaims } from "@/lib/licence";
import { claimDevice, subscriptionToken } from "@/lib/licences";

export const runtime = "nodejs";

const Body = z.object({
  email: z.string().email().max(200),
  password: z.string().min(1).max(200),
  device_id: z.string().min(8).max(128),
  device_name: z.string().max(120).optional().default(""),
});


export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(errorBody("MISSING_FIELDS"), { status: 400 });
  }

  const email = normaliseEmail(parsed.data.email);

  let user;
  try {
    user = await db.user.findUnique({
      where: { email },
      include: { licences: { where: { status: "active" }, orderBy: { createdAt: "desc" }, take: 1 } },
    });
  } catch {
    return NextResponse.json(errorBody("UNAVAILABLE"), { status: 503 });
  }

  if (!user) {
    // Says so, matching the web login. Inside the app this matters more than on the site: someone at a
    // "sign in" prompt with no account has to be told to create one, or they will retype the same
    // password until they give up and ask for a refund on something they never bought.
    return NextResponse.json(errorBody("NO_ACCOUNT"), { status: 404 });
  }

  if (isLockedOut(user)) {
    return NextResponse.json(errorBody("LOCKED_OUT"), { status: 429 });
  }

  if (!verifyPassword(parsed.data.password, user.passwordHash)) {
    const failed = user.failedLogins + 1;
    await db.user.update({
      where: { id: user.id },
      data: {
        failedLogins: failed,
        lockedUntil:
          failed >= MAX_FAILED_LOGINS ? new Date(Date.now() + LOCKOUT_MINUTES * 60_000) : null,
      },
    });
    await logEvent("desktop.login_failed", `${email} attempt ${failed}`);
    return NextResponse.json(errorBody("WRONG_PASSWORD"), { status: 401 });
  }

  await db.user.update({
    where: { id: user.id },
    data: { failedLogins: 0, lockedUntil: null },
  });

  const licence = user.licences[0];
  if (!licence) {
    // No subscription, but this machine may still be inside its trial — someone who reinstalled, or
    // cleared their credentials, during those seven days. Handing back the trial token is the correct
    // answer and stops a trial user being told to buy something they are already using.
    const trial = await db.trial.findFirst({
      where: { deviceId: parsed.data.device_id, expiresAt: { gt: new Date() } },
    });
    if (trial) {
      return NextResponse.json({
        token: signClaims({
          kind: "trial",
          plan: `${PLAN_NAME} trial`,
          email: user.email,
          expires_at: trial.expiresAt.toISOString(),
          issued_at: new Date().toISOString(),
        }),
        kind: "trial",
      });
    }

    return NextResponse.json(
      errorBody("NO_SUBSCRIPTION", `That account has no active licence yet. See ${siteUrl()}/#pricing and Nimbus activates straight away.`),
      { status: 402 },
    );
  }

  if (!(await claimDevice(licence, parsed.data.device_id, parsed.data.device_name.slice(0, 120)))) {
    return NextResponse.json(
      errorBody("SEAT_LIMIT", `Your licence is already on ${licence.seatsTotal} computers. Open Nimbus on one of them and use Account \u2192 Deactivate this device.`),
      { status: 403 },
    );
  }

  await logEvent("desktop.login", `${email} ${parsed.data.device_id.slice(0, 12)}`);

  // The key travels back so the client can store it and revalidate with it later. That is what keeps
  // the password single-use: it is never written to the keyring, a file, or a log.
  return NextResponse.json({
    token: await subscriptionToken({ ...licence, user: { email: user.email } }, parsed.data.device_id),
    key: licence.key,
  });
}
