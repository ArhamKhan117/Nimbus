/**
 * `POST /api/easypaisa/initiate` — the automated EasyPaisa hosted checkout.
 *
 * Dormant until `EASYPAISA_STORE_ID` and `EASYPAISA_HASH_KEY` exist, which needs a merchant account
 * (see `src/lib/easypaisa.ts` for what that involves). Until then this returns 503 and the UI shows the
 * manual transfer route instead — which works today.
 *
 * Returns the fields rather than redirecting, because EasyPaisa's hosted checkout is reached by a form
 * **POST** from the tester's browser, not a GET redirect. The page builds a hidden form and submits
 * it. `orderRefNum` is stored first so the postback can be matched to a person; an unmatched payment is
 * money received with nobody to give a licence to.
 */
import { NextResponse } from "next/server";

import { readSession, siteUrl } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import {
  easypaisaPostUrl,
  hostedCheckoutAvailable,
  hostedCheckoutFields,
} from "@/lib/easypaisa";

export const runtime = "nodejs";

export async function POST() {
  const session = await readSession();
  if (!session) return NextResponse.json({ error: "Create an account first." }, { status: 401 });

  if (!hostedCheckoutAvailable()) {
    return NextResponse.json(
      {
        error:
          "EasyPaisa checkout is not switched on yet. Send the transfer and submit your reference instead.",
      },
      { status: 503 },
    );
  }

  const payment = await db.manualPayment.create({
    data: {
      userId: session.userId,
      method: "easypaisa-hosted",
      reference: "",
      status: "initiated",
      amount: process.env.PRICE_PKR ?? "",
    },
  });

  try {
    const fields = hostedCheckoutFields(payment.id, `${siteUrl()}/api/easypaisa/callback`);
    await logEvent("easypaisa.initiated", `${session.email} ${payment.id}`);
    return NextResponse.json({ action: easypaisaPostUrl(), fields });
  } catch (error) {
    await db.manualPayment.update({ where: { id: payment.id }, data: { status: "failed" } });
    await logEvent("easypaisa.initiate_failed", String(error).slice(0, 200));
    return NextResponse.json(
      { error: "EasyPaisa checkout could not be started. Try the transfer route." },
      { status: 503 },
    );
  }
}
