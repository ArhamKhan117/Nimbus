/**
 * Which mail provider to use, and how to shape a from-address for it.
 *
 * Split out of `email.ts` for one reason: **it has no imports.** `email.ts` reaches for Prisma and the
 * Resend SDK at module load, so importing it in a test constructs a database client to assert on a
 * regular expression. The two functions here are pure, so they are testable in isolation -- the same
 * reason `licence.ts` carries no dependency either.
 *
 * ## Why two providers exist at all
 *
 * A specific constraint, not vendor-neutrality. **Resend will only deliver to the address that owns the
 * account until a domain is verified**, and a deployment on a platform subdomain has no DNS zone to put
 * those records in. On that kind of deployment Resend reaches the maintainer and nobody else, so the
 * six-digit trial code never arrives for anyone else.
 *
 * Brevo verifies a **single sender address** rather than a domain, which needs no DNS, so it works where
 * Resend structurally cannot.
 *
 * The honest limitation, recorded rather than discovered: a sender on a domain nobody has authenticated
 * is subject to the large mailbox providers' bulk-sender rules, so delivery to a Gmail or Yahoo
 * recipient is best-effort and can land in spam. Nothing that matters is allowed to depend on it -- a
 * licence key activates the application with no email anywhere in the path.
 */

export type EmailProvider = "brevo" | "resend" | "none";

/**
 * Brevo first **when configured**, because the deployments that set it are the ones where Resend cannot
 * reach a third party. Selection is by which key is present rather than by a mode flag, so an absent key
 * reads as absent instead of as a misconfiguration.
 *
 * An empty string counts as absent. An unset variable and one set to `""` are the same intent, and only
 * one of them is falsy by accident -- a blank value must not select a provider that will reject every
 * send.
 */
export function emailProvider(
  env: Record<string, string | undefined> = process.env,
): EmailProvider {
  if (env.BREVO_API_KEY) return "brevo";
  if (env.RESEND_API_KEY) return "resend";
  return "none";
}

/**
 * `"Name <address>"` split into its parts.
 *
 * The two providers disagree about the shape of a from-address: Resend takes the whole string, Brevo
 * wants the name and the address as separate fields and rejects a sender with no name. So a value that
 * is perfectly valid for one is silently wrong for the other, and the name always falls back rather
 * than ever coming back empty.
 */
export function parseSender(value: string): { name: string; email: string } {
  const match = /^\s*(.*?)\s*<\s*([^>]+?)\s*>\s*$/.exec(value);
  if (match) return { name: match[1] || "Nimbus", email: match[2] };
  return { name: "Nimbus", email: value.trim() };
}
