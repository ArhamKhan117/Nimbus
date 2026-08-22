import Link from "next/link";

/**
 * "← Back to X", for any screen a person can reach and then want out of.
 *
 * ## Why an explicit destination and not `history.back()`
 *
 * Because the browser's back button already does `history.back()`, and duplicating it adds nothing. What is
 * missing on a payment screen is different: **a way to change your mind about a choice you have made**, and
 * that has a specific destination — the page where the choice was offered. `history.back()` would send
 * someone who arrived from an email, or refreshed, somewhere unpredictable.
 *
 * Rendered as a quiet link rather than a button. It is a way out, not an action, and giving it button
 * weight would compete with the thing the page is actually for.
 */
export function BackLink({
  href,
  children,
  className = "",
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Link
      href={href}
      className={`group inline-flex items-center gap-2 text-[14px] text-ink-3 transition-colors hover:text-ink ${className}`}
    >
      <span
        aria-hidden
        className="transition-transform duration-200 group-hover:-translate-x-0.5"
      >
        &larr;
      </span>
      {children}
    </Link>
  );
}
