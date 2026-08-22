/**
 * `GET /healthz` — liveness, plus the two things that silently break a deployment.
 *
 * A health check that only says "the process is up" would have passed on every misconfiguration worth
 * catching. This one signs a token and verifies it with the public half, so a **mismatched keypair**
 * — the failure that rejects every valid licence while looking completely healthy — is caught here
 * instead of by a tester. It also touches the database, because a licence API without a database is
 * not healthy in any useful sense.
 *
 * No secrets in the response. It reports whether things are configured, never what they are.
 */
import { NextResponse } from "next/server";

import { db } from "@/lib/db";
import { signClaims, verifyToken } from "@/lib/licence";
import { stripeConfigured } from "@/lib/stripe";
import { hostedCheckoutAvailable, manualTransferAvailable } from "@/lib/easypaisa";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  const checks: Record<string, string> = {};
  let ok = true;

  try {
    await db.$queryRaw`SELECT 1`;
    checks.database = "ok";
  } catch (error) {
    checks.database = `fail: ${String(error).slice(0, 120)}`;
    ok = false;
  }

  const publicKey = process.env.NIMBUS_LICENCE_PUBLIC_KEY?.trim();
  try {
    const token = signClaims({
      kind: "trial",
      plan: "healthcheck",
      expires_at: new Date().toISOString(),
      issued_at: new Date().toISOString(),
    });
    if (!publicKey) {
      checks.signing = "signs, but NIMBUS_LICENCE_PUBLIC_KEY is not set so the pair is unverified";
    } else {
      verifyToken(token, publicKey);
      checks.signing = "ok";
    }
  } catch (error) {
    checks.signing = `fail: ${String(error).slice(0, 120)}`;
    ok = false;
  }

  checks.stripe = stripeConfigured() ? "ok" : "not configured";
  checks.easypaisa_hosted = hostedCheckoutAvailable() ? "ok" : "not configured";
  checks.easypaisa_manual = manualTransferAvailable() ? "ok" : "not configured";
  checks.email = process.env.RESEND_API_KEY ? "ok" : "not configured";

  return NextResponse.json({ ok, checks }, { status: ok ? 200 : 503 });
}
