"use client";

/**
 * Scroll-triggered reveals, driven by anime.js v4's `onScroll` observer.
 *
 * ## Why anime.js and not a scroll library plus a motion library
 *
 * v4 ships the scroll observer in the same package as the animation engine, so linking "when" to
 * "what" needs no glue and no second dependency. `createScope` ties every animation to this
 * component's subtree and disposes of it on unmount, which is the part that leaks in React if you
 * animate imperatively without it.
 *
 * ## Why elements start invisible
 *
 * A reveal that animates *from* visible flashes the finished state on first paint. The cost is that a
 * failed script would leave blank space, so `RevealBoot` adds `.no-js` to `<html>` and the CSS shows
 * everything unconditionally in that case. Motion is decoration; the text is the product.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { animate, onScroll, stagger, utils } from "animejs";

type Props = {
  children: ReactNode;
  className?: string;
  /** Distance in pixels the content rises from. Small numbers read as confidence; large read as fuss. */
  y?: number;
  delay?: number;
  /** Stagger direct children instead of moving the block as one. */
  staggerChildren?: boolean;
  as?: "div" | "section" | "li" | "header";
};

export function Reveal({
  children,
  className = "",
  y = 18,
  delay = 0,
  staggerChildren = false,
  as: Tag = "div",
}: Props) {
  const host = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const targets = staggerChildren
      ? Array.from(element.children).filter((child): child is HTMLElement => child instanceof HTMLElement)
      : [element];

    utils.set(targets, { opacity: 0, translateY: y });

    const animation = animate(targets, {
      opacity: 1,
      translateY: 0,
      duration: 700,
      delay: staggerChildren ? stagger(90, { start: delay }) : delay,
      ease: "out(3)",
      autoplay: onScroll({
        target: element,
        // Fires once the element is a fifth of the way up the viewport: soon enough that nothing
        // pops in late on a fast scroll, late enough that it is not already done on load.
        enter: "bottom-=15% top",
        // Plays once and stays played. Without this, scrolling back up re-runs every reveal, which
        // turns a considered entrance into a page that will not settle.
        repeat: false,
      }),
    });

    return () => {
      animation.revert();
    };
  }, [delay, staggerChildren, y]);

  return (
    <Tag ref={host as never} className={`reveal ${className}`}>
      {children}
    </Tag>
  );
}

/** Marks the document as script-capable. Rendered once, in the layout. */
export function RevealBoot() {
  useEffect(() => {
    document.documentElement.classList.remove("no-js");
  }, []);
  return null;
}
