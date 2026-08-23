"use client";

/**
 * "It knows your software", demonstrated by showing where the answer came from.
 *
 * A folder with one Markdown file in it, a question, and an answer that **quotes the user's own note back
 * and cites the filename**. The citation is the whole point: an assistant that says the right thing is
 * useful, and an assistant that shows you it read your file is trustworthy. It is also the honest
 * description of the mechanism — the file is dropped in, matched by executable name, and treated as
 * authoritative for that program.
 *
 * The file is named `LABVIEW.EXE.md` rather than something invented, because the naming convention *is*
 * the feature and a made-up name would teach the wrong thing.
 */
import { createTimeline, onScroll, stagger, utils } from "animejs";

import { AskBar, MockWindow, useStage } from "./Window";

const ANSWER = [
  "Your notes say the calibration file",
  "has to be loaded before the run starts,",
  "not after. Load it from Setup first.",
];

export function KnowledgeStage() {
  const root = useStage((element) => {
    utils.set("[data-file]", { opacity: 0, translateX: -10 });
    utils.set("[data-question]", { opacity: 0 });
    utils.set("[data-read]", { opacity: 0 });
    utils.set("[data-line]", { opacity: 0, translateY: 6 });
    utils.set("[data-cite]", { opacity: 0 });
    utils.set("[data-bar]", { scaleY: 0.22 });

    createTimeline({
      loop: true,
      defaults: { ease: "out(3)" },
      autoplay: onScroll({ target: element, enter: "bottom top", leave: "top bottom" }),
    })
      // 1. The folder, with the one file that matters.
      .add("[data-file]", { opacity: 1, translateX: 0, duration: 420 }, 250)
      // 2. The question.
      .add("[data-bar]", { scaleY: () => 0.35 + Math.random(), duration: 190, loop: 5, alternate: true }, "+=200")
      .add("[data-question]", { opacity: 1, duration: 300 }, "+=140")
      .add("[data-bar]", { scaleY: 0.22, duration: 220 }, "<<")
      // 3. Nimbus reads the file — the row highlights, so the source is visible before the answer is.
      .add("[data-read]", { opacity: 1, duration: 320 }, "+=120")
      // 4. The answer, line by line, then the citation.
      .add("[data-line]", { opacity: 1, translateY: 0, duration: 380, delay: stagger(140) }, "+=160")
      .add("[data-cite]", { opacity: 1, duration: 320 }, "-=120")
      // 5. Reset.
      .add("[data-line]", { opacity: 0, duration: 320 }, "+=2400")
      .add("[data-cite]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-read]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-question]", { opacity: 0, duration: 320 }, "<<")
      .add("[data-file]", { opacity: 0, translateX: -10, duration: 340 }, "<<");
  });

  return (
    <div ref={root}>
      <MockWindow
        title="Nimbus Knowledge Folder"
        footer={<AskBar question="when do I load the calibration file?" />}
      >
        <div className="grid gap-2.5 sm:grid-cols-[0.85fr_1fr]">
          {/* The folder. */}
          <div className="rounded-[7px] border border-line bg-[#0e0e11] p-3">
            <p className="font-mono text-[10.5px] text-ink-3">~/Documents/Nimbus Wiki/</p>
            <ul className="mt-2.5 space-y-1.5">
              {["EXCEL.EXE.md", "LABVIEW.EXE.md", "PORTAL.EXE.md"].map((name, index) => (
                <li
                  key={name}
                  data-file
                  className="relative flex items-center gap-2 rounded-[5px] px-1.5 py-1 font-mono text-[11px]"
                >
                  {index === 1 ? (
                    <span
                      data-read
                      aria-hidden
                      className="absolute inset-0 rounded-[5px] border border-accent/60 bg-accent/12"
                    />
                  ) : null}
                  <span aria-hidden className="relative text-ink-3">
                    &#9002;
                  </span>
                  <span className={`relative ${index === 1 ? "text-ink" : "text-ink-3"}`}>{name}</span>
                </li>
              ))}
            </ul>
            <p className="mt-3 font-mono text-[10px] leading-relaxed text-ink-3">
              named after the .exe
              <br />
              &rarr; authoritative for it
            </p>
          </div>

          {/* The answer. */}
          <div className="rounded-[7px] border border-line bg-[#0e0e11] p-3.5">
            <p className="eyebrow text-[10px]">Nimbus</p>
            <div className="mt-2 space-y-1">
              {ANSWER.map((line) => (
                <p key={line} data-line className="text-[12.5px] leading-snug text-ink sm:text-[13px]">
                  {line}
                </p>
              ))}
            </div>
            <p data-cite className="mt-3 flex items-center gap-1.5 font-mono text-[10.5px] text-accent">
              <span aria-hidden>&#8599;</span> from your LABVIEW.EXE.md
            </p>
          </div>
        </div>
      </MockWindow>
    </div>
  );
}
