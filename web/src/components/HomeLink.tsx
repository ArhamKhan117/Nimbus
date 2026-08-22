"use client";

/**
 * "Home": back to the top of the landing page.
 *
 * ## It used to reload, and that was worse
 *
 * The first version forced a full document load so the one-shot entrance animations would replay. In
 * practice the reload started before the smooth scroll had travelled any distance, so it read as "the page
 * flickered and stayed where it was" — the reload cancelled the very thing it was meant to show.
 *
 * The fix is to stop trying to do two things at once. This scrolls, smoothly, and nothing else. The looping
 * demos are always running anyway, and the entrance animations are worth seeing once rather than being
 * re-triggered by a navigation control.
 *
 * A real `href` is kept so middle-click, Ctrl-click and right-click all behave, and so it works with
 * JavaScript disabled. From another page it is an ordinary link.
 */
import { usePathname } from "next/navigation";

export function HomeLink({ className = "" }: { className?: string }) {
  const pathname = usePathname();

  function go(event: React.MouseEvent<HTMLAnchorElement>) {
    // Modified clicks are the browser's to interpret, not ours.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    // Only intercept on the page we are already on. Anywhere else, let the link be a link.
    if (pathname !== "/") return;

    event.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
    // Clear any `#section` left in the address bar, so a later click on the same anchor still works.
    if (window.location.hash) {
      window.history.replaceState(null, "", "/");
    }
  }

  return (
    <a href="/" onClick={go} className={className}>
      Home
    </a>
  );
}
