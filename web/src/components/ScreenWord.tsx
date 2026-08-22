"use client";

/**
 * The word "screen", inside a monitor that switches on.
 *
 * ## The alignment fix, and why the first attempt could never have worked
 *
 * Version one drew the monitor as one SVG and absolutely positioned the word over the middle of it. That
 * meant the word's vertical position was a number I had guessed, and the surrounding headline's baseline
 * was a number the browser had computed — two independent numbers that only agree by luck. The word sat
 * high, which is exactly what the screenshot showed.
 *
 * So the structure is inverted here: **the word is ordinary text in normal flow**, and the monitor is
 * absolutely positioned *around* it. Now the browser aligns "screen" with "your" the way it aligns any
 * two words on a line, and there is no magic offset to keep in step with the font. The frame is built
 * from CSS boxes rather than SVG for the same reason — a stretched `viewBox` would distort the bezel
 * radius at every different word width, and `em` units keep the whole thing proportional to the type at
 * any breakpoint.
 *
 * ## What it is imitating
 *
 * A CRT power-on, because everyone recognises it even on a flat panel: a bright line snaps across the
 * middle, opens vertically into a picture, overshoots into a flash, then settles. It reads as *a display
 * coming to life* rather than as a rectangle being animated, and it is the one moment on this page where
 * the medium is the message.
 *
 * Without JavaScript, or under `prefers-reduced-motion`, it renders switched on — which is the finished
 * state anyway.
 */
import { useEffect, useRef } from "react";
import { createScope, createTimeline, onScroll, utils, type Scope } from "animejs";

export function ScreenWord({ children = "screen" }: { children?: string }) {
  const root = useRef<HTMLSpanElement | null>(null);
  const scope = useRef<Scope | null>(null);

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    scope.current = createScope({ root: element }).add(() => {
      utils.set("[data-panel]", { opacity: 0 });
      utils.set("[data-word]", { opacity: 0 });
      utils.set("[data-scanline]", { scaleX: 0, scaleY: 0.05, opacity: 0 });
      utils.set("[data-flash]", { opacity: 0 });
      utils.set("[data-glow]", { opacity: 0 });
      utils.set("[data-bezel]", { opacity: 0.4 });
      utils.set("[data-stand]", { opacity: 0.4 });

      createTimeline({
        loop: true,
        defaults: { ease: "out(3)" },
        autoplay: onScroll({ target: element, enter: "bottom top", leave: "top bottom" }),
      })
        // 1. The frame is there, dark and cold.
        .add("[data-bezel]", { opacity: 1, duration: 320 }, 250)
        .add("[data-stand]", { opacity: 1, duration: 320 }, "<<")
        // 2. A single line snaps across the middle. Fast: this is the click of the switch.
        .add("[data-scanline]", { scaleX: [0, 1], opacity: [0, 1], duration: 190, ease: "out(4)" })
        // 3. It opens vertically into a picture, and the panel comes up under it.
        .add("[data-scanline]", { scaleY: [0.05, 1], duration: 260, ease: "out(2)" })
        .add("[data-panel]", { opacity: 1, duration: 240 }, "<<+=60")
        // 4. The overshoot flash every CRT does, then the line is gone and the word is there.
        .add("[data-flash]", { opacity: [0, 0.9, 0], duration: 340 }, "<<+=80")
        .add("[data-scanline]", { opacity: 0, duration: 200 }, "<<+=120")
        .add("[data-glow]", { opacity: 1, duration: 520 }, "<<")
        .add("[data-word]", { opacity: [0, 1], scale: [0.97, 1], duration: 360 }, "-=180")
        // 5. Hold it, then switch off and go again.
        .add("[data-word]", { opacity: 0, duration: 240 }, "+=3600")
        .add("[data-panel]", { opacity: 0, duration: 260 }, "<<")
        .add("[data-glow]", { opacity: 0, duration: 300 }, "<<")
        .add("[data-bezel]", { opacity: 0.4, duration: 300 }, "<<")
        .add("[data-stand]", { opacity: 0.4, duration: 300 }, "<<");
    });

    return () => scope.current?.revert();
  }, []);

  return (
    <span
      ref={root}
      // Padding gives the bezel somewhere to sit without touching the letters; the margins keep the
      // frame off the neighbouring words. All in `em`, so it scales with the headline.
      //
      // `top` lifts the whole assembly — frame, word and stand together — by a hair. A monitor sitting
      // exactly on the text baseline reads as slightly sunken next to the cap height of "your", and
      // lifting it also buys back the space the stand needs underneath.
      className="relative inline-block px-[0.3em] mx-[0.1em]"
      style={{ top: "-0.07em" }}
    >
      {/* The glow the panel throws into the room. Behind everything, larger than the frame. */}
      <span
        data-glow
        aria-hidden
        className="pointer-events-none absolute left-1/2 top-1/2 -z-10 h-[190%] w-[125%] -translate-x-1/2 -translate-y-1/2 blur-[0.3em]"
        style={{
          background:
            "radial-gradient(closest-side, rgba(255,122,26,0.4), rgba(255,122,26,0.06) 60%, transparent)",
        }}
      />

      {/* Stand: neck and base.
       *
       * It has to *touch* the bezel and stop. Previously it hung 0.3em below the box and reached into
       * "Out loud." on the next line, which made it read as a separate floating object rather than as
       * part of the monitor. The container's top edge is the bezel's bottom edge to the pixel — the two
       * numbers are tied: `-bottom-[0.22em]` plus `h-[0.16em]` puts the top at `-0.06em`, which is the
       * bezel's `-bottom-[0.06em]`. **Change one and you must change the other**, or the stand detaches.
       */}
      <span
        data-stand
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -bottom-[0.22em] h-[0.16em]"
      >
        <span
          className="absolute left-1/2 top-0 h-full w-[0.26em] -translate-x-1/2"
          style={{ background: "linear-gradient(180deg,#2b2b31,#15151a)" }}
        />
        <span
          className="absolute bottom-0 left-1/2 h-[0.055em] w-[0.78em] -translate-x-1/2 rounded-full"
          style={{ background: "linear-gradient(180deg,#33333a,#141418)" }}
        />
      </span>

      {/* The bezel, and the recessed screen inside it. */}
      <span
        data-bezel
        aria-hidden
        // Top only. The bottom stays at -0.06em and the stand stays tied to it, so the screen loses
        // height from above rather than the whole frame moving.
        //
        // A positive `top` now, so the edge sits *below* the word's box top and the tallest letters rise
        // a little past the bezel. That is safe because the word is a **sibling** of this frame, not a
        // child of the panel that clips — so nothing is cut off, the glyph tops simply overlap the frame
        // and read as sitting proud of the screen.
        className="pointer-events-none absolute top-[0.055em] -bottom-[0.06em] left-0 right-0 rounded-[0.17em]"
        style={{
          background: "linear-gradient(180deg,#34343c 0%,#1c1c21 52%,#101013 100%)",
          boxShadow:
            "inset 0 1px 0 rgba(255,255,255,0.1), 0 0.06em 0.18em rgba(0,0,0,0.65)",
          padding: "0.075em",
        }}
      >
        <span className="absolute inset-[0.075em] overflow-hidden rounded-[0.12em] bg-[#08080a]">
          {/* The lit panel. */}
          <span
            data-panel
            className="absolute inset-0 rounded-[0.12em]"
            style={{
              background: "linear-gradient(135deg,#2c1708 0%,#160d07 45%,#0b0b0d 100%)",
              boxShadow: "inset 0 0 0 1px rgba(255,122,26,0.45)",
            }}
          >
            {/* Faint scan texture, the way a panel is never perfectly flat. */}
            <span
              className="absolute inset-0"
              style={{
                backgroundImage:
                  "repeating-linear-gradient(180deg, rgba(255,122,26,0.075) 0 1px, transparent 1px 0.13em)",
              }}
            />
          </span>

          {/* The switch-on line, and the overshoot flash. */}
          <span
            data-scanline
            className="absolute left-0 top-1/2 h-[0.1em] w-full -translate-y-1/2 rounded-full bg-[#ffd7b0]"
          />
          <span data-flash className="absolute inset-0 bg-[#fff1e2]" />
        </span>
      </span>

      {/* The word: ordinary text, in normal flow. Its baseline is the browser's problem, which is the
          entire point of building it this way.
       *
       * A touch smaller than the surrounding headline — text inside a screen inside a sentence needs to
       * sit *within* its frame, and at the full headline size it filled the bezel edge to edge with no
       * margin, which read as cramped rather than as a display showing something. */}
      <span data-word className="relative inline-block text-[0.86em] leading-[1]">
        <span className="accent-text">{children}</span>
      </span>
    </span>
  );
}
