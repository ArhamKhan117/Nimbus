/**
 * What a licence *does*: get issued once, bind to devices, expire, come back.
 *
 * Everything here is shared by the three ways a licence can be created — a Stripe webhook, the
 * success page asking before the webhook lands, and a hand-approved EasyPaisa transfer — which is
 * exactly why it is one module. Three copies of "issue a licence" is three chances to issue two.
 */
import type { Licence } from "@prisma/client";

import { db, logEvent } from "./db";
import {
  DEFAULT_SEATS,
  PLAN_NAME,
  TRIAL_DAYS,
  addDays,
  newLicenceKey,
  signClaims,
  tokenExpiry,
} from "./licence";

/**
 * The tester's active licence, or a new one. `created` says which happened.
 *
 * Idempotent because it has to be: Stripe sends both `checkout.session.completed` and
 * `customer.subscription.created` for one purchase, and the success page may ask first. Minting a key
 * per arrival would leave a tester holding three, two of which count seats they are not using —
 * and no way to know which is theirs.
 */
export async function ensureLicence(
  userId: string,
  periodEnd: Date,
  options: { source?: string; stripeSubscriptionId?: string | null; seats?: number } = {},
): Promise<{ licence: Licence; created: boolean }> {
  const existing = await db.licence.findFirst({
    where: { userId, status: "active" },
    orderBy: { createdAt: "desc" },
  });

  if (existing) {
    const updated = await db.licence.update({
      where: { id: existing.id },
      data: {
        // Extend rather than replace: this path is also how a renewal arrives.
        periodEnd: periodEnd > existing.periodEnd ? periodEnd : existing.periodEnd,
        stripeSubscriptionId: options.stripeSubscriptionId ?? existing.stripeSubscriptionId,
      },
    });
    return { licence: updated, created: false };
  }

  const licence = await db.licence.create({
    data: {
      key: newLicenceKey(),
      userId,
      plan: PLAN_NAME,
      seatsTotal: options.seats ?? DEFAULT_SEATS,
      periodEnd,
      source: options.source ?? "stripe",
      stripeSubscriptionId: options.stripeSubscriptionId ?? null,
    },
  });
  await logEvent("licence.created", `${licence.key} ${options.source ?? "stripe"}`);
  return { licence, created: true };
}

export async function licenceByKey(key: string) {
  return db.licence.findUnique({
    where: { key: key.trim().toUpperCase() },
    include: { user: true },
  });
}

export async function activeDeviceCount(licenceId: string): Promise<number> {
  return db.device.count({ where: { licenceId, active: true } });
}

/**
 * Bind a device to a licence. `false` when the seat limit is already reached.
 *
 * **A device already on the licence is always let in, even at the limit.** It is re-activating, not
 * taking a new seat, and refusing it would lock a legitimate user out of their own machine after a
 * reinstall. With two seats that is the normal case rather than an edge case.
 */
export async function claimDevice(
  licence: { id: string; seatsTotal: number },
  deviceId: string,
  deviceName: string,
): Promise<boolean> {
  const existing = await db.device.findUnique({
    where: { licenceId_deviceId: { licenceId: licence.id, deviceId } },
  });

  if (existing) {
    await db.device.update({
      where: { id: existing.id },
      data: { active: true, deviceName: deviceName || existing.deviceName },
    });
    return true;
  }

  if ((await activeDeviceCount(licence.id)) >= Math.max(1, licence.seatsTotal)) return false;

  await db.device.create({
    data: { licenceId: licence.id, deviceId, deviceName },
  });
  return true;
}

export async function releaseDevice(licenceId: string, deviceId: string): Promise<void> {
  await db.device.updateMany({
    where: { licenceId, deviceId },
    data: { active: false },
  });
}

/** The signed subscription token for a licence bound to one device. */
export async function subscriptionToken(
  licence: Licence & { user: { email: string } },
  deviceId: string,
): Promise<string> {
  return signClaims({
    kind: "subscription",
    plan: licence.plan,
    email: licence.user.email,
    expires_at: tokenExpiry(licence.periodEnd).toISOString(),
    issued_at: new Date().toISOString(),
    seats_used: await activeDeviceCount(licence.id),
    seats_total: licence.seatsTotal,
    device_id: deviceId,
  });
}

/**
 * Start or resume a device's trial.
 *
 * Returns the *existing* expiry for a device that already has a trial running, rather than an error:
 * someone who reinstalls mid-trial has not used their trial up. Only an elapsed trial is refused, and
 * that is the caller's decision to make from `isNew` and the date.
 *
 * Truncated to the second because that is what gets stored — otherwise the token issued on the first
 * request and the one issued on a reinstall disagree about the same trial's expiry.
 */
export async function startTrial(
  deviceId: string,
  deviceName: string,
  userId?: string,
): Promise<{ expiresAt: Date; isNew: boolean }> {
  const existing = await db.trial.findUnique({ where: { deviceId } });
  if (existing) {
    // Record who asked, if we did not know before. Never overwrite an existing owner: the first
    // verified account on a machine is the one the trial belongs to, and letting a later address claim
    // it would make the row rewritable by anyone who can reach the endpoint.
    if (userId && !existing.userId) {
      await db.trial.update({ where: { deviceId }, data: { userId } });
    }
    return { expiresAt: existing.expiresAt, isNew: false };
  }

  const expiresAt = new Date(Math.floor(addDays(TRIAL_DAYS).getTime() / 1000) * 1000);
  await db.trial.create({ data: { deviceId, deviceName, expiresAt, userId: userId ?? null } });
  await logEvent("trial.started", `${deviceId.slice(0, 12)} ${userId ?? "anonymous"}`);
  return { expiresAt, isNew: true };
}
