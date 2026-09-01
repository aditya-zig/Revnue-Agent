(() => {
  const button = document.getElementById("buy-now");
  const status = document.getElementById("checkout-status");
  if (!button || !status) return;

  const key = `dumbbell-checkout-${window.crypto?.randomUUID?.() || Date.now()}`;

  function show(message, kind = "") {
    status.textContent = message;
    status.className = `status ${kind}`.trim();
  }

  async function reportClientFailure(error) {
    const orderId = error?.metadata?.order_id || error?.order_id;
    if (!orderId) return;
    try {
      await fetch("/api/v1/checkout/failure", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          order_id: orderId,
          payment_id: error?.metadata?.payment_id || error?.payment_id,
        }),
      });
    } catch (_) {
      // The signed provider webhook, not this best-effort UI report, is authoritative.
    }
  }

  async function sendCallback(result) {
    try {
      const response = await fetch("/api/v1/checkout/callback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          razorpay_order_id: result.razorpay_order_id,
          razorpay_payment_id: result.razorpay_payment_id,
          razorpay_signature: result.razorpay_signature,
        }),
      });
      if (!response.ok) throw new Error("callback verification failed");
      show("Payment submitted. Waiting for the verified Test Mode webhook.", "success");
    } catch (_) {
      show("Payment submitted; ReRoute is waiting for the verified provider webhook.", "success");
    }
  }

  async function openCheckout() {
    button.disabled = true;
    show("Preparing secure Test Mode checkout…");
    try {
      const response = await fetch("/api/v1/orders", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": key,
        },
        body: JSON.stringify({ idempotency_key: key }),
      });
      const order = await response.json();
      if (!response.ok) throw new Error(order.detail || "Could not create order");
      if (!order.key_id.startsWith("rzp_test_")) throw new Error("Test Mode key required");
      if (typeof window.Razorpay !== "function") throw new Error("Checkout is unavailable");

      const checkout = new window.Razorpay({
        key: order.key_id,
        amount: order.amount,
        currency: order.currency,
        name: order.name,
        description: order.description,
        order_id: order.order_id,
        handler: sendCallback,
        modal: { ondismiss: () => show("Checkout closed.") },
      });
      checkout.on("payment.failed", (event) => {
        show("Test Mode payment failed. ReRoute is waiting for the signed provider webhook.", "error");
        void reportClientFailure(event?.error || event);
      });
      checkout.open();
      show("Checkout opened. Choose the Test Mode failure path to continue the demo.");
    } catch (error) {
      show(error instanceof Error ? error.message : "Could not start checkout.", "error");
    } finally {
      button.disabled = false;
    }
  }

  button.addEventListener("click", () => void openCheckout());
})();
