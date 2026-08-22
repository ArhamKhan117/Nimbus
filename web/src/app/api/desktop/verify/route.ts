/**
 * `POST /api/desktop/verify` — check the 6-digit code, verify the email, and start the trial.
 *
 * One call rather than three, because from the user's side it is one action: they type six digits and
 * Nimbus starts working. Verifying and then separately asking for a trial would give the flow two ways to
 * fail halfway, and a half-verified account with no trial is a support conversation.
 *
 * ## What gets decided here
 *
 * 1. **Is the code right?** Wrong, expired and too-many-attempts are three different answers, because
 *    they need three different next actions from the person reading them.
 * 2. **Does this machine already have a licence?** If the account has an active subscription, they get a
 *    subscription token — someone who paid and then reinstalled should not be handed a trial.
 * 3. **Otherwise, the trial.** Keyed on the device, forever. The account records who asked; the device
 *    decides whether they may. A second address on the same machine gets a refusal, which is the whole
 *    anti-abuse design and the reason the trial table has no email column.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { checkCode, normaliseEmail } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { PLAN_NAME, signClaims } from "@/lib/licence";
import { claimDevice, startTrial, subscriptionToken } from "@/lib/licences";

export const runtime = "nodejs";

const Body = z.object({
  email: z.string().email().max(200),
  code: z.string().min(4).max(12),
  device_id: z.string().min(8).max(128),
  device_name: z.string().max(120).optional().default(""),
});

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ detail: "Enter the 6-digit code from your email." }, { status: 400 });
  }

  const email = normaliseEmail(parsed.data.email);
  const deviceId = parsed.data.device_id;
  const deviceName = parsed.data.device_name.slice(0, 120);

  let user;
  try {
    user = await db.user.findUnique({
      where: { email },
      include: { licences: { where: { status: "active" }, orderBy: { createdAt: "desc" }, take: 1 } },
    });
  } catch {
    return NextResponse.json({ detail: "Try again shortly." }, { status: 503 });
  }

  if (!user) {
    return NextResponse.json(
      { detail: "We have no account for that email. Create one first." },
      { status: 404 },
    );
  }

  const result = await checkCode(user.id, parsed.data.code, "verify");
  if (result !== "ok") {
    const detail = {
      wrong: "That code is not right. Check the email and try again.",
      expired: "That code has expired. Ask for a new one.",
      "too-many": "Too many wrong attempts. Ask for a new code.",
    }[result];
    return NextResponse.json({ detail }, { status: result === "wrong" ? 401 : 403 });
  }

  await db.user.update({
    where: { id: user.id },
    data: { emailVerified: true, failedLogins: 0, lockedUntil: null },
  });
  await logEvent("desktop.verified", email);

  // A licence beats a trial. Someone already activated who then reinstalled must not be handed seven days.
  const licence = user.licences[0];
  if (licence) {
    if (!(await claimDevice(licence, deviceId, deviceName))) {
      return NextResponse.json(
        {
          detail:
            `Your licence is already on ${licence.seatsTotal} computers. ` +
            "Open Nimbus on one of them and use Account \u2192 Deactivate this device.",
        },
        { status: 403 },
      );
    }
    return NextResponse.json({
      token: await subscriptionToken({ ...licence, user: { email: user.email } }, deviceId),
      key: licence.key,
      kind: "subscription",
    });
  }

  const { expiresAt, isNew } = await startTrial(deviceId, deviceName, user.id);
  if (!isNew && expiresAt <= new Date()) {
    return NextResponse.json(
      {
        detail:
          "The free trial on this computer has already been used. A licence key activates it again.",
      },
      { status: 403 },
    );
  }

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
