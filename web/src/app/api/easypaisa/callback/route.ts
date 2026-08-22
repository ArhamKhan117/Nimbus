/**
 * `POST|GET /api/easypaisa/callback` — EasyPaisa's postback after a hosted checkout.
 *
 * ## Why this does not issue the licence by itself
 *
 * A postback is an HTTP request from something claiming to be EasyPaisa. Unlike Stripe, there is no
 * webhook signing secret here: the guide's integrity mechanism is the hashed *request*, not a signed
 * response. So a successful-looking postback is **evidence, not proof**, and this route records it and
 * marks the payment for review rather than minting a licence on it.
 *
 * That is a deliberate trade. The cost is that a card-fast experience becomes a few-hours experience on
 * this rail; the alternative is an endpoint anyone can POST to for a free licence. When a merchant
 * account exists, the right upgrade is server-side confirmation against EasyPaisa's inquiry API before
 * approval — noted in `web/README.md` rather than pretended to here.
 *
 * Both verbs are handled because gateway postbacks are inconsistent about which they use.
 */
import { NextResponse } from "next/server";

import { db, logEvent } from "@/lib/db";
import { siteUrl } from "@/lib/auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function record(parameters: Record<string, string>): Promise<void> {
  const orderRefNum = parameters.orderRefNum ?? parameters.orderRefNumber ?? "";
  const status = parameters.status ?? parameters.responseCode ?? "";
  const transaction = parameters.transactionId ?? parameters.tid ?? "";

  await logEvent("easypaisa.callback", `${orderRefNum} status=${status} tid=${transaction}`);

  if (!orderRefNum) return;
  const payment = await db.manualPayment.findUnique({ where: { id: orderRefNum } });
  if (!payment) {
    await logEvent("easypaisa.callback_unmatched", orderRefNum);
    return;
  }

  const paid = status === "0000" || status.toLowerCase() === "paid";
  await db.manualPayment.update({
    where: { id: payment.id },
    data: {
      // "pending" means a human still has to approve it. See the note above: a postback is evidence.
      status: paid ? "pending" : "failed",
      reference: transaction || payment.reference,
      note: `postback status=${status}`.slice(0, 300),
    },
  });
}

export async function POST(request: Request) {
  const form = await request.formData().catch(() => null);
  const parameters: Record<string, string> = {};
  form?.forEach((value, key) => {
    parameters[key] = String(value);
  });
  await record(parameters);
  return NextResponse.redirect(`${siteUrl()}/account?easypaisa=1`, 303);
}

export async function GET(request: Request) {
  const parameters: Record<string, string> = {};
  new URL(request.url).searchParams.forEach((value, key) => {
    parameters[key] = value;
  });
  await record(parameters);
  return NextResponse.redirect(`${siteUrl()}/account?easypaisa=1`, 302);
}
