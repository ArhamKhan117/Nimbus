"use client";

/**
 * Privacy Guard, demonstrated by showing the screenshot **not** happen.
 *
 * A password manager comes to the front, the capture frame greys out and is struck through, the answer
 * still arrives, and the counter on Home ticks up. That last beat is the one that matters: the count is
 * the observable part, and an observation is worth more than a promise.
 *
 * The refusal is the hardest thing on this page to show, because the interesting event is an *absence*.
 * The solution is to make the absence visible: the capture frame appears, then visibly fails, rather than
 * simply never appearing.
 *
 * The window is a generic "Password Manager" — naming a real one would be both a permission question and
 * an implication we single it out, when the guard covers a documented list plus anything the user adds.
 */
import { animate, createTimeline, onScroll, utils } from "animejs";

import { AskBar, MockWindow, useStage } from "./Window";

export function PrivacyStage() {
  const root = useStage((element) => {
    utils.set("[data-vault]", { opacity: 0, translateY: 14, scale: 0.985 });
    utils.set("[data-question]", { opacity: 0 });
    utils.set("[data-shot]", { opacity: 0, scale: 1.04 });
    utils.set("[data-strike]", { scaleX: 0 });
    utils.set("[data-refused]", { opacity: 0, translateY: 6 });
    utils.set("[data-answer]", { opacity: 0, translateY: 6 });
    utils.set("[data-count-new]", { opacity: 0, translateY: 10 });
    utils.set("[data-count-old]", { opacity: 1, translateY: 0 });
    utils.set("[data-bar]", { scaleY: 0.22 });

    createTimeline({
      loop: true,
      defaults: { ease: "out(3)" },
      autoplay: onScroll({ target: element, enter: "bottom top", leave: "top bottom" }),
    })
      // 1. A password manager comes to the front.
      .add("[data-vault]", { opacity: 1, translateY: 0, scale: 1, duration: 460 }, 300)
      // 2. The question is asked anyway. Nimbus does not refuse to answer.
      .add("[data-bar]", { scaleY: () => 0.35 + Math.random(), duration: 190, loop: 5, alternate: true }, "+=260")
      .add("[data-question]", { opacity: 1, duration: 300 }, "+=140")
      .add("[data-bar]", { scaleY: 0.22, duration: 220 }, "<<")
      // 3. The capture frame appears and then visibly fails. Showing the absence is the whole trick.
      .add("[data-shot]", { opacity: 0.9, scale: 1, duration: 260 }, "+=140")
      .add("[data-strike]", { scaleX: 1, duration: 380, ease: "out(4)" }, "+=90")
      .add("[data-refused]", { opacity: 1, translateY: 0, duration: 320 }, "-=140")
      // 4. The answer arrives regardless: voice only, no screenshot.
      .add("[data-answer]", { opacity: 1, translateY: 0, duration: 380 }, "+=220")
      // 5. The counter on Home ticks up. The observable part.
      .add("[data-count-old]", { opacity: 0, translateY: -10, duration: 260 }, "+=260")
      .add("[data-count-new]", { opacity: 1, translateY: 0, duration: 300 }, "-=180")
      // 6. Reset.
      .add("[data-answer]", { opacity: 0, duration: 320 }, "+=2200")
      .add("[data-refused]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-shot]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-question]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-vault]", { opacity: 0, translateY: 14, duration: 360 }, "<<")
      .add("[data-strike]", { scaleX: 0, duration: 10 })
      .add("[data-count-new]", { opacity: 0, translateY: 10, duration: 10 })
      .add("[data-count-old]", { opacity: 1, translateY: 0, duration: 10 });

    // The guard chip in the corner, breathing. Outside the timeline: it is a permanent state, not a beat.
    animate("[data-guard]", {
      opacity: [0.55, 1],
      duration: 2600,
      alternate: true,
      loop: true,
      ease: "inOut(2)",
    });
  });

  return (
    <div ref={root} className="grid gap-3 lg:grid-cols-[1.35fr_1fr]">
      <MockWindow title="Password Manager" footer={<AskBar question="what does this setting do?" />}>
        <div className="relative">
          <svg viewBox="0 0 520 258" className="w-full" role="img" aria-label="Nimbus answering without taking a screenshot while a password manager is in front">
            <rect width="520" height="258" rx="8" fill="#0e0e11" />

            {/* The vault window, on top of whatever was there. */}
            <g data-vault style={{ transformOrigin: "260px 129px" }}>
              <rect x="86" y="26" width="348" height="206" rx="10" fill="#141418" stroke="#2b2b31" />
              <rect x="86" y="26" width="348" height="30" rx="10" fill="#191920" />
              <circle cx="104" cy="41" r="4" fill="#33333a" />
              <text x="118" y="45" fontSize="11" fill="#8a8a94" fontFamily="ui-monospace, monospace">
                Vault &mdash; 42 items
              </text>
              {[0, 1, 2, 3].map((row) => (
                <g key={row}>
                  <rect x="102" y={72 + row * 38} width="316" height="30" rx="6" fill="#101014" />
                  <circle cx="120" cy={87 + row * 38} r="7" fill="#26262b" />
                  <rect x="136" y={83 + row * 38} width={90 - row * 8} height="7" rx="3.5" fill="#3a3a42" />
                  {/* Password dots: the shape of a secret, without writing one. */}
                  {Array.from({ length: 8 }).map((_, dot) => (
                    <circle key={dot} cx={250 + dot * 9} cy={87 + row * 38} r="2.4" fill="#4a4a54" />
                  ))}
                </g>
              ))}
            </g>

            {/* The capture that gets refused. */}
            <g data-shot>
              <rect
                x="86"
                y="26"
                width="348"
                height="206"
                rx="10"
                fill="rgba(8,8,10,0.72)"
                stroke="#ef5350"
                strokeWidth="2"
                strokeDasharray="7 5"
              />
              {/* Corner marks, like a viewfinder. */}
              {[
                [92, 32, 1, 1],
                [428, 32, -1, 1],
                [92, 226, 1, -1],
                [428, 226, -1, -1],
              ].map(([x, y, sx, sy], index) => (
                <path
                  key={index}
                  d={`M${x} ${y + 16 * sy} L${x} ${y} L${x + 16 * sx} ${y}`}
                  fill="none"
                  stroke="#ef5350"
                  strokeWidth="2.4"
                />
              ))}
              <rect
                data-strike
                x="106"
                y="127"
                width="308"
                height="2.6"
                rx="1.3"
                fill="#ef5350"
                style={{ transformOrigin: "106px 128px" }}
              />
            </g>

            <g data-refused>
              <rect x="150" y="146" width="220" height="26" rx="6" fill="#1a0f0f" stroke="#ef5350" opacity="0.95" />
              <text x="260" y="163" textAnchor="middle" fontSize="11.5" fill="#ef5350" fontFamily="ui-monospace, monospace">
                screenshot skipped
              </text>
            </g>

            {/* The permanent guard state, bottom left. */}
            <g data-guard>
              <rect x="14" y="222" width="58" height="22" rx="11" fill="#101014" stroke="#2b2b31" />
              <circle cx="27" cy="233" r="4.5" fill="#FF7A1A" />
              <text x="38" y="237" fontSize="9.5" fill="#8a8a94" fontFamily="ui-monospace, monospace">
                GUARD
              </text>
            </g>
          </svg>
        </div>
      </MockWindow>

      {/* The answer still arrives, and the count moves. */}
      <div className="grid gap-3">
        <div className="surface grain relative p-5">
          <p className="eyebrow text-[10px]">Nimbus, out loud</p>
          <p data-answer className="mt-2.5 text-[13px] leading-relaxed text-ink sm:text-[13.5px]">
            &ldquo;I did not look at your screen for that one. In a password manager, that toggle
            controls whether the vault locks when your machine sleeps.&rdquo;
          </p>
        </div>

        <div className="surface grain relative p-5">
          <p className="eyebrow text-[10px]">Home &middot; this week</p>
          <div className="relative mt-2 h-[3.1rem]">
            <p data-count-old className="absolute inset-x-0 font-display text-[2.6rem] font-semibold leading-none">
              11
            </p>
            <p data-count-new className="absolute inset-x-0 font-display text-[2.6rem] font-semibold leading-none text-accent">
              12
            </p>
          </div>
          <p className="mt-1.5 text-[12.5px] text-ink-2">screenshots it chose not to take</p>
        </div>
      </div>
    </div>
  );
}
