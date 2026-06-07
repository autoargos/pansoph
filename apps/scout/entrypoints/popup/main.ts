// Vanilla popup. The only place capture starts, and only on an explicit click. The
// user controls the tenant, the groups the page is shared with, and whether it is
// public. Nothing is sent without this action.

interface Settings {
  agora: string;
  tenant: string;
  groups: string;
  isPublic: boolean;
}

interface Captured {
  url: string;
  title: string;
  text: string;
}

const el = (id: string) => document.getElementById(id) as HTMLInputElement;
const statusEl = () => document.getElementById("status") as HTMLParagraphElement;

function current(): Settings {
  return {
    agora: el("agora").value,
    tenant: el("tenant").value,
    groups: el("groups").value,
    isPublic: el("isPublic").checked,
  };
}

async function load(): Promise<void> {
  const r = await browser.storage.local.get("settings");
  const s = r.settings as Settings | undefined;
  if (!s) return;
  el("agora").value = s.agora;
  el("tenant").value = s.tenant;
  el("groups").value = s.groups;
  el("isPublic").checked = s.isPublic;
}

async function capture(): Promise<void> {
  statusEl().textContent = "capturing...";
  const s = current();
  await browser.storage.local.set({ settings: s });
  try {
    const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("no active tab");
    const got = (await browser.tabs.sendMessage(tab.id, { action: "capture" })) as Captured;
    const res = await fetch(`${s.agora}/api/grab`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        url: got.url,
        title: got.title,
        text: got.text,
        tenant_id: s.tenant,
        allow_groups: s.groups.split(",").map((g) => g.trim()).filter(Boolean),
        public: s.isPublic,
      }),
    });
    const data = (await res.json()) as { indexed?: number };
    statusEl().textContent = `indexed ${data.indexed ?? 0} chunks`;
  } catch (e) {
    statusEl().textContent = String(e);
  }
}

document.getElementById("capture")?.addEventListener("click", () => void capture());
void load();
