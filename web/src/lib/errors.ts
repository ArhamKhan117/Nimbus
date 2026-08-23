/**
 * Every failure a person can hit on this site, in one place, with the sentence they should read.
 *
 * ## Why a map and not messages written at each call site
 *
 * Ten routes each inventing their own wording produces ten different ways of saying "that did not work",
 * and the ones written last are always the worst. A shared map also makes the *coverage* reviewable: the
 * list below is the list of things that can go wrong, so a missing case is visible as a missing entry
 * rather than as a generic message in production.
 *
 * ## The decision worth flagging: we now say when an account does not exist
 *
 * The previous version answered identically for "no such account" and "wrong password", and computed a
 * password hash either way so the timing did not give it away. That is the textbook defence against
 * **account enumeration** — an attacker submitting a list of addresses to learn which are registered.
 *
 * You asked for the honest message, and that is a legitimate call: Google, Slack and GitHub all tell you
 * when an address is unrecognised, because the support cost of "it just says wrong password and I know my
 * password" is real and constant, while enumeration is mostly a nuisance for a product with no social
 * graph to mine. So `NO_ACCOUNT` exists and is used.
 *
 * What we keep: rate limiting and lockout, which is what actually stops someone working through a list,
 * and silence on the *reset* path, where confirming an address to an unauthenticated stranger has no
 * upside at all.
 */
export const ERRORS = {
  // --- shape of the request -------------------------------------------------
  BAD_EMAIL: "That does not look like an email address.",
  WEAK_PASSWORD: "Use at least 10 characters. Three words you will remember beats one clever one.",
  MISSING_FIELDS: "Fill in both fields to continue.",
  BAD_REQUEST: "Something about that request was not right. Try again.",

  // --- signing in -----------------------------------------------------------
  NO_ACCOUNT: "No account with that email yet. Create one and you can start the free trial.",
  WRONG_PASSWORD: "That password does not match. Try again, or email yourself a sign-in link.",
  LOCKED_OUT: "Too many attempts. Wait 15 minutes, or email yourself a sign-in link to get straight in.",

  // --- signing up -----------------------------------------------------------
  EMAIL_TAKEN: "That email already has an account. Sign in instead, or reset the password.",

  // --- codes and links ------------------------------------------------------
  CODE_WRONG: "That code is not right. Check the email and try again.",
  CODE_EXPIRED: "That code has expired. Ask for a new one.",
  CODE_ATTEMPTS: "Too many wrong attempts on that code. Ask for a new one.",
  LINK_USED: "That link has already been used or has expired. Ask for a new one below.",
  STALE_SESSION: "You were signed in to an account that no longer exists. Sign in again below.",

  // --- state of the account -------------------------------------------------
  // Named for the action that resolves it, not for the state that blocks it. Someone who signs in before
  // confirming their address needs to be told to confirm their address; "no active licence" is true and
  // tells them nothing they can act on.
  NOT_VERIFIED:
    "Confirm your email address first. Ask for a new 6-digit code, then enter it to finish.",
  NO_SUBSCRIPTION:
    "That account has no active licence yet. See the plan on the website and Nimbus activates straight away.",
  TRIAL_USED:
    "The free trial on this computer has already been used. A licence key activates it again.",
  SEAT_LIMIT: "That licence is already on all its computers. Deactivate one from Account first.",
  NEED_ACCOUNT: "Create a free account first — it takes an email and a code.",

  // --- our fault ------------------------------------------------------------
  UNAVAILABLE: "Something on our side is not responding. Nothing was charged. Try again shortly.",
  EMAIL_FAILED:
    "We could not send that email just now. Try again in a minute, or write to wolfhoghd@gmail.com.",
  CHECKOUT_UNAVAILABLE: "Card checkout is not responding. Try EasyPaisa or bank transfer instead.",
  OFFLINE: "You appear to be offline. Check your connection and try again.",
} as const;

export type ErrorCode = keyof typeof ERRORS;

/** A JSON body carrying both a machine code and the sentence to show. */
export function errorBody(code: ErrorCode, override?: string) {
  return { code, error: override ?? ERRORS[code], detail: override ?? ERRORS[code] };
}

/**
 * Turn whatever a `fetch` produced into something to show a person.
 *
 * Handles the case every hand-rolled form gets wrong: a network failure, where there is no response to
 * read a message out of and the browser's own error text ("Failed to fetch") means nothing to anyone.
 */
export function messageFromResponse(body: unknown, fallback: ErrorCode = "BAD_REQUEST"): string {
  if (body && typeof body === "object") {
    const shape = body as { error?: unknown; detail?: unknown; code?: unknown };
    if (typeof shape.error === "string" && shape.error) return shape.error;
    if (typeof shape.detail === "string" && shape.detail) return shape.detail;
    if (typeof shape.code === "string" && shape.code in ERRORS) {
      return ERRORS[shape.code as ErrorCode];
    }
  }
  return ERRORS[fallback];
}

/** For a thrown `fetch` rejection rather than an error response. */
export function messageFromThrow(error: unknown): string {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return ERRORS.OFFLINE;
  if (error instanceof Error && error.message) return error.message;
  return ERRORS.UNAVAILABLE;
}
