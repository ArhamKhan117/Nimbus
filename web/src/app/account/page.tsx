import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { BuyButton } from "@/components/BuyButton";
import { Footer, Header } from "@/components/Chrome";
import { LicenceKey } from "@/components/LicenceKey";
import { readSession } from "@/lib/auth";
import { db } from "@/lib/db";
import { TRIAL_DAYS } from "@/lib/licence";
import { stripeConfigured } from "@/lib/stripe";

export const metadata: Metadata = { title: "Your account" };
export const dynamic = "force-dynamic";

/**
 * "12 days left", from a period end.
 *
 * Rounded **up**, to match `licensing._state_from_claims` in the desktop app. Flooring makes a
 * licence issued moments ago read one day short, because it expires in 29.999 days rather than 30 —
 * and the app and the website disagreeing about how long someone has left is the kind of small
 * inconsistency that makes people distrust both numbers.
 */
function daysLeft(periodEnd: Date): string {
  const remaining = Math.max(0, Math.ceil((periodEnd.getTime() - Date.now()) / 86_400_000));
  if (remaining === 0) return "ends today";
  return `${remaining} day${remaining === 1 ? "" : "s"} left`;
}

/**
 * The page a tester comes back to.
 *
 * Its whole job is that **the licence key is never lost**. Email can be deleted, filtered or sent to a
 * dead address; this is the durable copy, and it is the first thing on the page rather than behind a
 * tab. Everything else here — devices, renewal date, payment state — exists to answer "is my thing
 * working", which is the only other question anyone arrives with.
 */
export default async function AccountPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const session = await readSession();
  if (!session) redirect("/login");

  const parameters = await searchParams;
  const justPurchased = "purchased" in parameters;
  const justVerified = "verified" in parameters;
  const easypaisaReturn = "easypaisa" in parameters;

  const user = await db.user.findUnique({
    where: { id: session.userId },
    include: {
      licences: {
        orderBy: { createdAt: "desc" },
        include: { devices: { where: { active: true }, orderBy: { lastSeen: "desc" } } },
      },
      payments: { orderBy: { createdAt: "desc" }, take: 3 },
    },
  });
  // A signed cookie whose account has been deleted. Sending them to /login was the other half of a
  // redirect loop, because /login saw the same cookie and sent them straight back. Go via the one place
  // that can actually remove it.
  if (!user) redirect("/api/auth/stale");

  const licence = user.licences.find((candidate) => candidate.status === "active") ?? null;
  const pending = user.payments.find((payment) => ["pending", "initiated"].includes(payment.status));

  return (
    <>
      <Header />

      <main id="main" className="mx-auto max-w-3xl px-6 py-14">
        <p className="eyebrow">Account</p>
        <h1 className="mt-2 text-[clamp(1.7rem,4vw,2.3rem)] font-semibold tracking-[-0.02em]">
          {user.name ? `Hello, ${user.name}` : user.email}
        </h1>

        {justVerified ? (
          <p className="mt-5 rounded-[8px] border border-accent/40 bg-accent/10 px-4 py-3 text-[15px]">
            Email confirmed.
          </p>
        ) : null}

        {/* Stripe's webhook is asynchronous, so a tester can land here a second before their licence
            exists. Saying so is better than showing them an empty page and letting them assume the
            worst about a payment they just made. */}
        {justPurchased && !licence ? (
          <p className="mt-5 rounded-[8px] border border-accent/40 bg-accent/10 px-4 py-3 text-[15px]">
            Payment received. Your licence key appears here within a few seconds &mdash; refresh if it
            has not. It is emailed to you as well.
          </p>
        ) : null}

        {easypaisaReturn ? (
          <p className="mt-5 rounded-[8px] border border-accent/40 bg-accent/10 px-4 py-3 text-[15px]">
            Thanks. We are confirming your EasyPaisa payment by hand and will email your key as soon as
            it clears.
          </p>
        ) : null}

        {/* --- the licence ------------------------------------------------ */}
        <section className="surface grain relative mt-8 overflow-hidden p-6 sm:p-7">
          <p className="eyebrow">Your licence key</p>

          {licence ? (
            <>
              <div className="mt-4">
                <LicenceKey licenceKey={licence.key} />
              </div>

              <dl className="mt-6 grid gap-x-8 gap-y-3 text-[15px] sm:grid-cols-2">
                <div className="flex justify-between gap-4 border-b border-line pb-2">
                  <dt className="text-ink-2">Plan</dt>
                  <dd>{licence.plan}</dd>
                </div>
                {/* The date and the countdown. A date on its own makes the reader work out how long
                    they have left, which is the one thing they came here to find out. */}
                <div className="flex justify-between gap-4 border-b border-line pb-2">
                  <dt className="text-ink-2">Renews</dt>
                  <dd>
                    {licence.periodEnd.toISOString().slice(0, 10)}
                    <span aria-hidden className="px-[0.5em] text-ink-3/70">
                      &middot;
                    </span>
                    <span className="text-ink-2">{daysLeft(licence.periodEnd)}</span>
                  </dd>
                </div>
                <div className="flex justify-between gap-4 border-b border-line pb-2">
                  <dt className="text-ink-2">Devices</dt>
                  <dd>
                    {licence.devices.length} of {licence.seatsTotal}
                  </dd>
                </div>
                <div className="flex justify-between gap-4 border-b border-line pb-2">
                  <dt className="text-ink-2">Paid by</dt>
                  <dd className="capitalize">{licence.source.replace("-", " ")}</dd>
                </div>
              </dl>

              {licence.devices.length ? (
                <ul className="mt-6 space-y-2 text-[15px]">
                  {licence.devices.map((device) => (
                    <li
                      key={device.id}
                      className="flex flex-wrap items-center justify-between gap-2 rounded-[8px] border border-line bg-sunken/60 px-4 py-2.5"
                    >
                      <span>{device.deviceName || "A Windows PC"}</span>
                      <span className="font-mono text-[13px] text-ink-3">
                        last seen {device.lastSeen.toISOString().slice(0, 10)}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : null}

              <p className="mt-6 text-[14px] text-ink-3">
                Out of devices? Open Nimbus on one you no longer use and choose{" "}
                <span className="text-ink-2">Account &rarr; Deactivate this device</span>. The seat is
                free again immediately.
              </p>
            </>
          ) : pending ? (
            <>
              <p className="mt-3 text-ink-2">
                We have your {pending.method === "bank" ? "bank transfer" : "EasyPaisa"} reference{" "}
                <span className="font-mono text-ink">{pending.reference || "\u2014"}</span> and are
                checking it by hand. Your key appears here and arrives by email, usually within a few
                hours.
              </p>
              <p className="mt-4 text-[14px] text-ink-3">
                Submitted {pending.createdAt.toISOString().slice(0, 16).replace("T", " ")} UTC.
              </p>
            </>
          ) : (
            <>
              {/* This said "the trial needs no account at all", which stopped being true the moment the
                  trial moved behind email verification. Stale copy that contradicts the product is worse
                  than no copy: it tells someone their next step is something the app will refuse. */}
              <p className="mt-3 text-ink-2">
                No licence yet. Your free trial starts inside the app &mdash; download Nimbus, sign in
                with this email, and you get {TRIAL_DAYS} days.
              </p>
              <div className="mt-6 flex flex-wrap items-center gap-3">
                <BuyButton signedIn cardsEnabled={stripeConfigured()} />
                <Link href="/pay" className="btn">
                  EasyPaisa or bank transfer
                </Link>
              </div>
            </>
          )}
        </section>

        {/* --- get the app ------------------------------------------------ */}
        <section className="surface grain relative mt-4 overflow-hidden p-6 sm:p-7">
          <p className="eyebrow">The app</p>
          <h2 className="mt-3 text-[1.1rem] font-semibold">Install and activate</h2>
          <ol className="mt-3 list-decimal space-y-2 pl-5 text-ink-2">
            <li>
              <Link href="/download" prefetch={false} className="text-accent hover:text-accent-hover">
                Download Nimbus for Windows
              </Link>{" "}
              and run the installer. No admin prompt.
            </li>
            {/* Two different sets of steps, because an activated licence and a trial genuinely do different
                things — and telling someone with no key to "paste the key above" is the kind of
                instruction that makes a person think they have missed an email. */}
            {licence ? (
              <>
                <li>
                  On first launch choose <span className="text-ink">I have a licence key</span>.
                </li>
                <li>Paste the key above. Nimbus checks it once, then works offline.</li>
              </>
            ) : (
              <>
                <li>
                  On first launch, enter <span className="text-ink">{user.email}</span> and your password,
                  then press <span className="text-ink">Start the {TRIAL_DAYS}-day trial</span>.
                </li>
                <li>
                  We email a 6-digit code. Type it into Nimbus and the trial begins &mdash; no card
                  needed.
                </li>
              </>
            )}
          </ol>
        </section>

        {/* --- housekeeping ----------------------------------------------- */}
        <section className="mt-4 flex flex-wrap items-center justify-between gap-3 px-1 text-[14px] text-ink-3">
          <span>
            Signed in as {user.email}
            {/* The separator is its own padded element, not a string with spaces in it. JSX collapses
                leading and trailing whitespace in text children, so `" · email not…"` rendered hard
                against the address. Padding is layout, so it survives. */}
            {user.emailVerified ? null : (
              <>
                <span aria-hidden className="px-[0.55em] text-ink-3/70">
                  &middot;
                </span>
                email not confirmed yet
              </>
            )}
          </span>
          <form action="/api/auth/logout" method="post">
            {/* A bare text button gave no sign it was pressable until the cursor changed. It now grows a
                border and a fill on hover, which is the same affordance as `.btn` at a smaller weight —
                signing out should look deliberate to reach, not accidental. */}
            <button
              type="submit"
              className="rounded-[7px] border border-transparent px-2.5 py-1 text-ink-2 transition-colors hover:border-line-strong hover:bg-sunken hover:text-ink focus-visible:border-line-strong focus-visible:bg-sunken focus-visible:text-ink"
            >
              Sign out
            </button>
          </form>
        </section>
      </main>

      <Footer />
    </>
  );
}
