"use client";

/**
 * The layout every stage set shares: copy on one side, the working demo on the other.
 *
 * One component so all four read as the same product rather than as four illustrations, and `reverse` so
 * the page alternates sides — four identical rows in a column becomes a pattern the eye stops reading.
 *
 * On narrow screens the demo always comes **after** the copy regardless of `reverse`, because on a phone
 * the reading order is the visual order and an unexplained animation arriving first is just noise.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { animate, onScroll, utils } from "animejs";

import { Headline } from "@/components/Type";

export function Stage({
  eyebrow,
  title,
  lead,
  demo,
  reverse = false,
  wide = false,
}: {
  eyebrow: string;
  title: string;
  lead: string;
  demo: ReactNode;
  reverse?: boolean;
  /** More of the row for the demo. For stages whose demo contains its own text panels. */
  wide?: boolean;
}) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const rule = element.querySelector("[data-rule]");
    const lead = element.querySelector("[data-lead]");
    const frame = element.querySelector("[data-frame]");

    if (rule) utils.set(rule, { scaleX: 0 });
    if (lead) utils.set(lead, { opacity: 0, translateY: 12 });
    // The demo panel rises and settles as it arrives. Small: it is a container for something that is
    // already moving, and two things moving at once compete.
    if (frame) utils.set(frame, { opacity: 0, translateY: 26 });

    const enter = { enter: "bottom-=10% top", repeat: false } as const;
    const animations = [
      rule && animate(rule, { scaleX: 1, duration: 700, ease: "out(3)", autoplay: onScroll({ target: element, ...enter }) }),
      lead && animate(lead, { opacity: 1, translateY: 0, duration: 700, delay: 200, ease: "out(3)", autoplay: onScroll({ target: element, ...enter }) }),
      frame && animate(frame, { opacity: 1, translateY: 0, duration: 900, delay: 120, ease: "out(3)", autoplay: onScroll({ target: element, ...enter }) }),
    ].filter(Boolean);

    return () => {
      animations.forEach((animation) => animation && animation.revert());
    };
  }, []);

  return (
    <div
      ref={host}
      className="mx-auto max-w-6xl px-6 py-[clamp(3.5rem,8vw,5.5rem)]"
    >
      {/*
       * `wide` **stacks** rather than narrowing the columns further.
       *
       * The first attempt at "make it wider" gave the demo a bigger share of a side-by-side row, which had
       * the opposite effect: the demo's own two panels each ended up under 300px, the text wrapped every
       * three words, and the whole thing got taller than it was before. Beyond a point, more of a row is
       * still not much room. Stacking hands the demo the full 1100px container, so its panels are wide and
       * the height collapses.
       */}
      <div
        className={
          wide
            ? "mx-auto max-w-3xl text-center"
            : `grid items-center gap-10 lg:gap-14 ${"lg:grid-cols-[0.82fr_1.18fr]"}`
        }
      >
        <div className={reverse && !wide ? "lg:order-2" : ""}>
          <div className={`flex items-center gap-3 ${wide ? "justify-center" : ""}`}>
            <span className="eyebrow">{eyebrow}</span>
            <span
              data-rule
              aria-hidden
              className="h-px w-12 origin-left bg-gradient-to-r from-accent to-transparent"
            />
          </div>

          <Headline
            as="h3"
            whenVisible
            text={title}
            className="mt-[var(--space-heading-lead)] block text-[clamp(1.4rem,2.9vw,2.05rem)]"
          />

          <p data-lead className="mt-4 text-[1.01rem] leading-relaxed text-ink-2">
            {lead}
          </p>
        </div>

        {/* Stacked: the demo leaves the centred copy column and takes the whole container width. */}
        {wide ? null : (
          <div data-frame className={reverse ? "lg:order-1" : ""}>
            {demo}
          </div>
        )}
      </div>

      {wide ? (
        <div data-frame className="mt-10 text-left">
          {demo}
        </div>
      ) : null}
    </div>
  );
}
