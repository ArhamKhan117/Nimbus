/**
 * Transactional email, through whichever provider is configured.
 *
 * ## The rule this file follows
 *
 * **A failed email never fails the thing that triggered it.** If the provider is down when a licence
 * is issued, the licence still exists, the success page still shows the key, and the account page
 * still has it. Throwing here would turn a mail outage into a failed activation, which is a far worse
 * outcome than a missing receipt.
 *
 * Every send is logged as an event either way, so "did they get their key" is answerable.
 *
 * Plain-text alternatives are included with every message. Some testers read mail on
 * low-end Android clients, and a key that only exists inside an HTML table is a support ticket.
 *
 * ## Two providers
 *
 * Selection and sender parsing live in `email-provider.ts`, which has no imports and is therefore
 * testable without constructing a database client. The reasoning for two providers is recorded there.
 */
import { Resend } from "resend";

import { logEvent } from "./db";
import { siteUrl } from "./auth";
import { emailProvider, parseSender } from "./email-provider";

const FROM = process.env.EMAIL_FROM ?? "Nimbus <wolfhoghd@gmail.com>";
const REPLY_TO = process.env.EMAIL_REPLY_TO ?? "wolfhoghd@gmail.com";

async function sendViaBrevo(
  to: string, subject: string, html: string, text: string,
): Promise<void> {
  const sender = parseSender(FROM);
  const reply = parseSender(REPLY_TO);
  const response = await fetch("https://api.brevo.com/v3/smtp/email", {
    method: "POST",
    headers: {
      "api-key": process.env.BREVO_API_KEY as string,
      "content-type": "application/json",
      accept: "application/json",
    },
    body: JSON.stringify({
      sender,
      to: [{ email: to }],
      replyTo: { email: reply.email },
      subject,
      htmlContent: html,
      textContent: text,
    }),
  });
  if (!response.ok) {
    // The body carries the actionable part -- an unverified sender reports itself here rather than as a
    // transport failure, and "sender not valid" is a five-minute fix that looks like an outage without it.
    const detail = await response.text().catch(() => "");
    throw new Error(`brevo ${response.status}: ${detail.slice(0, 200)}`);
  }
}

async function sendViaResend(
  to: string, subject: string, html: string, text: string,
): Promise<void> {
  const resend = new Resend(process.env.RESEND_API_KEY as string);
  const { error } = await resend.emails.send({
    from: FROM,
    to,
    subject,
    html,
    text,
    replyTo: REPLY_TO,
  });
  if (error) throw new Error(error.message);
}

async function send(to: string, subject: string, html: string, text: string): Promise<boolean> {
  const provider = emailProvider();
  if (provider === "none") {
    await logEvent("email.skipped", `${subject} -> ${to} (no email provider configured)`);
    return false;
  }
  try {
    if (provider === "brevo") await sendViaBrevo(to, subject, html, text);
    else await sendViaResend(to, subject, html, text);
    await logEvent("email.sent", `${provider} ${subject} -> ${to}`);
    return true;
  } catch (error) {
    await logEvent("email.failed", `${provider} ${subject} -> ${to}: ${String(error).slice(0, 200)}`);
    return false;
  }
}

/** The shell every message shares. Inline styles only — email clients strip everything else. */
function wrap(heading: string, body: string): string {
  return `<!doctype html><html><body style="margin:0;background:#0B0B0D;color:#F5F5F7;font-family:'Segoe UI',system-ui,-apple-system,sans-serif;font-size:16px;line-height:1.55">
  <div style="max-width:560px;margin:0 auto;padding:32px 24px">
    <p style="font-size:18px;font-weight:600;letter-spacing:.2px;margin:0 0 24px;color:#FF7A1A">Nimbus</p>
    <h1 style="font-size:24px;line-height:1.25;margin:0 0 16px;color:#F5F5F7">${heading}</h1>
    ${body}
    <p style="margin:32px 0 0;color:#8A8A94;font-size:13px">
      Nimbus &middot; <a href="${siteUrl()}" style="color:#FF7A1A;text-decoration:none">${new URL(siteUrl()).host}</a>
      &middot; reply to this email and a person will answer.
    </p>
  </div></body></html>`;
}

export async function sendVerificationEmail(to: string, link: string): Promise<boolean> {
  return send(
    to,
    "Confirm your Nimbus email",
    wrap(
      "Confirm your email",
      `<p style="color:#A1A1AA;margin:0 0 24px">One click and your account is ready.</p>
       <p style="margin:0 0 24px"><a href="${link}" style="display:inline-block;background:#FF7A1A;color:#1A0E04;font-weight:600;padding:12px 22px;border-radius:8px;text-decoration:none">Confirm my email</a></p>
       <p style="color:#8A8A94;font-size:14px;margin:0">Or paste this into your browser:<br>${link}<br>The link works once and expires in an hour.</p>`,
    ),
    `Confirm your Nimbus email:\n${link}\n\nThe link works once and expires in an hour.`,
  );
}

export async function sendSignInLinkEmail(to: string, link: string): Promise<boolean> {
  return send(
    to,
    "Your Nimbus sign-in link",
    wrap(
      "Sign in to Nimbus",
      `<p style="color:#A1A1AA;margin:0 0 24px">No password needed. This link signs you in and expires in 30 minutes.</p>
       <p style="margin:0 0 24px"><a href="${link}" style="display:inline-block;background:#FF7A1A;color:#1A0E04;font-weight:600;padding:12px 22px;border-radius:8px;text-decoration:none">Sign me in</a></p>
       <p style="color:#8A8A94;font-size:14px;margin:0">If you did not ask for this, ignore it — nothing changes until the link is opened.</p>`,
    ),
    `Sign in to Nimbus:\n${link}\n\nExpires in 30 minutes. Ignore this email if you did not ask for it.`,
  );
}

/** The one that matters. Sent when a licence is issued, whichever route issued it. */
export async function sendLicenceEmail(
  to: string,
  licenceKey: string,
  options: { seats: number; renewsOn: string; method: string },
): Promise<boolean> {
  const download = `${siteUrl()}/download`;
  return send(
    to,
    "Your Nimbus licence key",
    wrap(
      "You're in.",
      `<p style="color:#A1A1AA;margin:0 0 20px">Your subscription is active. Here is your licence key — keep this email.</p>
       <p style="font-family:Consolas,monospace;font-size:22px;letter-spacing:2px;background:#08080A;border:1px solid #33333A;border-radius:8px;padding:16px;margin:0 0 24px;color:#F5F5F7">${licenceKey}</p>
       <p style="margin:0 0 8px;color:#A1A1AA"><strong style="color:#F5F5F7">Next:</strong></p>
       <ol style="color:#A1A1AA;margin:0 0 24px;padding-left:20px">
         <li style="margin:6px 0"><a href="${download}" style="color:#FF7A1A;text-decoration:none">Download Nimbus for Windows</a> and run the installer.</li>
         <li style="margin:6px 0">On first launch, choose <strong style="color:#F5F5F7">I have a licence key</strong>.</li>
         <li style="margin:6px 0">Paste the key above. Nimbus checks it once, then works offline.</li>
       </ol>
       <p style="margin:0 0 24px"><a href="${download}" style="display:inline-block;background:#FF7A1A;color:#1A0E04;font-weight:600;padding:12px 22px;border-radius:8px;text-decoration:none">Download for Windows</a></p>
       <p style="color:#8A8A94;font-size:14px;margin:0">Your licence covers ${options.seats} computers and renews on ${options.renewsOn}. Paid by ${options.method}.
       Move a seat any time from Account &rarr; Deactivate this device. Your key is always at
       <a href="${siteUrl()}/account" style="color:#FF7A1A;text-decoration:none">your account page</a>.</p>`,
    ),
    [
      "You're in. Your Nimbus subscription is active.",
      "",
      `Licence key: ${licenceKey}`,
      "",
      `1. Download: ${download}`,
      '2. On first launch choose "I have a licence key".',
      "3. Paste the key. Nimbus checks it once, then works offline.",
      "",
      `Covers ${options.seats} computers. Renews ${options.renewsOn}. Paid by ${options.method}.`,
      `Your key is always at ${siteUrl()}/account`,
    ].join("\n"),
  );
}

/** Sent when a manual EasyPaisa or bank transfer is submitted, so the tester knows we have it. */
export async function sendManualPaymentReceivedEmail(to: string, reference: string): Promise<boolean> {
  return send(
    to,
    "We have your payment details",
    wrap(
      "Got it — checking now",
      `<p style="color:#A1A1AA;margin:0 0 20px">Thanks. We have your transfer reference <strong style="color:#F5F5F7">${reference}</strong> and will confirm it by hand, usually within a few hours.</p>
       <p style="color:#A1A1AA;margin:0">Your licence key arrives by email the moment it clears, and appears at
       <a href="${siteUrl()}/account" style="color:#FF7A1A;text-decoration:none">your account page</a>.</p>`,
    ),
    `Thanks. We have your transfer reference ${reference} and will confirm it by hand, usually within a few hours. Your licence key arrives by email the moment it clears.`,
  );
}


/** The 6-digit code, for someone sitting in front of the desktop app waiting to type it. */
export async function sendCodeEmail(
  to: string,
  code: string,
  purpose: "verify" | "reset",
): Promise<boolean> {
  const verifying = purpose === "verify";
  return send(
    to,
    // The code is in the subject line on purpose: it is readable from a notification without opening
    // anything, which is the difference between typing it straight in and going hunting for it.
    `${code} is your Nimbus ${verifying ? "verification" : "password reset"} code`,
    wrap(
      verifying ? "Confirm your email" : "Reset your password",
      `<p style="color:#A1A1AA;margin:0 0 20px">${
        verifying
          ? "Type this code into Nimbus to start your free trial."
          : "Type this code into the reset page to set a new password."
      }</p>
       <p style="font-family:Consolas,monospace;font-size:34px;letter-spacing:10px;font-weight:600;background:#08080A;border:1px solid #33333A;border-radius:8px;padding:18px 20px;margin:0 0 22px;color:#F5F5F7;text-align:center">${code}</p>
       <p style="color:#8A8A94;font-size:14px;margin:0">It expires in 20 minutes and works once. If you
       did not ask for it, nothing has happened to your account and you can ignore this.</p>`,
    ),
    [
      verifying
        ? "Type this code into Nimbus to start your free trial:"
        : "Type this code into the reset page to set a new password:",
      "",
      `    ${code}`,
      "",
      "It expires in 20 minutes and works once.",
    ].join("\n"),
  );
}
