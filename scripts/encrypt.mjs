#!/usr/bin/env node
// Encrypt the parsed schedule for the static page. WebCrypto ships with Node 20,
// so this needs no npm install and mirrors exactly what the browser does to undo it.
//
//   node scripts/encrypt.mjs plain.json docs/data.json   # PAGE_PASSWORD from env
//   node scripts/encrypt.mjs --selftest

import { webcrypto as crypto } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";

const ITERATIONS = 250000;
const enc = new TextEncoder();
const b64 = (buf) => Buffer.from(buf).toString("base64");

async function deriveKey(password, salt) {
  const base = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, [
    "deriveKey",
  ]);
  return crypto.subtle.deriveKey(
    { name: "PBKDF2", salt, iterations: ITERATIONS, hash: "SHA-256" },
    base,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt", "decrypt"],
  );
}

async function encrypt(plaintext, password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const key = await deriveKey(password, salt);
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(plaintext));
  return { v: 1, kdf: "PBKDF2-SHA256", iter: ITERATIONS, salt: b64(salt), iv: b64(iv), ct: b64(ct) };
}

async function decrypt(payload, password) {
  const un = (s) => new Uint8Array(Buffer.from(s, "base64"));
  const key = await deriveKey(password, un(payload.salt));
  const pt = await crypto.subtle.decrypt(
    { name: "AES-GCM", iv: un(payload.iv) },
    key,
    un(payload.ct),
  );
  return new TextDecoder().decode(pt);
}

async function digest(text) {
  return b64(await crypto.subtle.digest("SHA-256", enc.encode(text)));
}

async function selftest() {
  const secret = JSON.stringify({ hello: "dunia", n: [1, 2, 3] });
  const payload = await encrypt(secret, "selftest-only-not-the-real-password");
  if ((await decrypt(payload, "selftest-only-not-the-real-password")) !== secret) throw new Error("roundtrip mismatch");
  if (payload.ct.includes("dunia")) throw new Error("plaintext leaked into ciphertext");
  let rejected = false;
  await decrypt(payload, "wrong").catch(() => (rejected = true));
  if (!rejected) throw new Error("wrong password was accepted");
  console.log("encrypt.mjs selftest ok");
}

async function main() {
  if (process.argv[2] === "--selftest") return selftest();

  const [, , inPath, outPath] = process.argv;
  const password = process.env.PAGE_PASSWORD;
  if (!inPath || !outPath) throw new Error("usage: encrypt.mjs <plain.json> <out.json>");
  if (!password) throw new Error("PAGE_PASSWORD is not set");

  const plain = JSON.parse(readFileSync(inPath, "utf8"));
  const generated = plain.generated;
  // Hash the payload with `generated` removed, so a rebuild that finds an
  // unchanged roster is a no-op. Salt and IV are fresh every run, so without this
  // the ciphertext would differ daily and CI would commit noise forever.
  delete plain.generated;
  const stable = JSON.stringify(plain);
  const hash = await digest(stable);

  let existing = null;
  try {
    existing = JSON.parse(readFileSync(outPath, "utf8"));
  } catch {}
  if (existing?.hash === hash) {
    console.log("roster unchanged, keeping", outPath);
    return;
  }

  plain.generated = generated;
  const payload = await encrypt(JSON.stringify(plain), password);
  writeFileSync(outPath, JSON.stringify({ ...payload, hash, generated }, null, 1) + "\n");
  console.log(`wrote ${outPath} (${plain.mine.length} duties, ${plain.months.length} months)`);
}

main().catch((err) => {
  console.error(err.message);
  process.exit(1);
});
