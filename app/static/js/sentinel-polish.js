function polishEmptyMerchantState() {
  const runtime = window.ReRouteSentinel?.getState?.();
  const total = Number(runtime?.dashboard?.population?.total || 0);
  if (total !== 0) return;

  const healthValues = document.querySelectorAll(".health-strip .health-value");
  for (const value of [healthValues[0], healthValues[1]]) {
    if (value && value.textContent !== "—") value.textContent = "—";
  }

  const legend = document.querySelector(".chart-legend");
  const chartBody = legend?.closest(".card-body");
  if (chartBody && !chartBody.querySelector("[data-empty-baseline]")) {
    chartBody.innerHTML = '<div class="feed-empty" data-empty-baseline>No baseline yet. Start the interactive demo to establish normal merchant payment performance.</div>';
  }
}

const observer = new MutationObserver(polishEmptyMerchantState);
observer.observe(document.documentElement, { childList: true, subtree: true });
window.addEventListener("load", polishEmptyMerchantState);
polishEmptyMerchantState();
