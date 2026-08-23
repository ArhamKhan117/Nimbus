"use client";

/**
 * Shared chrome for every stage set on the page.
 *
 * ## Why one component and not four
 *
 * Each section demonstrates a different Nimbus feature, but they must all look like **the same
 * application** — otherwise the page reads as four unrelated illustrations rather than one product doing
 * four things. One chrome, one grain treatment, one title style, one shadow.
 *
 * The windows are deliberately generic: no real application is named or drawn, because putting someone
 * else's interface in our marketing invites a permission question a stand-in does not.
 *
 * `useStage` is the other half of the shared behaviour. Every demo needs the same four things — a scope
 * tied to the subtree, a timeline that only runs while visible, disposal on unmount, and nothing at all
 * under `prefers-reduced-motion` — and getting any of those wrong leaks animations or burns battery
 * behind a section nobody is looking at.
 */
import { useEffect, useRef, type ReactNode } from "react";
import { createScope, type Scope } from "animejs";

export function MockWindow({
  title,
  children,
  className = "",
  footer,
}: {
  title: string;
  children: ReactNode;
  className?: string;
  footer?: ReactNode;
}) {
  return (
    <div
      className={`surface grain grain-strong relative p-2.5 shadow-2xl shadow-black/70 sm:p-3 ${className}`}
    >
      <div className="flex items-center gap-2 px-1.5 pb-2.5 pt-0.5">
        <span className="h-2 w-2 rounded-full bg-line-strong sm:h-2.5 sm:w-2.5" />
        <span className="h-2 w-2 rounded-full bg-line-strong sm:h-2.5 sm:w-2.5" />
        <span className="h-2 w-2 rounded-full bg-line-strong sm:h-2.5 sm:w-2.5" />
        <span className="ml-1.5 truncate font-mono text-[11px] tracking-tight text-ink-3 sm:text-[12px]">
          {title}
        </span>
      </div>
      {children}
      {footer ? <div className="px-1.5 pb-0.5 pt-2.5">{footer}</div> : null}
    </div>
  );
}

/** The hotkey chip and waveform, shared by the demos that start with someone speaking. */
export function AskBar({ question }: { question: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2.5 gap-y-2">
      <span
        data-chip
        className="rounded-md border border-line-strong bg-sunken px-2 py-1 font-mono text-[10.5px] text-ink-2 sm:text-[11px]"
      >
        Ctrl + Alt + Space
      </span>
      <span className="flex h-4 items-end gap-[3px]" aria-hidden>
        {Array.from({ length: 8 }).map((_, index) => (
          <span
            key={index}
            data-bar
            className="w-[3px] origin-bottom rounded-full bg-accent"
            style={{ height: `${10 + (index % 3) * 4}px` }}
          />
        ))}
      </span>
      <span data-question className="text-[12.5px] text-ink-2 sm:text-[13.5px]">
        &ldquo;{question}&rdquo;
      </span>
    </div>
  );
}

/**
 * Run an anime.js scope for a stage set.
 *
 * `build` receives the root element and is called inside a `createScope`, so every animation it creates
 * is reverted together on unmount. Returns nothing: the caller only needs the ref.
 */
export function useStage(build: (root: HTMLElement) => void) {
  const root = useRef<HTMLDivElement | null>(null);
  const scope = useRef<Scope | null>(null);

  useEffect(() => {
    const element = root.current;
    if (!element) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    scope.current = createScope({ root: element }).add(() => build(element));
    return () => scope.current?.revert();
    // `build` is defined inline by each caller and closes over nothing that changes, so re-running on
    // identity change would restart every timeline on every render for no reason.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return root;
}

/** Placeholder text lines, so each demo's body looks like a real document without saying anything. */
export function TextLines({
  count = 3,
  x = 152,
  y = 52,
  width = 330,
  step = 18,
  taper = 40,
}: {
  count?: number;
  x?: number;
  y?: number;
  width?: number;
  step?: number;
  taper?: number;
}) {
  return (
    <>
      {Array.from({ length: count }).map((_, row) => (
        <rect
          key={row}
          x={x}
          y={y + row * step}
          width={width - row * taper}
          height="7"
          rx="3.5"
          fill="#1f1f25"
        />
      ))}
    </>
  );
}
