"use client";

/**
 * The moving background, and the light that follows the cursor.
 *
 * Two effects, both cheap, both doing a job a flat dark page cannot:
 *
 * **`Aurora`** — three large blurred orbs drifting on long, out-of-phase loops. A dark page with no
 * light in it reads as unfinished, and a static gradient reads as a screenshot. Movement this slow is
 * not noticed directly; it is noticed when it stops. Blur and transform only, so it stays on the
 * compositor and costs no layout.
 *
 * **`Spotlight`** — a soft warm light tracking the pointer across a card. This is the interactivity
 * that makes a page feel like a surface instead of a picture, and it is done with two CSS custom
 * properties updated on `pointermove` rather than React state: setting state at pointer frequency would
 * re-render the subtree sixty times a second for a visual with no logic behind it.
 *
 * Both are inert under `prefers-reduced-motion`, and neither exists on touch devices where there is no
 * pointer to follow.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { animate } from "animejs";

export function Aurora() {
  const root = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const orbs = element.querySelectorAll<HTMLElement>("[data-orb]");
    const animations = Array.from(orbs).map((orb, index) =>
      animate(orb, {
        // Deliberately long and prime-ish durations so the three never resynchronise into a pulse.
        translateX: [`${-6 + index * 4}%`, `${8 - index * 5}%`],
        translateY: [`${4 - index * 3}%`, `${-7 + index * 4}%`],
        scale: [1, 1.18 - index * 0.05],
        duration: 17000 + index * 4300,
        alternate: true,
        loop: true,
        ease: "inOut(2)",
      }),
    );

    return () => {
      animations.forEach((animation) => animation.revert());
    };
  }, []);

  return (
    <div ref={root} aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div
        data-orb
        className="absolute -left-[10%] -top-[18%] h-[52vw] w-[52vw] rounded-full opacity-[0.28] blur-[110px]"
        style={{ background: "radial-gradient(closest-side, #ff7a1a, transparent)" }}
      />
      {/* Was green. Now a deep near-black with a warm bias, so the middle of the page has depth without
          a second accent colour competing with the orange. */}
      <div
        data-orb
        className="absolute -right-[14%] top-[22%] h-[46vw] w-[46vw] rounded-full opacity-[0.5] blur-[120px]"
        style={{ background: "radial-gradient(closest-side, #201a17, transparent)" }}
      />
      <div
        data-orb
        className="absolute bottom-[-20%] left-[26%] h-[44vw] w-[44vw] rounded-full opacity-[0.16] blur-[130px]"
        style={{ background: "radial-gradient(closest-side, #d9600a, transparent)" }}
      />
      {/* A dot grid, masked so it fades out well before the edges. Gives the dark field a sense of
          depth without becoming a visible pattern. */}
      <div
        className="absolute inset-0 opacity-[0.5]"
        style={{
          backgroundImage: "radial-gradient(rgb(255 255 255 / 0.055) 1px, transparent 1px)",
          backgroundSize: "34px 34px",
          maskImage: "radial-gradient(120% 90% at 50% 0%, black 20%, transparent 72%)",
          WebkitMaskImage: "radial-gradient(120% 90% at 50% 0%, black 20%, transparent 72%)",
        }}
      />
    </div>
  );
}

export function Spotlight({
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
    if (!window.matchMedia("(pointer: fine)").matches) return;

    function move(event: PointerEvent) {
      const box = element!.getBoundingClientRect();
      element!.style.setProperty("--mx", `${event.clientX - box.left}px`);
      element!.style.setProperty("--my", `${event.clientY - box.top}px`);
      element!.style.setProperty("--spot", "1");
    }
    function leave() {
      element!.style.setProperty("--spot", "0");
    }

    element.addEventListener("pointermove", move);
    element.addEventListener("pointerleave", leave);
    return () => {
      element.removeEventListener("pointermove", move);
      element.removeEventListener("pointerleave", leave);
    };
  }, []);

  return (
    // `h-full` on both this and the inner wrapper is what makes cards in a row match heights. Without
    // it the child's own `h-full` had no height to fill, so every card was only as tall as its text and
    // a row of four looked ragged.
    <div
      ref={host}
      className={`group/spot relative h-full ${className}`}
      style={{ ["--spot" as string]: 0 }}
    >
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-0 rounded-[inherit] transition-opacity duration-500"
        style={{
          opacity: "var(--spot)",
          background:
            "radial-gradient(340px circle at var(--mx) var(--my), rgb(255 122 26 / 0.13), transparent 62%)",
        }}
      />
      <div className="relative z-[1] h-full">{children}</div>
    </div>
  );
}
