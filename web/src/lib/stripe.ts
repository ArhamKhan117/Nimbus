/**
 * Stripe, kept behind a null check.
 *
 * `stripeClient()` returns `null` when no key is configured rather than throwing, so the site is
 * fully usable before Stripe exists: the card button falls back to the manual-transfer route. Neither
 * rail is connected in this deployment, so this degrades rather than 500s by default.
 */
import Stripe from "stripe";

export function stripeClient(): Stripe | null {
  const key = process.env.STRIPE_SECRET_KEY;
  if (!key) return null;
  return new Stripe(key, { apiVersion: "2025-02-24.acacia" });
}

export function stripePriceId(): string | null {
  return process.env.STRIPE_PRICE_ID || null;
}

export function stripeConfigured(): boolean {
  return Boolean(stripeClient() && stripePriceId());
}
