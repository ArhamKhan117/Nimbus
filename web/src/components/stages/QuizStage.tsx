"use client";

/**
 * "Quiz me", demonstrated by reversing who is asking.
 *
 * Nimbus asks the question, the student answers wrongly, and instead of marking it wrong it **points at
 * where the answer is on their screen**. That is the part a flashcard app structurally cannot do, and it
 * is the reason this feature belongs in a product about screens rather than in a study app.
 *
 * The pointer lands inside the highlight, for the same reason it matters in the hero: an animation of the
 * product missing is worse than no animation.
 */
import { createTimeline, onScroll, utils } from "animejs";

import { MockWindow, useStage } from "./Window";

const TARGET = { x: 372, y: 96 };
const POINTER_SCALE = 1.4;

export function QuizStage() {
  const root = useStage((element) => {
    const highlight = element.querySelector<SVGRectElement>("[data-highlight]");
    if (!highlight) return;
    utils.set("[data-ask]", { opacity: 0, translateY: 8 });
    utils.set("[data-wrong]", { opacity: 0, translateY: 8 });
    utils.set("[data-nudge]", { opacity: 0, translateY: 8 });
    utils.set(highlight, { opacity: 0, scale: 0.9 });
    utils.set("[data-pointer]", { translateX: 150, translateY: 210, opacity: 0 });

    createTimeline({
      loop: true,
      defaults: { ease: "out(3)" },
      autoplay: onScroll({ target: element, enter: "bottom top", leave: "top bottom" }),
    })
      // 1. Nimbus asks.
      .add("[data-ask]", { opacity: 1, translateY: 0, duration: 420 }, 300)
      // 2. The student has a go, and gets it wrong.
      .add("[data-wrong]", { opacity: 1, translateY: 0, duration: 380 }, "+=1500")
      // 3. Not "incorrect" — a pointer to where the answer is.
      .add("[data-nudge]", { opacity: 1, translateY: 0, duration: 380 }, "+=900")
      .add("[data-pointer]", { opacity: 1, duration: 200 }, "-=200")
      .add(
        "[data-pointer]",
        { translateX: TARGET.x - 3, translateY: TARGET.y - 4, duration: 780, ease: "outBack(1.4)" },
        "<<+=60",
      )
      .add(highlight, { opacity: 1, scale: 1, duration: 420 }, "-=320")
      // 4. Reset.
      .add("[data-ask]", { opacity: 0, duration: 320 }, "+=2200")
      .add("[data-wrong]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-nudge]", { opacity: 0, duration: 320 }, "<<")
      .add(highlight, { opacity: 0, duration: 320 }, "<<")
      .add("[data-pointer]", { opacity: 0, translateX: 150, translateY: 210, duration: 380 }, "<<");
  });

  return (
    // Two roughly equal columns across the full container. With ~1100px to share, each panel is about
    // 540px and every line of the exchange fits in one or two lines instead of five — which is what
    // "wider, not longer" actually needed.
    <div ref={root} className="grid items-stretch gap-3 lg:grid-cols-2">
      <MockWindow title="Circuit Simulator">
        <svg viewBox="0 0 520 260" className="w-full" role="img" aria-label="Nimbus asking a question and then pointing at where the answer is on screen">
          <rect width="520" height="260" rx="8" fill="#0e0e11" />
          <rect width="120" height="260" rx="8" fill="#121216" />
          {[0, 1, 2, 3].map((row) => (
            <rect key={row} x="14" y={20 + row * 24} width={74 - row * 6} height="7" rx="3.5" fill="#26262b" />
          ))}

          {/* A properties panel: the sort of place an answer hides. */}
          <rect x="140" y="20" width="140" height="9" rx="4" fill="#33333a" />
          {[0, 1, 2, 3, 4].map((row) => (
            <g key={row}>
              <rect x="140" y={48 + row * 34} width="96" height="7" rx="3.5" fill="#1f1f25" />
              <rect x="300" y={42 + row * 34} width="130" height="22" rx="5" fill="#16161b" stroke="#26262b" />
              <rect x="310" y={49 + row * 34} width={70 - row * 6} height="7" rx="3.5" fill="#4a4a54" />
            </g>
          ))}

          <rect
            data-highlight
            x="292"
            y="76"
            width="146"
            height="38"
            rx="8"
            fill="rgba(255,122,26,0.12)"
            stroke="#FF7A1A"
            strokeWidth="2.4"
            style={{ transformOrigin: `${TARGET.x}px ${TARGET.y}px` }}
          />

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
      </MockWindow>

      {/* The exchange. Labels sit *beside* their line rather than above it, which removes three rows of
          height, and the copy is trimmed so each turn is one or two lines at this width. */}
      <div className="surface grain relative flex flex-col justify-center gap-3.5 p-5 sm:p-6">
        <div data-ask className="grid grid-cols-[4.6rem_1fr] items-baseline gap-3">
          <p className="eyebrow text-[10px]">Nimbus</p>
          <p className="text-[13.5px] leading-relaxed text-ink">
            &ldquo;Which field decides how long the simulation runs?&rdquo;
          </p>
        </div>

        <div
          data-wrong
          className="grid grid-cols-[4.6rem_1fr] items-baseline gap-3 rounded-[8px] border border-line bg-sunken/70 px-3 py-2.5"
        >
          <p className="eyebrow text-[10px]">You</p>
          <p className="text-[13px] text-ink-2">&ldquo;the sample rate one?&rdquo;</p>
        </div>

        <div data-nudge className="grid grid-cols-[4.6rem_1fr] items-baseline gap-3">
          <p className="eyebrow text-[10px]">Nimbus</p>
          <p className="text-[13.5px] leading-relaxed text-ink">
            &ldquo;Close &mdash; that sets resolution. The one I mean is highlighted. Try again.&rdquo;
          </p>
        </div>
      </div>
    </div>
  );
}
