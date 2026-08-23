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
 * ## Signing in can also start the trial
 *
 * It did not, and that was a defect rather than a policy. A verified account signing in on a machine
 * that had never had a trial was refused with "that account has no active licence yet", while the machine
 * itself was perfectly eligible, and the only way through was to ask for another six-digit code. That is
 * the flow the person had already completed once.
 *
 * It costs nothing to allow, because **the trial is counted against the device**. An account that starts
 * a trial on a second machine has spent that machine's one and only trial, and a verified password proves
 * ownership of an address exactly as a code does. So this is a second door into the same room.
 *
 * ## Failure posture
 *
 * A wrong password is 401. An unverified address is 403, phrased as "confirm your email" rather than as
 * anything about licences, because that is the action which resolves it. A spent trial is 402 and says so
 * as a spent trial, not as a missing licence: those are different situations and only one of them is the
 * person's own history. Anything of ours is 503, which the client treats as "keep what you have and try
 * later".
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
import { claimDevice, startTrial, subscriptionToken } from "@/lib/licences";

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
    // No subscription. Two cases, and this used to handle only the first.
    //
    // **A trial already running on this machine.** Someone who reinstalled, or cleared their
    // credentials, inside the seven days. Handing the trial token back stops a trial user being told to
    // buy something they are already using.
    //
    // **A machine that has never had a trial.** This returned 402 "no active licence yet", which was
    // wrong and was reported as such: the account was verified, the machine was eligible, and the only
    // way through was to ask for another six-digit code -- the flow that person had already completed
    // once and had no reason to expect again.
    //
    // Starting it here does not weaken anything, because the trial is counted against the **device**.
    // An account that seeds a trial on a second machine has spent that machine's one and only trial, and
    // a verified password proves ownership of the address exactly as a code does. It is a second door to
    // the same room, not a second trial.
    if (!user.emailVerified) {
      // Refused with the action that resolves it. "No active licence" would be true and useless: what
      // this person has to do is confirm their address, and nothing about licences tells them that.
      return NextResponse.json(
        errorBody(
          "NOT_VERIFIED",
          "Confirm your email address first. Use the trial button above to have a new 6-digit code sent, then enter it.",
        ),
        { status: 403 },
      );
    }

    const { expiresAt, isNew } = await startTrial(
      parsed.data.device_id,
      parsed.data.device_name.slice(0, 120),
      user.id,
    );

    if (!isNew && expiresAt <= new Date()) {
      // The one genuine refusal left: this machine has had its trial and it is over. Named as a spent
      // trial rather than as a missing licence, because those are different situations and only one of
      // them is the person's own history.
      return NextResponse.json(
        errorBody(
          "NO_SUBSCRIPTION",
          `The free trial on this computer has ended. A licence key activates it again, and the plan is at ${siteUrl()}/#pricing.`,
        ),
        { status: 402 },
      );
    }

    await logEvent(isNew ? "desktop.trial_from_login" : "desktop.login_trial", email);
    return NextResponse.json({
      token: signClaims({
        kind: "trial",
        plan: `${PLAN_NAME} trial`,
        email: user.email,
        expires_at: expiresAt.toISOString(),
        issued_at: new Date().toISOString(),
      }),
      kind: "trial",
    });
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
