export async function onRequestGet(context) {
  const env = context.env || {};
  const provider = String(env.FORM_DELIVERY_PROVIDER || "disabled").toLowerCase();
  const emailConfigured = provider === "cloudflare-email-api" && Boolean(
    env.CLOUDFLARE_ACCOUNT_ID &&
    env.CLOUDFLARE_EMAIL_API_TOKEN &&
    env.FORM_TO_EMAIL &&
    env.FORM_FROM_EMAIL
  );
  const webhookConfigured = provider === "webhook" && Boolean(
    env.FORM_WEBHOOK_URL && env.FORM_WEBHOOK_SECRET
  );
  const turnstileRequired = String(env.REQUIRE_TURNSTILE || "true").toLowerCase() !== "false";
  const turnstileSecretConfigured = Boolean(env.TURNSTILE_SECRET_KEY);
  const turnstileHostnameConfigured = Boolean(env.TURNSTILE_EXPECTED_HOSTNAME);
  const turnstileActionConfigured = Boolean(env.TURNSTILE_EXPECTED_ACTION);

  return Response.json({
    status: "ok",
    service: "restaurant-website-form",
    provider,
    formDeliveryConfigured: emailConfigured || webhookConfigured,
    turnstileRequired,
    turnstileConfigured: turnstileSecretConfigured && turnstileHostnameConfigured && turnstileActionConfigured,
    turnstileSecretConfigured,
    turnstileHostnameConfigured,
    turnstileActionConfigured,
    allowedOriginsConfigured: Boolean(env.ALLOWED_ORIGINS),
    timestamp: new Date().toISOString(),
  }, {
    headers: { "cache-control": "no-store" },
  });
}

export async function onRequest(context) {
  return new Response("Method not allowed", {
    status: 405,
    headers: { allow: "GET" },
  });
}
