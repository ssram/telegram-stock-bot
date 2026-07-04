export default {
async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("ok");
    }

    const expectedSecret = (env.TELEGRAM_WEBHOOK_SECRET || "").trim();
    const receivedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";

    if (!expectedSecret || receivedSecret !== expectedSecret) {
      console.log("Rejected request: invalid or missing secret token");
      return new Response("unauthorized", { status: 401 });
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response("ok");
    }

    const message = update.message;
    if (!message || !message.text) {
      return new Response("ok");
    }

    const text = message.text;
    const userId = message.from ? message.from.id : "";
    const username = message.from ? (message.from.username || "unknown") : "unknown";

    try {
const cleanRepo = (env.GITHUB_REPO || "").trim();
const dispatchUrl = `https://api.github.com/repos/${cleanRepo}/dispatches`;
console.log("Full dispatch URL:", dispatchUrl);
const resp = await fetch(
  dispatchUrl,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${(env.GITHUB_TOKEN || "").trim()}`,
            "Accept": "application/vnd.github+json",
            "User-Agent": "telegram-webhook-worker",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            event_type: "telegram_command",
            client_payload: { text, userId, username },
          }),
        }
      );

      if (!resp.ok) {
        console.log("GitHub dispatch failed:", resp.status, await resp.text());
      }
    } catch (err) {
      console.log("Error forwarding to GitHub:", err);
    }

    return new Response("ok");
  },
};