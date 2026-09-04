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
  await sleep(1200);
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
  await sleep(400);
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
await waitFor("document.querySelector('[data-sentinel-shell]') !== null", "Sentinel shell");
await waitFor("!document.body.innerText.includes('Loading ReRoute Sentinel')", "dashboard data");
if (!(await bodyIncludes("ReRoute Sentinel")) || !(await bodyIncludes("Actual recovered"))) {
  throw new Error("Home view is missing Sentinel or recovered-outcome copy");
}
await screenshot("home-1920x1080.png");

await viewport(1280, 900);
await sleep(300);
await screenshot("home-1280x900.png");

await viewport(390, 844, true);
await sleep(300);
await screenshot("home-mobile-390x844.png");

await viewport(1920, 1080);
await click('[data-nav="incidents"]', "Incidents navigation");
await waitFor("document.body.innerText.includes('Incidents')", "Incidents view");
await click("[data-review-incident]", "Review incident");
await waitFor("document.body.innerText.includes('Verified facts')", "incident detail");
if (!(await bodyIncludes("AI ANALYSIS — ADVISORY"))) throw new Error("AI analysis is not visibly separated");
await screenshot("incident-before-control-1920x1080.png");

if (await evaluate("document.querySelector('[data-action=investigate-incident]') !== null")) {
  await click("[data-action=investigate-incident]", "Investigate incident");
  await waitFor("document.body.innerText.includes('Human approval required')", "recommendation awaiting approval", 20000);
}
if (await evaluate("document.querySelector('[data-action=approve-incident]') !== null")) {
  await click("[data-action=approve-incident]", "Approve recommendation");
  await waitFor("document.body.innerText.includes('Approved by business owner')", "approved state");
}
if (await evaluate("document.querySelector('[data-action=execute-incident]') !== null")) {
  await click("[data-action=execute-incident]", "Execute recommendation");
  await waitFor("document.body.innerText.includes('Awaiting provider Outcome')", "awaiting provider outcome", 12000);
}
const actualRecoveredText = await evaluate(`(() => { const text = document.body.innerText; const match = text.match(/Actual recovered[^₹]*₹[^\n]*/i); return match ? match[0] : text; })()`);
if (!(await bodyIncludes("Removed before AI ranking"))) {
  throw new Error("Policy-blocked action is not labelled as removed before AI ranking");
}
await screenshot("incident-after-control-1920x1080.png");

await viewport(1280, 900);
await sleep(300);
await screenshot("incident-after-control-1280x900.png");

await navigate(`${baseUrl}/storefront`);
await viewport(1280, 900);
await waitFor("document.querySelector('.back-link') !== null", "storefront Back button");
if (!(await bodyIncludes("Razorpay · TEST MODE")) || !(await bodyIncludes("Buy Now"))) {
  throw new Error("Storefront is missing Test Mode or checkout copy");
}
await screenshot("storefront-1280x900.png");

await viewport(390, 844, true);
await sleep(300);
await screenshot("storefront-mobile-390x844.png");

console.log(JSON.stringify({
  ok: true,
  actualRecoveredText,
  screenshots: [
    "home-1920x1080.png",
    "home-1280x900.png",
    "home-mobile-390x844.png",
    "incident-before-control-1920x1080.png",
    "incident-after-control-1920x1080.png",
    "incident-after-control-1280x900.png",
    "storefront-1280x900.png",
    "storefront-mobile-390x844.png",
  ],
}));
cdp.close();
