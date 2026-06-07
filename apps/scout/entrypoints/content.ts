import { Readability } from "@mozilla/readability";

// The content script only acts when the popup explicitly asks it to capture. It
// extracts the readable text of the page the user is looking at, using Readability
// on a clone of the live DOM, and returns it. It never sends anything on its own.
export default defineContentScript({
  matches: ["<all_urls>"],
  main() {
    browser.runtime.onMessage.addListener((message: { action?: string }) => {
      if (message?.action !== "capture") return;
      const clone = document.cloneNode(true) as Document;
      const article = new Readability(clone).parse();
      return Promise.resolve({
        url: location.href,
        title: article?.title ?? document.title,
        text: article?.textContent ?? document.body.innerText,
      });
    });
  },
});
