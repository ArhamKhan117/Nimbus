/**
 * `POST /deactivate` — free this machine's seat.
 *
 * With two seats, this is a button testers will actually use: replacing a laptop is normal. It has
 * to work first time and report the new count, so the app can say something true afterwards.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { logEvent } from "@/lib/db";
import { activeDeviceCount, licenceByKey, releaseDevice } from "@/lib/licences";

export const runtime = "nodejs";

const Body = z.object({
  key: z.string().min(4).max(64),
  device_id: z.string().min(8).max(128),
});

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ detail: "That request was not understood." }, { status: 400 });
  }

  const licence = await licenceByKey(parsed.data.key).catch(() => null);
  if (!licence) {
    return NextResponse.json({ detail: "That licence key was not recognised." }, { status: 404 });
  }

  await releaseDevice(licence.id, parsed.data.device_id);
  await logEvent("licence.deactivated", `${licence.key} ${parsed.data.device_id.slice(0, 12)}`);
  return NextResponse.json({ ok: true, seats_used: await activeDeviceCount(licence.id) });
}
