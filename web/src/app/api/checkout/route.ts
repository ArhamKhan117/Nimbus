/**
 * `POST /api/checkout` — start a Stripe Checkout session for the signed-in user.
 *
 * Requires an account, which is the point of having accounts: `client_reference_id` carries our user
 * id into Stripe and back out through the webhook, so a payment can never arrive without our knowing
 * whose it is. Matching purchases to people by email afterwards is how testers end up with two
 * accounts and one licence.
 *
 * `customer_email` is prefilled so nobody pays with a different address than the one their key will be
 * emailed to — the single most common way this goes wrong.
 */
import { NextResponse } from "next/server";

import { readSession, siteUrl } from "@/lib/auth";
import { db, logEvent } from "@/lib/db";
import { stripeClient, stripePriceId } from "@/lib/stripe";

export const runtime = "nodejs";

export async function POST() {
  const session = await readSession();
  if (!session) {
    return NextResponse.json({ error: "Create an account first." }, { status: 401 });
  }

  const stripe = stripeClient();
  const price = stripePriceId();
  if (!stripe || !price) {
    return NextResponse.json(
      { error: "Card payments are not switched on yet. Use EasyPaisa or bank transfer." },
      { status: 503 },
    );
  }

  const user = await db.user.findUnique({ where: { id: session.userId } });
  if (!user) return NextResponse.json({ error: "Create an account first." }, { status: 401 });

  try {
    const checkout = await stripe.checkout.sessions.create({
      mode: "subscription",
      line_items: [{ price, quantity: 1 }],
      client_reference_id: user.id,
      customer: user.stripeCustomerId ?? undefined,
      customer_email: user.stripeCustomerId ? undefined : user.email,
      allow_promotion_codes: true,
      success_url: `${siteUrl()}/account?purchased=1`,
      cancel_url: `${siteUrl()}/#pricing`,
      subscription_data: { metadata: { userId: user.id } },
    });
    return NextResponse.json({ url: checkout.url });
  } catch (error) {
    await logEvent("stripe.checkout_failed", String(error).slice(0, 200));
    return NextResponse.json(
      { error: "Checkout is unavailable right now. Try again shortly." },
      { status: 503 },
    );
  }
}
