/**
 * The "ways you can pay" strip in the footer.
 *
 * ## Why these are drawn rather than dropped in
 *
 * Every one of these is somebody's registered trademark. Using a name to say "we accept this" is fine —
 * that is nominative use, and it is why every checkout page in the world can list card brands — but
 * *reproducing* an official logo file means following each brand's usage rules on clear space, minimum
 * size, permitted colourways and background, and shipping five vendor SVGs to do it.
 *
 * So these are typographic marks in our own palette: recognisable, honest about what they are, and not a
 * pretend copy of artwork I do not have. Mastercard gets its two interlocking circles because that shape
 * *is* how it is recognised at 20px, where a word is not.
 *
 * If you want the official assets later they are all downloadable from the brands' own press pages, and
 * this component is the only place to change.
 *
 * ## Why the card marks dim when Stripe is off
 *
 * Because a Visa mark in the footer is a claim that you can pay with Visa. Until `STRIPE_SECRET_KEY`
 * exists that is not true, and quietly implying it is the kind of small dishonesty that makes someone
 * distrust the rest of the page. Dimmed with a tooltip is the honest middle: the intent is visible, the
 * claim is not made.
 */
type MarkProps = { dimmed?: boolean; title?: string };

function Frame({
  children,
  dimmed,
  title,
  width = 52,
}: MarkProps & { children: React.ReactNode; width?: number }) {
  return (
    <span
      title={title}
      className={`inline-flex h-8 items-center justify-center rounded-[6px] border border-line-strong bg-sunken/80 px-2.5 transition-opacity ${
        dimmed ? "opacity-40" : "opacity-85 hover:opacity-100"
      }`}
      style={{ minWidth: width }}
    >
      {children}
    </span>
  );
}

function Visa(props: MarkProps) {
  return (
    <Frame {...props}>
      {/* Visa's mark is a wordmark, so a wordmark is the honest stand-in. Italic, tight, uppercase. */}
      <span className="font-display text-[13px] font-bold italic tracking-[0.06em] text-ink-2">
        VISA
      </span>
    </Frame>
  );
}

function Mastercard(props: MarkProps) {
  return (
    // Circles only, no "mc" caption. The two overlapping discs already are the recognition at this size;
    // a two-letter abbreviation beside them read as a label on a diagram rather than as a brand.
    <Frame {...props} width={46}>
      {/* The two interlocking circles: at this size the shape carries the recognition, not the name. */}
      <svg width="24" height="15" viewBox="0 0 22 14" aria-hidden>
        <circle cx="7" cy="7" r="6.2" fill="#a1a1aa" opacity="0.85" />
        <circle cx="15" cy="7" r="6.2" fill="#8a8a94" opacity="0.75" />
      </svg>
    </Frame>
  );
}

function Stripe(props: MarkProps) {
  return (
    <Frame {...props} width={54}>
      <span className="font-display text-[12px] font-semibold tracking-[-0.02em] text-ink-2">
        stripe
      </span>
    </Frame>
  );
}

function Easypaisa(props: MarkProps) {
  return (
    <Frame {...props} width={74}>
      <span className="font-display text-[11.5px] font-semibold tracking-[-0.01em] text-ink-2">
        easypaisa
      </span>
    </Frame>
  );
}

function BankTransfer(props: MarkProps) {
  return (
    <Frame {...props} width={70}>
      <span className="flex items-center gap-1.5">
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden>
          <path d="M1 4.6 L6 1.6 L11 4.6 Z" fill="#8a8a94" />
          <rect x="2" y="5.6" width="1.6" height="4" fill="#8a8a94" />
          <rect x="5.2" y="5.6" width="1.6" height="4" fill="#8a8a94" />
          <rect x="8.4" y="5.6" width="1.6" height="4" fill="#8a8a94" />
          <rect x="1" y="10.2" width="10" height="1.4" fill="#8a8a94" />
        </svg>
        {/* Nudged down a pixel. `items-center` centres both boxes, but a font's em box reserves descender
            space that "Bank" does not use, so its ink sits optically high against a glyph-free SVG whose
            ink fills the box. Aligning the boxes is not aligning what you see. */}
        <span className="translate-y-[1px] font-display text-[10.5px] font-semibold text-ink-2">
          Bank
        </span>
      </span>
    </Frame>
  );
}

export function PaymentMarks({
  cardsEnabled,
  localEnabled,
}: {
  cardsEnabled: boolean;
  localEnabled: boolean;
}) {
  const cardHint = cardsEnabled ? undefined : "Card payments are being switched on";
  const localHint = localEnabled ? undefined : "Local transfer details coming";

  return (
    <div>
      <p className="eyebrow mb-3">Ways to pay</p>
      <div className="flex flex-wrap items-center gap-2">
        <Visa dimmed={!cardsEnabled} title={cardHint} />
        <Mastercard dimmed={!cardsEnabled} title={cardHint} />
        <Stripe dimmed={!cardsEnabled} title={cardHint} />
        <Easypaisa dimmed={!localEnabled} title={localHint} />
        {/* JazzCash removed. It was the only mark here for a rail we have no route to at all — not even a
            manual one — so it was advertising an intention rather than a payment method. */}
        <BankTransfer dimmed={!localEnabled} title={localHint} />
      </div>
      {/* One line, and only when it is needed. The "EasyPaisa gets you the same licence as a card"
          reassurance is already made where it matters — on /pay, at the moment of choosing — and repeating
          it in the footer was explaining a decision nobody is making here. */}
      {cardsEnabled ? null : (
        <p className="mt-3 text-[12.5px] leading-relaxed text-ink-3">
          Card payments are being switched on.
        </p>
      )}
    </div>
  );
}
