/**
 * `POST /trial` — a 7-day trial token for a device that has not had one.
 *
 * Reached as `/trial` too, via a rewrite in `next.config.ts`: that is the path already baked into
 * every installer that has shipped.
 *
 * Keyed on `device_id` and nothing else, which is where trial abuse is actually stopped. The client
 * sends a salted SHA-256 of the machine GUID and volume serial, never the raw values, so this table
 * holds nothing that identifies a person or correlates outside Nimbus. A new email address earns no
 * second trial because the email was never what a trial was counted against.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { db } from "@/lib/db";
import { PLAN_NAME, signClaims } from "@/lib/licence";
import { startTrial } from "@/lib/licences";

export const runtime = "nodejs";

const Body = z.object({
  device_id: z.string().min(8).max(128),
  device_name: z.string().max(120).optional().default(""),
});

export async function POST(request: Request) {
  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ detail: "That request was not understood." }, { status: 400 });
  }

  try {
    // **No new trials from here.** A trial now begins at `/api/desktop/verify`, after a 6-digit code
    // has proved the person owns an email address. This endpoint only re-issues a token for a machine
    // whose trial already exists — which is the reinstall case, and the reason it still exists at all:
    // an installer already in someone's hands calls this path.
    const existing = await db.trial.findUnique({
      where: { deviceId: parsed.data.device_id },
    });
    if (!existing) {
      return NextResponse.json(
        {
          detail:
            "Create a free account in Nimbus to start your 7-day trial. It takes an email and a code.",
        },
        { status: 403 },
      );
    }

    const { expiresAt, isNew } = await startTrial(
      parsed.data.device_id,
      parsed.data.device_name.slice(0, 120),
    );

    if (!isNew && expiresAt <= new Date()) {
      return NextResponse.json(
        {
          detail:
            "Your 7-day trial on this computer has ended. A licence key activates it again.",
        },
        { status: 403 },
      );
    }

    return NextResponse.json({
      token: signClaims({
        kind: "trial",
        plan: `${PLAN_NAME} trial`,
        expires_at: expiresAt.toISOString(),
        issued_at: new Date().toISOString(),
      }),
    });
  } catch {
    // 5xx, not 4xx: the client keeps its cached licence on a 5xx and clears it on a 4xx, so an
    // infrastructure fault must never be reported as a refusal.
    return NextResponse.json({ detail: "Try again shortly." }, { status: 503 });
  }
}
