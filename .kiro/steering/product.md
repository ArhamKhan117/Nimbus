# Nimbus — what it is, who it is for, and what it refuses to do

## The product in one paragraph

Nimbus is a Windows desktop application. Hold `Ctrl+Alt+Space`, ask a question out loud about
whatever is on screen, release. Nimbus captures every monitor, sends the screenshot plus the
question to a vision model, streams the spoken answer back through text-to-speech, and flies a
pointer to the exact control it is talking about.

It is a personal project, not a commercial one. It was built by two people, and used during development
by a small group of testers — mostly close friends — who reported bugs and asked for features. An
activation layer and an accounts backend exist because device-bound licensing was one of the problems
worth solving; the **payment rails are integrated but not connected**, nothing is charged, and the
plan and price constants in the code are placeholders on a rail that has never been switched on.

## The problem it solves

A user is stuck inside a piece of software and **does not know the word for the thing they are
looking at**. They cannot search for it and cannot describe it to a chatbot, because describing it
is the hard part. Nimbus removes that requirement by looking at the same screen.

## Who it is for, and the consequences

Someone learning unfamiliar Windows software on an ordinary laptop, possibly on a slow connection.
Two design consequences follow, and they are not negotiable:

- **Windows desktop, not a browser extension.** The software with no tutorial — a college portal, a
  lab instrument's control panel, an accounting package from 2009 — is a native Win32 window. A web
  product is structurally unable to help with any of it.
- **Low-end hardware and slow connections are the target, not the edge case.** No WebGL on the
  landing page; a latency budget measured in milliseconds; a fully-local provider stack that needs
  no network at all.

## Refusals — do not implement these

These are decisions, not gaps in the backlog. Reversing one needs an explicit conversation, not a
pull request.

| Refusal | Why |
|---|---|
| **Nimbus never clicks for you.** No Computer Use, no automation of the user's mouse. | An agent that clicks demos better and teaches nothing. The hand on the mouse has to be the learner's own, because that is where the learning gets stored. Recorded as a non-goal at `IMPROVEMENTS.md` §8 (`T3-1`, skipped outright). |
| **No proxying inference through our servers.** BYOK only. | It would end the "nothing leaves your machine" claim on the local path, add per-user cost, and create a privacy liability. Recorded as a non-goal. A no-keys edition is on the roadmap as a *deliberate* future reversal. |
| **No obfuscation of the licence check.** | PyArmor breaks PyInstaller in ways that cost days and delays an attacker by an afternoon. A local desktop application cannot enforce a licence, only deter casual sharing. |
| **No vector database, no embeddings, no RAG** for memory or the knowledge base. | Plain Markdown the user can read, edit and delete is the transparency contract. A keyword score is inspectable in a way a cosine distance is not. |
| **No emoji anywhere in the interface.** | Segoe UI Emoji has its own metrics and its own palette: one glyph in a label silently changes that label's line height and ignores the theme. Enforced by `tests/test_shell.py::TestNoEmojiInTheUi`. |

## The claims the product makes, and what makes each one true

Every one of these is user-facing copy. If a change would make one of them false, the change is
wrong — or the copy has to change in the same commit.

- *"Nothing leaves your machine"* — true of credentials (Windows Credential Manager, DPAPI) always,
  and true of screen contents on the local provider stack. The Privacy Guard is what makes the claim
  honest on a cloud provider.
- *"Refuses to screenshot password managers and sign-in pages, and shows you the count"* — the count
  is durable, read from the `privacy_skips` table. A count is an observation; a promise is not.
- *"Runs fully offline"* — Ollama + faster-whisper + Kokoro, no keys, no network. This is a
  **regression gate** on every model-layer change.
- *"Licences are verified offline, with a 14-day grace"* — a tool that stops working on a flight is
  worse than one that gets pirated.
- *"Plain Markdown you can read, edit or delete"* — per-app memory and the knowledge base are files,
  not a database the user cannot inspect.

## Limits, as constants

The plan and price constants exist in the code because the activation layer was built end to end, but
the rail behind them is **not connected** — treat them as placeholders, not as a commercial position.
No rendered surface states an amount: not a page, not a dialog, not an email. `PLAN_PRICE_USD` is
declared and never read; `PRICE_PKR` survives only as a stored column on a `ManualPayment` row and as a
field in the hosted-checkout POST body. If a change would put a figure back on screen, that is a product
decision and needs saying out loud, not a copy tweak.

| | |
|---|---|
| Devices | 2 (`licence.DEFAULT_SEATS`) — a desktop and a laptop |
| Trial | 7 days (`TRIAL_DAYS`), no card, **device-bound**, needs a verified email |
| Token TTL | 30 days (`TOKEN_TTL_DAYS`), refreshed every 7 |
| Offline grace | 14 days (`OFFLINE_GRACE_DAYS`) |
| Latency budget | 1.5 s to first audible word (`config.E2E_LATENCY_BUDGET_S`) |
