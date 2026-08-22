import Image from "next/image";
import Link from "next/link";

import { HomeLink } from "@/components/HomeLink";
import { PaymentMarks } from "@/components/PaymentMarks";
import { Magnetic } from "@/components/Type";
import { sessionState } from "@/lib/auth";
import { manualTransferAvailable } from "@/lib/easypaisa";
import { stripeConfigured } from "@/lib/stripe";

/**
 * Header and footer.
 *
 * The header is a server component so it can read the session: a signed-in visitor sees "Account"
 * rather than "Sign in", which is the smallest thing that makes a site feel like it knows who you are,
 * and it means no loading flicker on the one element above the fold on every page.
 *
 * Layout is the conventional one, because this is not the place to be inventive: mark and wordmark hard
 * left, everything else hard right, one primary action at the end of the row. The live dot next to the
 * wordmark is the app's own listening green — the only other place that colour appears.
 */
export async function Header() {
  // Checks the account still exists rather than only that the cookie parses, so a deleted account stops
  // being offered an "Account" link that can only redirect away.
  const { state } = await sessionState();
  const session = state === "ok";

  return (
    // Full width with a modest inset rather than a centred 1152px column: the mark belongs at the left
    // edge of the window and the actions at the right edge, which is what every application does and what
    // a centred container cannot do on a wide display.
    <header className="sticky top-0 z-40 border-b border-line/80 bg-base/70 backdrop-blur-xl">
      {/* Asymmetric padding on purpose: the lockup sits closer to the left edge than the nav does to the
          right, because a wordmark reads as anchored to the corner while a row of links needs breathing
          room before the window edge. */}
      <div className="flex h-[68px] w-full items-center pl-2.5 pr-4 sm:h-[74px] sm:pl-3.5 sm:pr-7 lg:pl-5 lg:pr-10">
        <Link href="/" className="group mr-auto flex items-center gap-3" aria-label="Nimbus, home">
          <Image
            src="/nimbus_mark.png"
            alt=""
            width={44}
            height={44}
            priority
            className="h-9 w-9 transition-transform duration-500 group-hover:scale-[1.08] group-hover:rotate-[-4deg] sm:h-11 sm:w-11"
          />
          <span className="wordmark text-[21px] sm:text-[25px]">Nimbus</span>
        </Link>

        <nav className="flex items-center gap-5 lg:gap-7">
          <HomeLink className="navlink hidden sm:block" />
          <Link href="/#how" className="navlink hidden sm:block">
            How it works
          </Link>
          <Link href="/#built" className="navlink hidden md:block">
            Features
          </Link>
          <Link href="/#privacy" className="navlink hidden md:block">
            Privacy
          </Link>
          <Link href="/#pricing" className="navlink hidden sm:block">
            The plan
          </Link>
          {session ? (
            <Link href="/account" className="navlink">
              Account
            </Link>
          ) : (
            <Link href="/login" className="navlink">
              Sign in
            </Link>
          )}
          <Magnetic strength={5}>
            <Link href="/download" className="btn btn-primary" prefetch={false}>
              <span>Download</span>
            </Link>
          </Magnetic>
        </nav>
      </div>
    </header>
  );
}

export function Footer() {
  const cards = stripeConfigured();
  const local = manualTransferAvailable();

  return (
    <footer className="relative mt-24 border-t border-line py-12 sm:mt-28">
      <div className="mx-auto flex max-w-6xl flex-wrap items-start gap-x-12 gap-y-8 px-6">
        {/* The payment marks live here, in the identity column, rather than in a band of their own between
            two rules. They were fenced off before, which gave a footnote about card rails the same visual
            weight as the whole footer — they belong under the wordmark as part of "here is who we are and
            how you can pay us". */}
        <div className="mr-auto max-w-sm">
          <span className="wordmark text-[19px]">Nimbus</span>
          <p className="mt-3 text-[14px] leading-relaxed text-ink-3">
            Ask about anything on your screen, out loud. Built for Windows.
          </p>
          <div className="mt-6">
            <PaymentMarks cardsEnabled={cards} localEnabled={local} />
          </div>
        </div>

        <nav className="flex flex-col gap-2.5 text-[14.5px]">
          <span className="eyebrow mb-1">Product</span>
          <Link href="/#how" className="text-ink-2 transition-colors hover:text-accent">
            How it works
          </Link>
          <Link href="/#pricing" className="text-ink-2 transition-colors hover:text-accent">
            The plan
          </Link>
          <Link href="/download" prefetch={false} className="text-ink-2 transition-colors hover:text-accent">
            Download
          </Link>
        </nav>

        <nav className="flex flex-col gap-2.5 text-[14.5px]">
          <span className="eyebrow mb-1">Licence</span>
          <Link href="/pay" className="text-ink-2 transition-colors hover:text-accent">
            The transfer rail
          </Link>
          <Link href="/signup" className="text-ink-2 transition-colors hover:text-accent">
            Create an account
          </Link>
          <Link href="/login" className="text-ink-2 transition-colors hover:text-accent">
            Sign in
          </Link>
        </nav>

        <nav className="flex flex-col gap-2.5 text-[14.5px]">
          <span className="eyebrow mb-1">Contact</span>
          <Link href="/#privacy" className="text-ink-2 transition-colors hover:text-accent">
            Privacy
          </Link>
          <a href="mailto:wolfhoghd@gmail.com" className="text-ink-2 transition-colors hover:text-accent">
            wolfhoghd@gmail.com
          </a>
        </nav>
      </div>

      <div className="mx-auto mt-10 max-w-6xl px-6">
        <hr className="rule" />
        <p className="mt-6 font-mono text-[12.5px] text-ink-3">
          &copy; {new Date().getFullYear()} Nimbus
          <span aria-hidden className="px-[0.5em] text-ink-3/70">
            &middot;
          </span>
          Windows 10 &amp; 11
        </p>
      </div>
    </footer>
  );
}
