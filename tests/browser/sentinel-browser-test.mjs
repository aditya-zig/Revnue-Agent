import fs from "node:fs/promises";
import path from "node:path";

const baseUrl = process.env.SENTINEL_BASE_URL;
const chromePort = process.env.SENTINEL_CHROME_PORT;
const outputDir = process.env.SENTINEL_SCREENSHOT_DIR;
if (!baseUrl || !chromePort || !outputDir) {
  throw new Error("SENTINEL_BASE_URL, SENTINEL_CHROME_PORT and SENTINEL_SCREENSHOT_DIR are required");
}

await fs.mkdir(outputDir, { recursive: true });

async function createTarget() {
  const response = await fetch(
    `http://127.0.0.1:${chromePort}/json/new?${encodeURIComponent("about:blank")}`,
    { method: "PUT" },
  );
  if (!response.ok) throw new Error(`Unable to create Chrome target: ${response.status}`);
  return response.json();
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
  }

  async ready() {
    if (this.socket.readyState === WebSocket.OPEN) return;
    await new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
  }

  async send(method, params = {}) {
    const id = this.nextId++;
    const promise = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return promise;
  }

  close() {
    this.socket.close();
  }
}

const target = await createTarget();
const cdp = new Cdp(target.webSocketDebuggerUrl);
await cdp.ready();
await cdp.send("Page.enable");
await cdp.send("Runtime.enable");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function evaluate(expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    awaitPromise: true,
    returnByValue: true,
  });
  if (result.exceptionDetails) {
    throw new Error(result.exceptionDetails.text || "Runtime.evaluate failed");
  }
  return result.result?.value;
}

async function viewport(width, height, mobile = false) {
  await cdp.send("Emulation.setDeviceMetricsOverride", {
    width,
    height,
    deviceScaleFactor: 1,
    mobile,
  });
}

async function navigate(url) {
  await cdp.send("Page.navigate", { url });
  for (let i = 0; i < 80; i += 1) {
    const ready = await evaluate("document.readyState === 'complete'");
    if (ready) break;
    await sleep(100);
  }
  await sleep(900);
}

async function waitFor(expression, label, timeoutMs = 12000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await evaluate(expression)) return;
    await sleep(200);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

async function click(selector, label = selector) {
  const clicked = await evaluate(`(() => { const element = document.querySelector(${JSON.stringify(selector)}); if (!element) return false; element.click(); return true; })()`);
  if (!clicked) throw new Error(`Unable to click ${label}`);
  await sleep(350);
}

async function screenshot(name) {
  const result = await cdp.send("Page.captureScreenshot", {
    format: "png",
    captureBeyondViewport: false,
  });
  await fs.writeFile(path.join(outputDir, name), Buffer.from(result.data, "base64"));
}

async function bodyIncludes(text) {
  return evaluate(`document.body.innerText.includes(${JSON.stringify(text)})`);
}

await viewport(1920, 1080);
await navigate(`${baseUrl}/`);
await waitFor("document.querySelector('#startSandbox') !== null", "guided landing CTA");
const ctaCount = await evaluate(`Array.from(document.querySelectorAll('button')).filter((button) => button.textContent.trim() === 'Try it for yourself →').length`);
if (ctaCount !== 1) throw new Error(`Expected one Try it for yourself CTA, found ${ctaCount}`);
if (!(await bodyIncludes("Catch payment incidents before they become lost revenue."))) {
  throw new Error("Guided landing headline is missing");
}
await screenshot("landing-1920x1080.png");

await viewport(1280, 900);
await sleep(250);
await screenshot("landing-1280x900.png");

await viewport(390, 844, true);
await sleep(250);
await screenshot("landing-mobile-390x844.png");

await viewport(1920, 1080);
await click("#startSandbox", "Try it for yourself");
await waitFor("document.querySelector('#sandbox').classList.contains('active')", "sandbox transition");
if (!(await bodyIncludes("Payments are healthy")) || !(await bodyIncludes("Actual recovered"))) {
  throw new Error("Healthy merchant sandbox is missing payment-health copy");
}
await screenshot("sandbox-healthy-1920x1080.png");

await click("#guideAction", "Open test storefront");
await waitFor("document.querySelector('#guideAction').textContent.includes('Continue after test purchase')", "test purchase continuation");
await click("#guideAction", "Continue after test purchase");
await waitFor("document.querySelector('#incidentFocus').classList.contains('show')", "incident interruption", 18000);
if (!(await bodyIncludes("UPI payment degradation detected")) || !(await bodyIncludes("ESTIMATED AT RISK"))) {
  throw new Error("Incident interruption is missing risk framing");
}
await screenshot("sandbox-incident-1920x1080.png");

await click("#reviewIncident", "Review incident");
await waitFor("document.querySelector('#reviewPanel').classList.contains('show')", "incident review");
if (!(await bodyIncludes("AI ANALYSIS — ADVISORY"))) throw new Error("AI analysis is not visibly advisory");
if (!(await bodyIncludes("Policy decides what the model is allowed to rank."))) throw new Error("Policy-first explanation is missing");
if (!(await bodyIncludes("Blocked actions never reach the recommendation model."))) throw new Error("Blocked-action boundary is missing");
if (!(await bodyIncludes("Send alternate payment link"))) throw new Error("Recommended recovery action is missing");
const baselineRate = Number(await evaluate("document.querySelector('#factBaseline').textContent.replace('%', '')"));
const currentRate = Number(await evaluate("document.querySelector('#factCurrent').textContent.replace('%', '')"));
const affectedPayments = Number(await evaluate("document.querySelector('#factAffected').textContent"));
if (!(baselineRate > currentRate)) {
  throw new Error(`Incident must show degradation; baseline ${baselineRate}% is not above current ${currentRate}%`);
}
if (!(affectedPayments > 0)) {
  throw new Error(`Incident must have affected payments; saw ${affectedPayments}`);
}
await screenshot("sandbox-review-1920x1080.png");

await click("#generateAnalysis", "Generate analysis");
await waitFor(
  "document.querySelector('#generateAnalysis').textContent.includes('Analysis generated') || !document.querySelector('#aiMeta').classList.contains('hidden')",
  "advisory analysis response",
  20000,
);
if (await bodyIncludes("Advisory analysis could not be generated")) {
  const errorText = await evaluate("document.querySelector('#aiMeta').textContent");
  throw new Error(`Incident analysis failed in browser journey: ${errorText}`);
}
if (!(await evaluate("document.querySelector('#generateAnalysis').textContent.includes('Analysis generated')"))) {
  throw new Error("Incident analysis did not reach generated state");
}
await screenshot("sandbox-analysis-1920x1080.png");

await click("#approveAction", "Approve recommended action");
await waitFor("document.querySelector('#awaiting').classList.contains('show')", "approval waiting state", 15000);
await waitFor("document.querySelector('#awaitTitle').textContent.includes('Payment link created') || document.querySelector('#awaitTitle').textContent.includes('Recovery action created')", "recovery execution", 15000);
const awaitingAmount = await evaluate("document.querySelector('#awaitAmount').textContent.trim()");
if (awaitingAmount !== "₹0") throw new Error(`Approval must not count as recovery; saw ${awaitingAmount}`);
if (!(await bodyIncludes("Approval is not recovery."))) throw new Error("Approval/recovery boundary is missing");
const recoveredVisible = await evaluate("document.querySelector('#recovered').classList.contains('show')");
if (recoveredVisible) throw new Error("Recovered state appeared before provider outcome evidence");
await screenshot("sandbox-awaiting-provider-1920x1080.png");

await viewport(1280, 900);
await sleep(250);
await screenshot("sandbox-awaiting-provider-1280x900.png");

await navigate(`${baseUrl}/storefront`);
await viewport(1280, 900);
await waitFor("document.querySelector('.back-link') !== null", "storefront Back button");
if (!(await bodyIncludes("Razorpay · TEST MODE")) || !(await bodyIncludes("Buy Now"))) {
  throw new Error("Storefront is missing Test Mode or checkout copy");
}
await screenshot("storefront-1280x900.png");

await viewport(390, 844, true);
await sleep(250);
await screenshot("storefront-mobile-390x844.png");

console.log(JSON.stringify({
  ok: true,
  ctaCount,
  baselineRate,
  currentRate,
  affectedPayments,
  awaitingAmount,
  recoveredVisible,
  screenshots: [
    "landing-1920x1080.png",
    "landing-1280x900.png",
    "landing-mobile-390x844.png",
    "sandbox-healthy-1920x1080.png",
    "sandbox-incident-1920x1080.png",
    "sandbox-review-1920x1080.png",
    "sandbox-analysis-1920x1080.png",
    "sandbox-awaiting-provider-1920x1080.png",
    "sandbox-awaiting-provider-1280x900.png",
    "storefront-1280x900.png",
    "storefront-mobile-390x844.png",
  ],
}));
cdp.close();
