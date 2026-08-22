"use client";

/**
 * The hero: what Nimbus actually does, animated once and looped.
 *
 * A mock application window, the hotkey held, a waveform, then the pointer flying **into** the ring it
 * draws round the control. Every beat maps to a real beat of the product.
 *
 * ## The one detail worth getting right
 *
 * The pointer's tip must finish inside the ring. The pointer path is a filled arrow whose *origin is
 * its tip*, so the landing coordinate is the ring's centre minus nothing — but the group is also
 * `scale(1.5)`, and the transforms compose, so the numbers below are the ring centre in the group's own
 * scaled space. Getting this wrong looks like the product missing, which is the single worst thing this
 * animation could imply.
 *
 * ## Why this instead of a 3D scene
 *
 * The current advice on landing pages is that shipping a large WebGL bundle to fade in text is over, and
 * the constraint here is sharper than fashion: the target is an ordinary laptop, often on a slow
 * connection. This is one SVG and one timeline, animating transforms and
 * `stroke-dashoffset` only, so it stays on the compositor. It also *demonstrates the product* rather
 * than decorating around it, which no spinning mesh would.
 *
 * `createScope` disposes of everything on unmount, and the loop pauses when scrolled out of view — an
 * animation running behind the pricing table is battery someone paid for.
 */
import { useEffect, useRef } from "react";
import { animate, createScope, createTimeline, onScroll, utils, type Scope } from "animejs";

const TARGET = { x: 390, y: 144 };
const POINTER_SCALE = 1.5;
const START = { x: 210, y: 232 };

/**
 * Where the pointer stops, in SVG user units.
 *
 * **This was wrong, visibly.** The landing point was divided by `POINTER_SCALE`, which put the tip up and
 * to the left of the ring — the animation showed the product missing, which is the worst thing it could
 * possibly imply. The reason: `scale()` is on the `<path>`, *inside* the `<g>` that gets translated, so
 * the group's translation is in plain user units and the child's scale never applies to it. Composing
 * transforms is easy to get backwards and impossible to miss once you look at the result.
 *
 * A couple of units up and left of dead centre, so the arrow's *tip* reads as on the control rather than
 * its middle.
 */
const LAND = { x: TARGET.x - 3, y: TARGET.y - 4 };

export function PointerDemo() {
  const root = useRef<HTMLDivElement | null>(null);
  const scope = useRef<Scope | null>(null);

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    scope.current = createScope({ root: element }).add(() => {
      const ring = element.querySelector<SVGCircleElement>("[data-ring]");
      if (!ring) return;
      const ringLength = ring.getTotalLength?.() ?? 320;

      utils.set(ring, { strokeDasharray: ringLength, strokeDashoffset: ringLength });
      utils.set("[data-pointer]", { translateX: START.x, translateY: START.y, opacity: 0, scale: 0.85 });
      utils.set("[data-caption]", { opacity: 0, translateY: 10 });
      utils.set("[data-bar]", { scaleY: 0.22 });
      utils.set("[data-chip]", { opacity: 0.4, borderColor: "#33333a" });
      utils.set("[data-question]", { opacity: 0 });
      utils.set("[data-halo]", { opacity: 0, scale: 0.6 });

      const timeline = createTimeline({
        defaults: { ease: "inOut(2)" },
        loop: true,
        autoplay: onScroll({ target: element, enter: "bottom top", leave: "top bottom" }),
      });

      timeline
        // 1. The chord goes down. The chip warms to accent, as the app's own listening state does.
        .add("[data-chip]", { opacity: 1, borderColor: "#ff7a1a", duration: 260 }, 400)
        // 2. Nimbus listens.
        .add(
          "[data-bar]",
          {
            scaleY: () => 0.35 + Math.random() * 1.05,
            duration: 200,
            loop: 6,
            alternate: true,
            ease: "inOut(3)",
          },
          "<<",
        )
        // 3. The question appears, as the transcript would.
        .add("[data-question]", { opacity: [0, 1], translateY: [8, 0], duration: 340 }, "+=380")
        .add("[data-bar]", { scaleY: 0.22, duration: 260 }, "<<")
        .add("[data-chip]", { opacity: 0.4, borderColor: "#33333a", duration: 260 }, "<<")
        // 4. The pointer flies to the control and lands on it. The product, in one movement.
        .add("[data-pointer]", { opacity: 1, scale: 1, duration: 220 }, "+=140")
        .add(
          "[data-pointer]",
          {
            translateX: LAND.x,
            translateY: LAND.y,
            // Two eases in sequence would be smoother; one slightly overshooting ease reads as
            // intent. It arrives like something aimed rather than something interpolated.
            duration: 820,
            ease: "outBack(1.4)",
          },
          "<<+=70",
        )
        // 5. Teaching mode draws round it, and the control lights up.
        .add("[data-halo]", { opacity: 1, scale: 1, duration: 420 }, "-=340")
        .add("[data-ring]", { strokeDashoffset: 0, duration: 560, ease: "out(3)" }, "-=300")
        .add("[data-target]", { opacity: [0.5, 1], duration: 340 }, "<<")
        .add("[data-caption]", { opacity: 1, translateY: 0, duration: 420 }, "-=240")
        // 6. Reset, and go again.
        .add("[data-caption]", { opacity: 0, duration: 320 }, "+=1900")
        .add("[data-ring]", { strokeDashoffset: ringLength, duration: 340 }, "<<")
        .add("[data-halo]", { opacity: 0, scale: 0.6, duration: 340 }, "<<")
        .add("[data-question]", { opacity: 0, duration: 320 }, "<<")
        .add("[data-target]", { opacity: 0.5, duration: 320 }, "<<")
        .add(
          "[data-pointer]",
          {
            translateX: START.x,
            translateY: START.y,
            opacity: 0,
            scale: 0.85,
            duration: 460,
          },
          "<<",
        );

      // The glow behind the window, breathing. Outside the timeline so it never waits for the loop.
      animate("[data-glow]", {
        opacity: [0.22, 0.46],
        scale: [1, 1.07],
        duration: 4600,
        alternate: true,
        loop: true,
        ease: "inOut(2)",
      });
    });

    return () => scope.current?.revert();
  }, []);

  return (
    <div ref={root} className="relative mx-auto w-full max-w-[580px]">
      <div
        data-glow
        aria-hidden
        className="pointer-events-none absolute -inset-12 -z-10 rounded-full opacity-30 blur-3xl"
        style={{
          background:
            "radial-gradient(closest-side, rgba(255,122,26,0.32), rgba(255,122,26,0.05) 62%, transparent)",
        }}
      />

      <div className="surface grain grain-strong relative p-3 shadow-2xl shadow-black/70">
        {/* A stand-in application, not a real one: naming someone else's software in a marketing image
            invites a question about permission that a generic window does not. */}
        <div className="flex items-center gap-2 px-2 pb-3 pt-1">
          <span className="h-2.5 w-2.5 rounded-full bg-line-strong" />
          <span className="h-2.5 w-2.5 rounded-full bg-line-strong" />
          <span className="h-2.5 w-2.5 rounded-full bg-line-strong" />
          <span className="ml-2 font-mono text-[12px] tracking-tight text-ink-3">
            Physics Lab Portal
          </span>
        </div>

        <svg
          viewBox="0 0 520 300"
          className="w-full"
          role="img"
          aria-label="Nimbus moving the pointer onto the control it is describing, with a ring drawn round it"
        >
          <rect x="0" y="0" width="520" height="300" rx="8" fill="#0e0e11" />
          <rect x="0" y="0" width="132" height="300" rx="8" fill="#121216" />
          {[0, 1, 2, 3, 4].map((row) => (
            <rect
              key={row}
              x="16"
              y={22 + row * 26}
              width={80 - row * 7}
              height="8"
              rx="4"
              fill="#26262b"
            />
          ))}

          <rect x="152" y="22" width="150" height="9" rx="4" fill="#33333a" />
          {[0, 1, 2].map((row) => (
            <rect
              key={row}
              x="152"
              y={52 + row * 18}
              width={330 - row * 40}
              height="7"
              rx="3.5"
              fill="#1f1f25"
            />
          ))}

          {/* The halo sits under the ring so the ring reads as drawn on top of a lit control. */}
          <circle data-halo cx={TARGET.x} cy={TARGET.y} r="52" fill="url(#halo)" />
          <defs>
            <radialGradient id="halo">
              <stop offset="0%" stopColor="#ff7a1a" stopOpacity="0.28" />
              <stop offset="100%" stopColor="#ff7a1a" stopOpacity="0" />
            </radialGradient>
          </defs>

          <g data-target opacity="0.5">
            <rect x="326" y="126" width="128" height="36" rx="8" fill="#1f1f26" stroke="#33333a" />
            <rect x="346" y="141" width="72" height="7" rx="3.5" fill="#a1a1aa" />
          </g>
          <circle
            data-ring
            cx={TARGET.x}
            cy={TARGET.y}
            r="42"
            fill="none"
            stroke="#FF7A1A"
            strokeWidth="2.5"
            strokeLinecap="round"
          />

          {[0, 1, 2].map((row) => (
            <rect
              key={row}
              x="152"
              y={196 + row * 18}
              width={300 - row * 60}
              height="7"
              rx="3.5"
              fill="#1f1f25"
            />
          ))}

          {/* The pointer. Same silhouette the desktop overlay draws, tip at the group's origin. */}
          <g data-pointer>
            <path
              d="M0 0 L0 21 L5.2 16.4 L8.6 24.4 L12.4 22.7 L9 14.9 L15.8 14.4 Z"
              fill="#FF7A1A"
              stroke="#1A0E04"
              strokeWidth="0.9"
              transform={`scale(${POINTER_SCALE})`}
            />
          </g>
        </svg>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-2 pb-1 pt-3">
          <span
            data-chip
            className="rounded-md border bg-sunken px-2.5 py-1 font-mono text-[11px] text-ink-2 sm:text-[11.5px]"
          >
            Ctrl + Alt + Space
          </span>
          <span className="flex h-5 items-end gap-[3px]" aria-hidden>
            {Array.from({ length: 10 }).map((_, index) => (
              <span
                key={index}
                data-bar
                className="w-[3px] origin-bottom rounded-full bg-accent"
                style={{ height: `${12 + (index % 3) * 4}px` }}
              />
            ))}
          </span>
          <span data-question className="text-[13px] text-ink-2 sm:text-[14px]">
            &ldquo;what do I do next?&rdquo;
          </span>
        </div>
      </div>

      <p data-caption className="mt-5 text-center text-[14px] text-ink-2 sm:text-[15px]">
        <span className="font-display font-medium text-ink">Submit for review</span> is the one you want,
        and your pointer is already on it.
      </p>
    </div>
  );
}
