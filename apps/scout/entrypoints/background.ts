// scout does no background page work. Capture is always user initiated from the
// popup. This entrypoint exists so the extension has a service worker.
export default defineBackground(() => {
  // Intentionally empty. No autonomous scraping.
});
