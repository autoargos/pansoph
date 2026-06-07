import { defineConfig } from "wxt";

// scout is Manifest V3. It requests only activeTab (the page the user explicitly
// captures), scripting, and storage. It never runs in the background on pages, never
// replays credentials, and never bypasses authentication. It reads the DOM the
// signed in user already sees, on an explicit click.
export default defineConfig({
  manifest: {
    name: "pansoph scout",
    description: "Capture what you can already see, with consent, into pansoph.",
    permissions: ["activeTab", "scripting", "storage"],
    host_permissions: ["<all_urls>"],
  },
});
