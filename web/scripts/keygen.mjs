/**
 * Generate the Ed25519 licence keypair.
 *
 *     node scripts/keygen.mjs
 *
 * Prints both halves and saves neither. The private half goes into the deployment's environment and
 * nowhere else; the public half is baked into the desktop build by
 * `python -m tools.set_licence_key --public-key <key>` from the repo root.
 *
 * If you already generated a pair for the Python service, **use that one** — the public half is
 * already inside a shipped installer, and a new pair invalidates every licence issued so far.
 */
import crypto from "node:crypto";

const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");

// Raw 32-byte keys, base64url, matching what the desktop client expects. The DER wrappers are fixed
// lengths, so the raw bytes are simply the tail: 16 bytes of PKCS#8 header, 12 of SPKI.
const privateRaw = privateKey.export({ format: "der", type: "pkcs8" }).subarray(16);
const publicRaw = publicKey.export({ format: "der", type: "spki" }).subarray(12);

const privateB64 = privateRaw.toString("base64url");
const publicB64 = publicRaw.toString("base64url");

// Prove the pair works before printing it, so a bad pair cannot reach a deployment.
const signature = crypto.sign(null, Buffer.from("nimbus"), privateKey);
if (!crypto.verify(null, Buffer.from("nimbus"), publicKey, signature)) {
  throw new Error("Generated pair failed to verify. Do not use it.");
}

console.log("Generated an Ed25519 licence keypair.\n");
console.log("Server secret — set this in Vercel and nowhere else:");
console.log(`  NIMBUS_LICENCE_PRIVATE_KEY=${privateB64}\n`);
console.log("Public half — safe to expose. Set it in Vercel too, and bake it into the app:");
console.log(`  NIMBUS_LICENCE_PUBLIC_KEY=${publicB64}`);
console.log(`  python -m tools.set_licence_key --public-key ${publicB64}\n`);
console.log("Neither half is saved by this script. Store the private one now.");
