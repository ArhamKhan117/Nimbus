/**
 * EasyPaisa, in the two forms it actually comes in.
 *
 * ## What I checked, because this is the part everyone assumes
 *
 * EasyPaisa **does** have an official online payment gateway, and it is not self-serve. Per
 * [EasyPaisa's own gateway page](https://easypaisa.com.pk/online-payment-gateway/) it is offered to a
 * business with a live site or app, which means a merchant application, a registered business, and a
 * `storeId` plus `hashKey` issued to you. Integrators describe two modes — a hosted checkout that
 * redirects the tester to EasyPaisa's page, and a direct REST call — with the request carrying
 * `storeId`, `amount`, `orderRefNum` and `postBackURL`
 * ([integration notes](https://github.com/zfhassaan/easypaisa)).
 * *Content was rephrased for compliance with licensing restrictions.*
 *
 * So there is no "just add EasyPaisa" the way there is with Stripe. Two paths, and this file supports
 * both:
 *
 * 1. **Hosted checkout** — live the moment `EASYPAISA_STORE_ID` and `EASYPAISA_HASH_KEY` are set.
 *    Builds the signed form the tester's browser POSTs to EasyPaisa.
 * 2. **Manual transfer** — works today, with no merchant account. The tester sends money to your
 *    EasyPaisa or bank account, submits the transaction reference, and you approve it. Neither rail is
 *    connected in this deployment - the code path exists and is covered by tests, and nothing is
 *    charged.
 *
 * The manual path is deliberately not disguised as automatic. A page that says "we will confirm this
 * by hand, usually within a few hours" and then does is better than a spinner that lies.
 *
 * ## The hash
 *
 * EasyPaisa's guide specifies AES-128-ECB over the sorted request parameters using the merchant hash
 * key, base64-encoded, sent as `merchantHashedReq`. Integrators report "Request Rejected" almost
 * always traces to parameter order, a non-HTTPS `postBackURL`, or the wrong key length — so the
 * parameters are sorted here and the key length is checked rather than trusted.
 */
import crypto from "node:crypto";

export type EasypaisaConfig = {
  storeId: string;
  hashKey: string;
  postUrl: string;
  accountNumber: string;
  accountName: string;
  bankName: string;
  bankAccount: string;
  bankIban: string;
  /** The name on the account. Banks reject transfers whose beneficiary name does not match. */
  bankTitle: string;
  /** Only needed for an international transfer, so it is rendered conditionally rather than always. */
  bankSwift: string;
  priceLocal: string;
};

const SANDBOX_URL = "https://easypaystg.easypaisa.com.pk/easypay/Index.jsf";
const LIVE_URL = "https://easypay.easypaisa.com.pk/easypay/Index.jsf";

export function easypaisaConfig(): EasypaisaConfig {
  return {
    storeId: process.env.EASYPAISA_STORE_ID ?? "",
    hashKey: process.env.EASYPAISA_HASH_KEY ?? "",
    postUrl: process.env.EASYPAISA_MODE === "live" ? LIVE_URL : SANDBOX_URL,
    accountNumber: process.env.EASYPAISA_ACCOUNT_NUMBER ?? "",
    accountName: process.env.EASYPAISA_ACCOUNT_NAME ?? "",
    bankName: process.env.BANK_NAME ?? "",
    bankAccount: process.env.BANK_ACCOUNT_NUMBER ?? "",
    bankIban: process.env.BANK_IBAN ?? "",
    bankTitle: process.env.BANK_ACCOUNT_TITLE ?? "",
    bankSwift: process.env.BANK_SWIFT ?? "",
    priceLocal: process.env.PRICE_PKR ?? "2,800",
  };
}

/** Whether the automated hosted checkout can be offered at all. */
export function hostedCheckoutAvailable(): boolean {
  const { storeId, hashKey } = easypaisaConfig();
  return Boolean(storeId && hashKey);
}

/** Whether we can even tell someone where to send money manually. */
export function manualTransferAvailable(): boolean {
  const config = easypaisaConfig();
  return Boolean(config.accountNumber || config.bankAccount);
}

function hashRequest(parameters: Record<string, string>, hashKey: string): string {
  if (![16, 24, 32].includes(Buffer.byteLength(hashKey))) {
    throw new Error(
      `EASYPAISA_HASH_KEY is ${Buffer.byteLength(hashKey)} bytes; EasyPaisa issues a 16-byte key.`,
    );
  }
  const ordered = Object.keys(parameters)
    .sort()
    .map((key) => `${key}=${parameters[key]}`)
    .join("&");
  const cipher = crypto.createCipheriv("aes-128-ecb", hashKey, null);
  cipher.setAutoPadding(true);
  return Buffer.concat([cipher.update(ordered, "utf8"), cipher.final()]).toString("base64");
}

/**
 * The fields for the form the browser POSTs to EasyPaisa.
 *
 * `amount` is in rupees, so this needs a local-currency figure of its own. Currency conversion is
 * not done here on purpose: a rate that moves mid-checkout is how a payment ends up
 * one rupee short and rejected. `PRICE_PKR_AMOUNT` is a number you set and change deliberately.
 */
export function hostedCheckoutFields(
  orderRefNum: string,
  postBackUrl: string,
): Record<string, string> {
  const config = easypaisaConfig();
  const amount = process.env.PRICE_PKR_AMOUNT ?? "2800";
  const expiry = new Date(Date.now() + 60 * 60_000)
    .toISOString()
    .replace(/\.\d+Z$/, "");

  const parameters: Record<string, string> = {
    storeId: config.storeId,
    amount,
    postBackURL: postBackUrl,
    orderRefNum,
    expiryDate: expiry,
    autoRedirect: "1",
    paymentMethod: "",
  };
  return { ...parameters, merchantHashedReq: hashRequest(parameters, config.hashKey) };
}

export function easypaisaPostUrl(): string {
  return easypaisaConfig().postUrl;
}
