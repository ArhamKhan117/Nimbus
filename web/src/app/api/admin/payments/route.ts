/**
 * The admin surface for money that arrives out of band: list pending transfers, approve, reject.
 *
 * `GET` lists, `POST` decides. Both need `ADMIN_TOKEN` as a bearer token — this endpoint mints
 * licences, which makes it the second most sensitive thing here after the Stripe webhook. It is
 * bearer-protected rather than unauthenticated-and-obscure, and when `ADMIN_TOKEN` is unset it refuses
 * everyone rather than defaulting open.
 *
 * Approving is the same `ensureLicence` the card path uses, so an EasyPaisa tester gets an identical
 * licence — same seats, same signing key, same email. Nothing about Nimbus is different because of how
 * someone paid, and that is enforced by there being one code path rather than by intent.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { db, logEvent } from "@/lib/db";
import { sendLicenceEmail } from "@/lib/email";
import { addDays } from "@/lib/licence";
import { ensureLicence } from "@/lib/licences";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function authorised(request: Request): boolean {
  const expected = process.env.ADMIN_TOKEN;
  if (!expected) return false;
  return request.headers.get("authorization") === `Bearer ${expected}`;
}

export async function GET(request: Request) {
  if (!authorised(request)) {
    return NextResponse.json({ error: "Unauthorized." }, { status: 401 });
  }
  const payments = await db.manualPayment.findMany({
    where: { status: { in: ["pending", "initiated"] } },
    orderBy: { createdAt: "asc" },
    include: { user: { select: { email: true, name: true } } },
    take: 100,
  });
  return NextResponse.json({
    payments: payments.map((payment) => ({
      id: payment.id,
      email: payment.user.email,
      name: payment.user.name,
      method: payment.method,
      reference: payment.reference,
      note: payment.note,
      status: payment.status,
      createdAt: payment.createdAt.toISOString(),
    })),
  });
}

const Decision = z.object({
  id: z.string().min(1),
  action: z.enum(["approve", "reject"]),
  months: z.number().int().min(1).max(24).optional().default(1),
});

export async function POST(request: Request) {
  if (!authorised(request)) {
    return NextResponse.json({ error: "Unauthorized." }, { status: 401 });
  }

  const parsed = Decision.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "Bad request." }, { status: 400 });
  }

  const payment = await db.manualPayment.findUnique({
    where: { id: parsed.data.id },
    include: { user: true },
  });
  if (!payment) return NextResponse.json({ error: "Not found." }, { status: 404 });

  if (parsed.data.action === "reject") {
    await db.manualPayment.update({
      where: { id: payment.id },
      data: { status: "rejected", reviewedAt: new Date() },
    });
    await logEvent("manual.rejected", `${payment.user.email} ${payment.reference}`);
    return NextResponse.json({ ok: true });
  }

  const { licence, created } = await ensureLicence(
    payment.userId,
    addDays(31 * parsed.data.months),
    { source: payment.method },
  );

  await db.manualPayment.update({
    where: { id: payment.id },
    data: { status: "approved", reviewedAt: new Date() },
  });

  await sendLicenceEmail(payment.user.email, licence.key, {
    seats: licence.seatsTotal,
    renewsOn: licence.periodEnd.toISOString().slice(0, 10),
    method: payment.method === "bank" ? "bank transfer" : "EasyPaisa",
  });

  await logEvent(
    created ? "manual.licence_issued" : "manual.licence_extended",
    `${payment.user.email} ${licence.key}`,
  );
  return NextResponse.json({ ok: true, key: licence.key, created });
}
