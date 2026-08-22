/**
 * `POST /api/stripe/webhook` — subscriptions starting, renewing and lapsing.
 *
 * ## The most important line in this file
 *
 * **The signature is verified before anything is read.** Without it, this endpoint is an open "give me
 * a licence" API: anyone who knows the URL could POST a fake `checkout.session.completed`. The raw
 * body is used for that check, which is why this route reads `request.text()` and never `.json()` —
 * any reserialisation changes the bytes and every signature fails.
 *
 * ## Idempotency
 *
 * Stripe sends more than one event per purchase and retries on any non-2xx. `ensureLicence` returns the
 * tester's existing licence rather than minting another, so replays are harmless. Every branch
 * returns 200 even when it does nothing, because a 500 on an event we do not care about makes Stripe
 * retry it for days.
 */
import { NextResponse } from "next/server";
import type Stripe from "stripe";

import { db, logEvent } from "@/lib/db";
import { sendLicenceEmail } from "@/lib/email";
import { DEFAULT_SEATS, addDays } from "@/lib/licence";
import { ensureLicence } from "@/lib/licences";
import { stripeClient } from "@/lib/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function renews(subscription: Stripe.Subscription | null): Date {
  const seconds = subscription?.current_period_end;
  return seconds ? new Date(seconds * 1000) : addDays(31);
}

export async function POST(request: Request) {
  const stripe = stripeClient();
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!stripe || !secret) {
    return NextResponse.json({ error: "Webhooks are not configured." }, { status: 500 });
  }

  const signature = request.headers.get("stripe-signature") ?? "";
  const raw = await request.text();

  let event: Stripe.Event;
  try {
    event = stripe.webhooks.constructEvent(raw, signature, secret);
  } catch {
    return NextResponse.json({ error: "Bad signature." }, { status: 400 });
  }

  try {
    switch (event.type) {
      case "checkout.session.completed": {
        const checkout = event.data.object as Stripe.Checkout.Session;
        const userId = checkout.client_reference_id;
        if (!userId) {
          await logEvent("stripe.no_user", String(checkout.id));
          break;
        }

        const subscriptionId =
          typeof checkout.subscription === "string" ? checkout.subscription : null;
        const subscription = subscriptionId
          ? await stripe.subscriptions.retrieve(subscriptionId)
          : null;

        if (typeof checkout.customer === "string") {
          await db.user.update({
            where: { id: userId },
            data: { stripeCustomerId: checkout.customer },
          }).catch(() => undefined);
        }

        const { licence, created } = await ensureLicence(userId, renews(subscription), {
          source: "stripe",
          stripeSubscriptionId: subscriptionId,
        });
        const user = await db.user.findUnique({ where: { id: userId } });

        if (user && created) {
          await sendLicenceEmail(user.email, licence.key, {
            seats: licence.seatsTotal ?? DEFAULT_SEATS,
            renewsOn: licence.periodEnd.toISOString().slice(0, 10),
            method: "card",
          });
        }
        await logEvent(created ? "stripe.licence_issued" : "stripe.licence_reused", licence.key);
        break;
      }

      case "invoice.paid":
      case "invoice.payment_succeeded": {
        const invoice = event.data.object as Stripe.Invoice;
        const subscriptionId =
          typeof invoice.subscription === "string" ? invoice.subscription : null;
        if (!subscriptionId) break;
        const subscription = await stripe.subscriptions.retrieve(subscriptionId);
        await db.licence.updateMany({
          where: { stripeSubscriptionId: subscriptionId },
          data: { status: "active", periodEnd: renews(subscription) },
        });
        await logEvent("stripe.renewed", subscriptionId);
        break;
      }

      case "customer.subscription.deleted":
      case "customer.subscription.paused": {
        const subscription = event.data.object as Stripe.Subscription;
        await db.licence.updateMany({
          where: { stripeSubscriptionId: subscription.id },
          data: { status: "lapsed" },
        });
        await logEvent("stripe.lapsed", subscription.id);
        break;
      }

      default:
        // Everything else is acknowledged and ignored. A 500 here would have Stripe retrying an
        // event we have no opinion about for days.
        break;
    }
  } catch (error) {
    await logEvent("stripe.webhook_error", `${event.type}: ${String(error).slice(0, 200)}`);
    // 500 on purpose: this is a real failure and Stripe's retry is the recovery mechanism.
    return NextResponse.json({ error: "Handler failed." }, { status: 500 });
  }

  return NextResponse.json({ received: true });
}
