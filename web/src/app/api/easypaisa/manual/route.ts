/**
 * `POST /api/easypaisa/manual` — "I have sent the money, here is the reference."
 *
 * This is the manual path, and it is honest about being manual: the sender
 * gets an email saying we will confirm by hand, and then we do. A spinner pretending to verify a
 * transfer nobody has looked at would be worse than the truth.
 *
 * One pending claim per person. Without that, a mistyped reference means two rows to reconcile and a
 * real chance of issuing two licences for one payment.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import { readSession } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { sendManualPaymentReceivedEmail } from "@/lib/email";

export const runtime = "nodejs";

const Body = z.object({
  method: z.enum(["easypaisa", "bank"]),
  reference: z.string().min(4).max(64),
  note: z.string().max(300).optional().default(""),
});

export async function POST(request: Request) {
  const session = await readSession();
  if (!session) return NextResponse.json({ error: "Create an account first." }, { status: 401 });

  const parsed = Body.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Enter the transaction ID from your receipt." },
      { status: 400 },
    );
  }

  const pending = await db.manualPayment.findFirst({
    where: { userId: session.userId, status: "pending" },
  });
  if (pending) {
    return NextResponse.json({
      ok: true,
      already: true,
      message: "We already have a payment from you waiting to be checked.",
    });
  }

  const payment = await db.manualPayment.create({
    data: {
      userId: session.userId,
      method: parsed.data.method,
      reference: parsed.data.reference.trim(),
      note: parsed.data.note.trim(),
      amount: process.env.PRICE_PKR ?? "",
    },
  });

  await sendManualPaymentReceivedEmail(session.email, payment.reference);
  await logEvent("manual.submitted", `${session.email} ${payment.method} ${payment.reference}`);
  return NextResponse.json({ ok: true });
}
