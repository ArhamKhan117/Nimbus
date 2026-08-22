"use client";

/**
 * Text effects, built rather than installed.
 *
 * ## Why no text-animation library
 *
 * I looked at what is current — kinetic-typography packages, WebGL text, the shadcn-style component
 * collections. The good ones ship source into your project; the rest ship a runtime. For this audience
 * the deciding number is bundle size on mobile data, and every effect worth having here is thirty lines
 * of anime.js over spans I already control. So these are written out, and the page stays at ~130 kB.
 *
 * Three components, each with a rule about where it is allowed:
 *
 * | | Where | Rule |
 * |---|---|---|
 * | `Headline` | The hero, and section headings | Words rise and fade in on a stagger. Accent words get the orange gradient. |
 * | `Scramble` | Exactly one line, in the hero | Characters settle out of noise. It is a gimmick, and one gimmick is a signature while five is a carnival. |
 * | `Magnetic` | Primary buttons | The control leans a few pixels toward the cursor. Makes a button feel physical without moving anything else. |
 *
 * Accessibility is not an afterthought in any of them: the full sentence stays in one element with the
 * per-character spans marked `aria-hidden` and the real text on `aria-label`, so a screen reader hears a
 * sentence rather than an alphabet.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { animate, onScroll, stagger, utils } from "animejs";

const reduced = () =>
  typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Word-by-word entrance. Wrap a word in *asterisks* for the accent gradient.
 *
 * `onScroll` when `whenVisible` is set, so section headings animate as they arrive rather than all
 * having happened before the reader gets there.
 */
export function Headline({
  text,
  className = "",
  delay = 0,
  whenVisible = false,
  as: Tag = "span",
}: {
  text: string;
  className?: string;
  delay?: number;
  whenVisible?: boolean;
  as?: "span" | "h1" | "h2" | "h3";
}) {
  const host = useRef<HTMLElement | null>(null);
  const words = text.split(" ");

  useEffect(() => {
    const element = host.current;
    if (!element || reduced()) return;

    const targets = element.querySelectorAll<HTMLElement>("[data-word] > span");
    utils.set(targets, { opacity: 0, translateY: "108%", rotate: "2deg" });

    const animation = animate(targets, {
      opacity: 1,
      translateY: "0%",
      rotate: "0deg",
      duration: 900,
      delay: stagger(52, { start: delay }),
      ease: "out(4)",
      autoplay: whenVisible
        ? onScroll({ target: element, enter: "bottom-=12% top", repeat: false })
        : true,
    });

    return () => {
      animation.revert();
    };
  }, [delay, whenVisible]);

  return (
    <Tag ref={host as never} aria-label={text.replace(/\*/g, "")} className={className}>
      {words.map((word, index) => {
        const accented = word.startsWith("*") && word.endsWith("*");
        return (
          // The outer span clips, so each word rises out of nothing rather than sliding over its
          // neighbour. `pb-[0.12em]` stops descenders being sheared off by the clip.
          <span
            key={`${word}-${index}`}
            data-word
            aria-hidden
            className="inline-block overflow-hidden pb-[0.12em] align-bottom"
          >
            <span className={`inline-block ${accented ? "accent-text" : ""}`}>
              {accented ? word.slice(1, -1) : word}
              {index < words.length - 1 ? "\u00A0" : ""}
            </span>
          </span>
        );
      })}
    </Tag>
  );
}

const NOISE = "ABCDEFGHJKLMNPQRSTUVWXYZ/\\<>*#$%&_+=-";

/**
 * Characters resolve out of noise, once, when scrolled into view.
 *
 * Deliberately literal: the product's job is turning "I do not know what this is" into a definite
 * answer, and this is that in one line of type. It runs on a timer rather than per-frame randomness so
 * the cost is fixed and it always finishes.
 */
export function Scramble({ text, className = "" }: { text: string; className?: string }) {
  const host = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const element = host.current;
    if (!element || reduced()) return;

    const cells = Array.from(element.querySelectorAll<HTMLElement>("[data-char]"));
    let frame = 0;
    let timer = 0;

    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries[0]?.isIntersecting) return;
        observer.disconnect();

        timer = window.setInterval(() => {
          frame += 1;
          let settled = 0;
          cells.forEach((cell, index) => {
            const target = cell.dataset.char ?? "";
            // Each character settles at its own moment, left to right, with a little jitter so the
            // resolve does not read as a wipe.
            if (frame > index * 1.7 + 6 || target === " ") {
              cell.textContent = target;
              cell.style.color = "";
              settled += 1;
            } else {
              cell.textContent = NOISE[Math.floor(Math.random() * NOISE.length)];
              cell.style.color = "var(--color-ink-3)";
            }
          });
          if (settled === cells.length) window.clearInterval(timer);
        }, 34);
      },
      { threshold: 0.4 },
    );

    observer.observe(element);
    return () => {
      observer.disconnect();
      window.clearInterval(timer);
    };
  }, []);

  return (
    // `whitespace-pre` is the fix for a real bug: every character is its own `inline-block`, and an
    // `inline-block` containing a single space collapses to zero width. The line rendered as
    // "notutorial·noscreenshots·nodescribingit" — each word legible, the sentence not.
    //
    // `tabular-nums` too, so a character swapping for a noise glyph does not change its width and shove
    // the rest of the line sideways on every frame.
    <span ref={host} aria-label={text} className={`whitespace-pre tabular-nums ${className}`}>
      {text.split("").map((character, index) => (
        <span key={index} data-char={character} aria-hidden className="inline-block">
          {character}
        </span>
      ))}
    </span>
  );
}

/** A control that leans toward the cursor. Transform only, and it springs back on leave. */
export function Magnetic({ children, strength = 8 }: { children: ReactNode; strength?: number }) {
  const host = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    const element = host.current?.firstElementChild as HTMLElement | null;
    if (!element || reduced()) return;
    if (!window.matchMedia("(pointer: fine)").matches) return;

    const wrapper = host.current!;

    function move(event: PointerEvent) {
      const box = element!.getBoundingClientRect();
      const x = (event.clientX - (box.left + box.width / 2)) / (box.width / 2);
      const y = (event.clientY - (box.top + box.height / 2)) / (box.height / 2);
      element!.style.transform = `translate(${x * strength}px, ${y * strength * 0.55}px)`;
    }
    function leave() {
      element!.style.transform = "";
    }

    wrapper.addEventListener("pointermove", move);
    wrapper.addEventListener("pointerleave", leave);
    return () => {
      wrapper.removeEventListener("pointermove", move);
      wrapper.removeEventListener("pointerleave", leave);
    };
  }, [strength]);

  return (
    <span ref={host} className="inline-flex [&>*]:transition-transform [&>*]:duration-300">
      {children}
    </span>
  );
}
