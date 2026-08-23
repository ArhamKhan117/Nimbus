import type { Metadata } from "next";
import Link from "next/link";

import { BackLink } from "@/components/BackLink";
import { Footer, Header } from "@/components/Chrome";
import { ManualPaymentForm } from "@/components/ManualPaymentForm";
import { readSession } from "@/lib/auth";
import { easypaisaConfig, hostedCheckoutAvailable } from "@/lib/easypaisa";
import { DEFAULT_SEATS } from "@/lib/licence";

export const metadata: Metadata = { title: "The EasyPaisa and bank transfer rail" };
export const dynamic = "force-dynamic";

/**
 * The transfer route: submit a reference, an admin confirms it by hand, a key is issued.
 *
 * The rail is implemented and covered by tests but is not connected — nothing is charged. The page is
 * kept because it states the one thing an automated checkout hides and this one cannot: a person checks
 * it, and roughly how long that takes. Given the choice between a spinner that implies instant and a
 * sentence that tells the truth, the sentence is the better design.
 */
export default async function PayPage() {
  const session = await readSession();
  const config = easypaisaConfig();
  const easypaisa = Boolean(config.accountNumber);
  const bank = Boolean(config.bankAccount || config.bankIban);
  const hosted = hostedCheckoutAvailable();

  return (
    <>
      <Header />

      <main id="main" className="mx-auto max-w-2xl px-6 py-14">
        {/* A way back to where the choice was offered. Without it, someone who came here to look at the
            local option and then decided on a card had no route except the browser's back button. */}
        <BackLink href="/#pricing" className="mb-6">
          Back to the plan
        </BackLink>

        <p className="eyebrow">Bank transfer</p>
        <h1 className="mt-2 text-[clamp(1.7rem,4vw,2.3rem)] font-semibold tracking-[-0.02em]">
          The EasyPaisa and bank transfer rail
        </h1>
        <p className="mt-4 text-ink-2">
          The same licence as the card rail: {DEFAULT_SEATS} devices and every feature. Both rails are
          implemented and tested, and neither is connected &mdash; nothing is charged here.
        </p>

        {!easypaisa && !bank ? (
          <div className="surface grain relative mt-8 overflow-hidden border-l-2 border-l-warn p-7">
            <h2 className="text-[1.1rem] font-semibold">Not switched on yet</h2>
            <p className="mt-2 text-ink-2">
              No transfer details are published, because the rail is not connected. Email{" "}
              <a className="text-accent hover:text-accent-hover" href="mailto:wolfhoghd@gmail.com">
                wolfhoghd@gmail.com
              </a>{" "}
              and a licence key will be sent back, at no charge.
            </p>
          </div>
        ) : (
          <>
            <section className="surface grain relative mt-8 overflow-hidden p-6 sm:p-7">
              <p className="eyebrow">Step 1</p>
              <h2 className="mt-3 text-[1.1rem] font-semibold">Where a transfer would go</h2>

              <dl className="mt-4 space-y-3 text-[15px]">
                {easypaisa ? (
                  <>
                    <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                      <dt className="text-ink-2">EasyPaisa number</dt>
                      <dd className="select-all font-mono">{config.accountNumber}</dd>
                    </div>
                    {config.accountName ? (
                      <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                        <dt className="text-ink-2">Account name</dt>
                        <dd>{config.accountName}</dd>
                      </div>
                    ) : null}
                  </>
                ) : null}

                {bank ? (
                  <>
                    {config.bankName ? (
                      <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                        <dt className="text-ink-2">Bank</dt>
                        <dd>{config.bankName}</dd>
                      </div>
                    ) : null}
                    {/* The account title, before the numbers. Banks and the EasyPaisa app both ask
                        for the beneficiary name and reject a transfer when it does not match, so
                        leaving it out turns a two-minute payment into a failed one. */}
                    {config.bankTitle ? (
                      <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                        <dt className="text-ink-2">Account title</dt>
                        <dd className="select-all">{config.bankTitle}</dd>
                      </div>
                    ) : null}
                    {config.bankAccount ? (
                      <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                        <dt className="text-ink-2">Account number</dt>
                        <dd className="select-all font-mono">{config.bankAccount}</dd>
                      </div>
                    ) : null}
                    {config.bankIban ? (
                      <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                        <dt className="text-ink-2">IBAN</dt>
                        <dd className="select-all font-mono">{config.bankIban}</dd>
                      </div>
                    ) : null}
                    {/* Last, and labelled for who needs it. A SWIFT code is only used for an
                        international wire; domestically the IBAN is enough, and an unexplained
                        extra field invites someone to think they have missed a step. */}
                    {config.bankSwift ? (
                      <div className="flex flex-wrap justify-between gap-3 border-b border-line pb-2">
                        <dt className="text-ink-2">SWIFT (from abroad)</dt>
                        <dd className="select-all font-mono">{config.bankSwift}</dd>
                      </div>
                    ) : null}
                  </>
                ) : null}
              </dl>

              <p className="mt-4 text-[14px] text-ink-3">
                Keep the receipt open &mdash; the next step asks for the transaction ID on it.
              </p>
            </section>

            <div className="mt-4">
              <ManualPaymentForm
                signedIn={Boolean(session)}
                easypaisaAvailable={easypaisa}
                bankAvailable={bank}
              />
            </div>
          </>
        )}

        {hosted ? (
          <p className="mt-6 text-[14px] text-ink-3">
            Prefer to pay inside EasyPaisa&rsquo;s own checkout? That is available too &mdash;{" "}
            <Link href="/pay/easypaisa" className="text-accent hover:text-accent-hover">
              continue there
            </Link>
            .
          </p>
        ) : null}

        <p className="mt-8 text-[14px] text-ink-3">
          Questions, or want a key for a group of testers?{" "}
          <a className="text-accent hover:text-accent-hover" href="mailto:wolfhoghd@gmail.com">
            wolfhoghd@gmail.com
          </a>{" "}
          reaches a person, not a queue.
        </p>
      </main>

      <Footer />
    </>
  );
}
