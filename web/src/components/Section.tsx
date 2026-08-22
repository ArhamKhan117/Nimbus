"use client";

/**
 * Section scaffolding, so vertical rhythm is a decision made once.
 *
 * The spacing between a heading and its lead line, and between that lead and the content, was
 * inconsistent section by section — which is exactly the kind of thing that reads as "unfinished" without
 * anyone being able to name why. Both gaps are now CSS custom properties in `globals.css`
 * (`--space-heading-lead`, `--space-lead-content`) and every section goes through here.
 *
 * The heading animates in on scroll, and the eyebrow gets a short accent rule that draws itself — a
 * small piece of motion that marks the start of a section without a divider line across the page.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { animate, onScroll, stagger, utils } from "animejs";

import { Headline } from "@/components/Type";

export function SectionHeading({
  eyebrow,
  title,
  lead,
  align = "left",
}: {
  eyebrow: string;
  title: string;
  lead?: string;
  align?: "left" | "center";
}) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const rule = element.querySelector("[data-rule]");
    const lead = element.querySelector("[data-lead]");
    if (rule) utils.set(rule, { scaleX: 0 });
    if (lead) utils.set(lead, { opacity: 0, translateY: 12 });

    const animations = [
      rule &&
        animate(rule, {
          scaleX: 1,
          duration: 700,
          ease: "out(3)",
          autoplay: onScroll({ target: element, enter: "bottom-=12% top", repeat: false }),
        }),
      lead &&
        animate(lead, {
          opacity: 1,
          translateY: 0,
          duration: 700,
          delay: 220,
          ease: "out(3)",
          autoplay: onScroll({ target: element, enter: "bottom-=12% top", repeat: false }),
        }),
    ].filter(Boolean);

    return () => {
      animations.forEach((animation) => animation && animation.revert());
    };
  }, []);

  return (
    <div ref={host} className={align === "center" ? "mx-auto max-w-2xl text-center" : "max-w-2xl"}>
      <div
        className={`flex items-center gap-3 ${align === "center" ? "justify-center" : ""}`}
      >
        <span className="eyebrow">{eyebrow}</span>
        <span
          data-rule
          aria-hidden
          className="h-px w-14 origin-left bg-gradient-to-r from-accent to-transparent"
        />
      </div>

      <Headline
        as="h2"
        whenVisible
        text={title}
        className="mt-[var(--space-heading-lead)] block text-[clamp(1.65rem,3.4vw,2.5rem)]"
      />

      {lead ? (
        <p data-lead className="mt-4 text-[1.05rem] leading-relaxed text-ink-2">
          {lead}
        </p>
      ) : null}
    </div>
  );
}

/** A grid whose children arrive on a stagger as it scrolls in. */
export function StaggerGrid({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const cards = Array.from(element.children).filter(
      (child): child is HTMLElement => child instanceof HTMLElement,
    );
    utils.set(cards, { opacity: 0, translateY: 26, scale: 0.985 });

    const animation = animate(cards, {
      opacity: 1,
      translateY: 0,
      scale: 1,
      duration: 780,
      delay: stagger(85),
      ease: "out(3)",
      autoplay: onScroll({ target: element, enter: "bottom-=8% top", repeat: false }),
    });

    return () => {
      animation.revert();
    };
  }, []);

  return (
    <div ref={host} className={className}>
      {children}
    </div>
  );
}
