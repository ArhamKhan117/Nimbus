"use client";

/**
 * Teaching mode, demonstrated rather than described.
 *
 * Three numbered boxes draw themselves in order, everything else dims, then an arrow curves from the
 * thing that was wrong to the thing that fixes it. That is exactly what the app does when an answer has
 * more than one step, and it is the feature that is hardest to believe from a sentence.
 *
 * The order of beats is the point: **dim first, then number, then correct.** Dimming before drawing is
 * what makes the boxes read as attention rather than as decoration, and it is the same order
 * `annotations.py` uses — the dim layer goes down before any geometry.
 */
import { createTimeline, onScroll, utils } from "animejs";

import { AskBar, MockWindow, TextLines, useStage } from "./Window";

const STEPS = [
  { x: 168, y: 60, w: 132, h: 30, n: "1" },
  { x: 168, y: 108, w: 176, h: 30, n: "2" },
  { x: 330, y: 168, w: 124, h: 32, n: "3" },
];

export function TeachingStage() {
  const root = useStage((element) => {
    utils.set("[data-dim]", { opacity: 0 });
    utils.set("[data-box]", { opacity: 0, scale: 0.94 });
    utils.set("[data-num]", { opacity: 0, scale: 0.6 });
    utils.set("[data-arrow]", { opacity: 0 });
    utils.set("[data-wrong]", { opacity: 0 });
    utils.set("[data-question]", { opacity: 0 });
    utils.set("[data-bar]", { scaleY: 0.22 });

    const arrow = element.querySelector<SVGPathElement>("[data-arrow-path]");
    if (!arrow) return;
    const arrowLength = arrow.getTotalLength?.() ?? 200;
    utils.set(arrow, { strokeDasharray: arrowLength, strokeDashoffset: arrowLength });

    const timeline = createTimeline({
      loop: true,
      defaults: { ease: "out(3)" },
      autoplay: onScroll({ target: element, enter: "bottom top", leave: "top bottom" }),
    });

    timeline
      .add("[data-bar]", {
        scaleY: () => 0.35 + Math.random(),
        duration: 190,
        loop: 5,
        alternate: true,
      }, 300)
      .add("[data-question]", { opacity: 1, duration: 300 }, "+=180")
      .add("[data-bar]", { scaleY: 0.22, duration: 240 }, "<<")
      // Dim the page before drawing anything on it.
      .add("[data-dim]", { opacity: 1, duration: 420 }, "+=120");

    // Each step: box, then its number a beat later, so the sequence reads left to right in time.
    STEPS.forEach((_, index) => {
      timeline
        .add(`[data-box="${index}"]`, { opacity: 1, scale: 1, duration: 340 }, index === 0 ? "-=60" : "+=340")
        .add(`[data-num="${index}"]`, { opacity: 1, scale: 1, duration: 260, ease: "outBack(2)" }, "-=180");
    });

    timeline
      // The mistake, then the arrow to the fix.
      .add("[data-wrong]", { opacity: 1, duration: 300 }, "+=420")
      .add("[data-arrow]", { opacity: 1, duration: 160 }, "-=120")
      .add(arrow, { strokeDashoffset: 0, duration: 620, ease: "inOut(2)" }, "<<")
      .add("[data-arrowhead]", { opacity: [0, 1], duration: 220 }, "-=140")
      // Clear down and repeat.
      .add("[data-arrow]", { opacity: 0, duration: 340 }, "+=2000")
      .add("[data-wrong]", { opacity: 0, duration: 340 }, "<<")
      .add("[data-box]", { opacity: 0, duration: 340 }, "<<")
      .add("[data-num]", { opacity: 0, duration: 340 }, "<<")
      .add("[data-dim]", { opacity: 0, duration: 400 }, "<<")
      .add("[data-question]", { opacity: 0, duration: 340 }, "<<")
      .add(arrow, { strokeDashoffset: arrowLength, duration: 10 });
  });

  return (
    <div ref={root}>
      <MockWindow
        title="Enrolment System"
        footer={<AskBar question="how do I enrol a student in two courses?" />}
      >
        <svg
          viewBox="0 0 520 300"
          className="w-full"
          role="img"
          aria-label="Nimbus numbering three controls in order and drawing an arrow from a mistake to its fix"
        >
          <rect width="520" height="300" rx="8" fill="#0e0e11" />
          <rect width="132" height="300" rx="8" fill="#121216" />
          {[0, 1, 2, 3, 4].map((row) => (
            <rect key={row} x="16" y={22 + row * 26} width={80 - row * 7} height="8" rx="4" fill="#26262b" />
          ))}
          <rect x="152" y="22" width="150" height="9" rx="4" fill="#33333a" />

          {/* The three controls, drawn plain. The boxes below are what Nimbus adds. */}
          {STEPS.map((step, index) => (
            <g key={index}>
              <rect x={step.x} y={step.y} width={step.w} height={step.h} rx="7" fill="#1c1c22" stroke="#2b2b31" />
              <rect x={step.x + 14} y={step.y + step.h / 2 - 3.5} width={step.w * 0.42} height="7" rx="3.5" fill="#8a8a94" />
            </g>
          ))}
          <TextLines count={2} x={168} y={218} width={300} />

          {/* Everything else dimmed. Sits above the interface, below the annotations. */}
          <rect data-dim width="520" height="300" rx="8" fill="#08080a" opacity="0" style={{ mixBlendMode: "multiply" }} />
          <rect data-dim width="520" height="300" rx="8" fill="rgba(8,8,10,0.55)" />

          {/* The annotations. */}
          {STEPS.map((step, index) => (
            <g key={index}>
              <rect
                data-box={index}
                x={step.x - 7}
                y={step.y - 7}
                width={step.w + 14}
                height={step.h + 14}
                rx="10"
                fill="none"
                stroke="#FF7A1A"
                strokeWidth="2.4"
                style={{ transformOrigin: `${step.x + step.w / 2}px ${step.y + step.h / 2}px` }}
              />
              <g data-num={index} style={{ transformOrigin: `${step.x - 7}px ${step.y - 7}px` }}>
                <circle cx={step.x - 7} cy={step.y - 7} r="11" fill="#FF7A1A" />
                <text
                  x={step.x - 7}
                  y={step.y - 2.5}
                  textAnchor="middle"
                  fontSize="13"
                  fontWeight="700"
                  fill="#1A0E04"
                  fontFamily="ui-sans-serif, system-ui"
                >
                  {step.n}
                </text>
              </g>
            </g>
          ))}

          {/* The mistake, and the arrow from it to the fix. */}
          <g data-wrong>
            <rect x="168" y="108" width="176" height="30" rx="7" fill="none" stroke="#ef5350" strokeWidth="2.2" strokeDasharray="5 4" />
            <text x="176" y="153" fontSize="11" fill="#ef5350" fontFamily="ui-monospace, monospace">
              already on this course
            </text>
          </g>
          <g data-arrow>
            <path
              data-arrow-path
              d="M352 122 C 402 122 402 168 330 184"
              fill="none"
              stroke="#FF7A1A"
              strokeWidth="2.4"
              strokeLinecap="round"
            />
            <path data-arrowhead d="M336 176 L326 184 L338 191 Z" fill="#FF7A1A" opacity="0" />
          </g>
        </svg>
      </MockWindow>
    </div>
  );
}
