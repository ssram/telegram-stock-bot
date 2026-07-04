/**
 * Telegram webhook receiver -> GitHub repository_dispatch forwarder
 * ====================================================================
 * Deploy this as a Cloudflare Worker (free tier). Telegram calls this
 * Worker the instant a message arrives; the Worker immediately forwards
 * the command to GitHub, which triggers the "Telegram Command Handler"
 * workflow within seconds.
 *
 * Required Worker secrets (set in Cloudflare dashboard -> Settings ->
 * Variables and Secrets, or via `wrangler secret put`):
 *   GITHUB_REPO   e.g. "your-username/telegram-stock-bot"
 *   GITHUB_TOKEN  a GitHub Personal Access Token with `repo` scope
 *
 * After deploying, register this Worker's URL as your bot's webhook:
 *   https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>
 */

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("ok");
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      return new Response("ok"); // ignore malformed payloads
    }

    const message = update.message;
    if (!message || !message.text) {
      return new Response("ok");
    }

    const text = message.text;
    const userId = message.from ? message.from.id : "";
    const username = message.from ? (message.from.username || "unknown") : "unknown";

    try {
      const resp = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`,
        {
          method: "POST",
          headers: {
            "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
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

    // Always return 200 to Telegram quickly, regardless of GitHub's response,
    // so Telegram doesn't retry-storm this webhook.
    return new Response("ok");
  },
};
