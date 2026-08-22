/**
 * `POST /activate` — exchange a licence key for a device-bound token.
 *
 * Enforces the seat limit, and names the number when it refuses. "This licence is already on 2
 * devices" tells a tester what to do next; "seat limit reached" makes them write to support.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { logEvent } from "@/lib/db";
import { claimDevice, licenceByKey, subscriptionToken } from "@/lib/licences";

export const runtime = "nodejs";

const Body = z.object({
  key: z.string().min(4).max(64),
  device_id: z.string().min(8).max(128),
  device_name: z.string().max(120).optional().default(""),
});

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ detail: "Enter your licence key." }, { status: 400 });
  }
  const { key, device_id: deviceId, device_name: deviceName } = parsed.data;

  let licence;
  try {
    licence = await licenceByKey(key);
  } catch {
    return NextResponse.json({ detail: "Try again shortly." }, { status: 503 });
  }

  if (!licence) {
    return NextResponse.json({ detail: "That licence key was not recognised." }, { status: 404 });
  }
  if (licence.status !== "active") {
    return NextResponse.json(
      { detail: "This subscription is not active. Renew it to keep using Nimbus." },
      { status: 403 },
    );
  }

  if (!(await claimDevice(licence, deviceId, deviceName.slice(0, 120)))) {
    return NextResponse.json(
      {
        detail:
          `This licence is already on ${licence.seatsTotal} devices. ` +
          "Open Nimbus on one of them and use Account \u2192 Deactivate this device.",
      },
      { status: 403 },
    );
  }

  await logEvent("licence.activated", `${licence.key} ${deviceId.slice(0, 12)}`);
  return NextResponse.json({ token: await subscriptionToken(licence, deviceId) });
}
