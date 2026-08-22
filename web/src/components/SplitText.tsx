"use client";

/**
 * Word-by-word entrance for headlines.
 *
 * The split happens in JSX rather than by rewriting `innerHTML` at runtime, which matters for two
 * reasons: React owns the DOM here and would fight a script that reshapes it, and a crawler or a
 * screen reader sees the whole sentence either way because the words stay in order inside one
 * element. `aria-label` carries the plain sentence so assistive technology never hears it letter by
 * letter.
 *
 * Only the hero headline uses this. Staggered text everywhere becomes a page that will not sit still,
 * and the point of animating one thing is that it is the one thing worth reading first.
 */
import { useEffect, useRef } from "react";
import { animate, stagger, utils } from "animejs";

type Props = {
  text: string;
  className?: string;
  /** Words wrapped in *asterisks* render in the accent colour. */
  delay?: number;
};

export function SplitText({ text, className = "", delay = 120 }: Props) {
  const host = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const words = element.querySelectorAll<HTMLElement>("[data-word]");
    utils.set(words, { opacity: 0, translateY: "0.4em" });

    const animation = animate(words, {
      opacity: 1,
      translateY: 0,
      duration: 620,
      delay: stagger(45, { start: delay }),
      ease: "out(3)",
    });

    return () => {
      animation.revert();
    };
  }, [delay]);

  return (
    <span ref={host} aria-label={text.replace(/\*/g, "")} className={className}>
      {text.split(" ").map((word, index) => {
        const accented = word.startsWith("*") && word.endsWith("*");
        return (
          <span
            key={`${word}-${index}`}
            data-word
            aria-hidden
            className={`inline-block ${accented ? "text-accent" : ""}`}
          >
            {accented ? word.slice(1, -1) : word}
            {index < text.split(" ").length - 1 ? "\u00A0" : ""}
          </span>
        );
      })}
    </span>
  );
}
