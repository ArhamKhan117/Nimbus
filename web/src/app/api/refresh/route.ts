/**
 * `POST /refresh` — the desktop app's silent 7-day revalidation.
 *
 * ## The most important status code on this site
 *
 * The client **clears** its cached licence on a 4xx and **keeps** it on a 5xx. So:
 *
 * * a revoked key, a lapsed subscription, a removed device → 4xx, and the licence goes;
 * * a database blip, a timeout, anything of ours → 503, and the licence stays.
 *
 * Getting that backwards turns a two-minute outage on our side into a lockout on a legitimate user's
 * machine. Every `catch` here returns 503 for that reason.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { claimDevice, licenceByKey, subscriptionToken } from "@/lib/licences";

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

  let licence;
  try {
    licence = await licenceByKey(parsed.data.key);
  } catch {
    return NextResponse.json({ detail: "Try again shortly." }, { status: 503 });
  }

  if (!licence) {
    return NextResponse.json({ detail: "That licence key was not recognised." }, { status: 404 });
  }
  if (licence.status !== "active" || licence.periodEnd < new Date()) {
    return NextResponse.json(
      { detail: "This subscription is no longer active." },
      { status: 403 },
    );
  }

  try {
    if (!(await claimDevice(licence, parsed.data.device_id, ""))) {
      return NextResponse.json(
        { detail: "This device is no longer on the licence." },
        { status: 403 },
      );
    }
    return NextResponse.json({ token: await subscriptionToken(licence, parsed.data.device_id) });
  } catch {
    return NextResponse.json({ detail: "Try again shortly." }, { status: 503 });
  }
}
