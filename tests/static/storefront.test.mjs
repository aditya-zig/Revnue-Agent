import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const storefrontScript = readFileSync(new URL("../../app/static/js/storefront.js", import.meta.url), "utf8");

function response(body, ok = true) {
  return { ok, json: async () => body };
}

test("buy now opens Checkout with only the server-provided Test Mode order", async () => {
  const calls = [];
  const elements = {
    "buy-now": {
      disabled: false,
      addEventListener: (_type, handler) => { elements.click = handler; },
    },
    "checkout-status": { textContent: "", className: "" },
  };
  let checkout;
  class Razorpay {
    constructor(options) {
      this.options = options;
      this.listeners = {};
      checkout = this;
    }

    on(event, handler) { this.listeners[event] = handler; }
    open() { this.opened = true; }
  }

  const context = {
    document: { getElementById: (id) => elements[id] },
    fetch: async (url, options) => {
      calls.push({ url, options });
      if (url === "/api/v1/orders") {
        return response({
          order_id: "order_test_browser",
          key_id: "rzp_test_browser",
          amount: 249900,
          currency: "INR",
          name: "ReRoute Dumbbell Store",
          description: "5 kg Dumbbell",
        });
      }
      return response({ status: "callback_received" });
    },
    setTimeout,
    window: { crypto: { randomUUID: () => "fixed-browser-key" }, Razorpay },
  };
  vm.runInNewContext(storefrontScript, context);
  elements.click();
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(calls[0].url, "/api/v1/orders");
  assert.equal(JSON.parse(calls[0].options.body).idempotency_key, "dumbbell-checkout-fixed-browser-key");
  assert.equal(checkout.opened, true);
  assert.equal(checkout.options.key, "rzp_test_browser");
  assert.equal(checkout.options.amount, 249900);
  assert.equal(checkout.options.currency, "INR");
  assert.equal(checkout.options.name, "ReRoute Dumbbell Store");
  assert.equal(checkout.options.description, "5 kg Dumbbell");
  assert.equal(checkout.options.order_id, "order_test_browser");
  assert.equal(typeof checkout.options.handler, "function");
  assert.equal(typeof checkout.options.modal.ondismiss, "function");
  assert.equal(calls.some(({ url }) => url === "/api/v1/webhooks/razorpay"), false);

  checkout.listeners["payment.failed"]({ error: { metadata: { order_id: "order_test_browser" } } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(calls.at(-1).url, "/api/v1/checkout/failure");
});
