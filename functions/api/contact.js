const MAX_BODY_BYTES = 32 * 1024;
const ALLOWED_FIELDS = new Set([
  "name",
  "email",
  "phone",
  "message",
  "form_type",
  "location",
  "event_date",
  "event_time",
  "party_size",
  "company",
  "budget",
  "preferred_contact",
  "privacy_consent",
  "cf-turnstile-response",
  "turnstile_token",
  "website",
]);

function responseJson(body, status = 200, origin = null) {
  const headers = new Headers({
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  if (origin) {
    headers.set("access-control-allow-origin", origin);
    headers.set("vary", "Origin");
  }
  return new Response(JSON.stringify(body), { status, headers });
}

function safeLine(value, max = 200) {
  return String(value || "")
    .replace(/[\r\n\0]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, max);
}

function safeBlock(value, max = 5000) {
  return String(value || "")
    .replace(/\0/g, "")
    .trim()
    .slice(0, max);
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeBoolean(value) {
  return ["true", "1", "yes", "on"].includes(String(value || "").toLowerCase());
}

function isEmail(value) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value) && value.length <= 254;
}

function allowedOrigin(request, env) {
  const origin = request.headers.get("Origin");
  if (!origin) return null;
  const sameOrigin = new URL(request.url).origin;
  const configured = String(env.ALLOWED_ORIGINS || "")
    .split(",")
    .map((item) => item.trim().replace(/\/$/, ""))
    .filter(Boolean);
  const allowed = new Set([sameOrigin, ...configured]);
  return allowed.has(origin.replace(/\/$/, "")) ? origin : false;
}

async function parseBody(request) {
  const declaredLength = parseContentLength(request.headers.get("content-length"));
  const contentEncoding = (request.headers.get("content-encoding") || "identity").trim().toLowerCase();
  if (contentEncoding !== "identity") throw new Error("UNSUPPORTED_CONTENT_ENCODING");
  const type = (request.headers.get("content-type") || "")
    .split(";", 1)[0]
    .trim()
    .toLowerCase();
  let raw = {};
  if (type === "application/json") {
    const text = await readBoundedText(request, declaredLength);
    try {
      raw = JSON.parse(text || "{}");
    } catch {
      throw new Error("INVALID_BODY");
    }
  } else if (type === "application/x-www-form-urlencoded") {
    const text = await readBoundedText(request, declaredLength);
    raw = Object.fromEntries(new URLSearchParams(text));
  } else if (type === "multipart/form-data") {
    // This endpoint intentionally rejects multipart bodies. Website forms send URL-encoded data,
    // which keeps the body-size limit enforceable and prevents file uploads.
    throw new Error("FILES_NOT_ALLOWED");
  } else {
    throw new Error("UNSUPPORTED_CONTENT_TYPE");
  }
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) throw new Error("INVALID_BODY");
  const data = {};
  for (const [key, value] of Object.entries(raw)) {
    if (ALLOWED_FIELDS.has(key)) data[key] = value;
  }
  return data;
}

function parseContentLength(value) {
  if (value === null) return null;
  const raw = String(value).trim();
  if (!/^[0-9]+$/.test(raw)) throw new Error("INVALID_CONTENT_LENGTH");
  let length;
  try {
    length = BigInt(raw);
  } catch {
    throw new Error("INVALID_CONTENT_LENGTH");
  }
  if (length > BigInt(MAX_BODY_BYTES)) throw new Error("BODY_TOO_LARGE");
  return Number(length);
}

async function readBoundedText(request, declaredLength) {
  if (!request.body) {
    if (declaredLength !== null && declaredLength !== 0) throw new Error("INVALID_CONTENT_LENGTH");
    return "";
  }

  let reader;
  let byob = false;
  try {
    // Workers request bodies are byte streams. BYOB keeps each read bounded even when the
    // sender omits Content-Length or uses chunked transfer encoding.
    reader = request.body.getReader({ mode: "byob" });
    byob = true;
  } catch {
    // Some standards-compliant test runtimes expose a default reader only. The cumulative
    // byte limit below still prevents an oversized body from being retained or decoded.
    reader = request.body.getReader();
  }

  const decoder = new TextDecoder("utf-8", { fatal: true });
  let text = "";
  let total = 0;
  let complete = false;
  try {
    while (true) {
      const allowance = Math.min(8192, MAX_BODY_BYTES - total + 1);
      const result = byob
        ? await reader.read(new Uint8Array(allowance))
        : await reader.read();
      if (result.done) break;
      if (!(result.value instanceof Uint8Array)) throw new Error("INVALID_BODY");
      if (total + result.value.byteLength > MAX_BODY_BYTES) throw new Error("BODY_TOO_LARGE");
      total += result.value.byteLength;
      text += decoder.decode(result.value, { stream: true });
    }
    text += decoder.decode();
    complete = true;
  } catch (error) {
    await reader.cancel("request body rejected").catch(() => {});
    if (error instanceof TypeError) throw new Error("INVALID_BODY");
    throw error;
  } finally {
    reader.releaseLock();
  }

  if (!complete) throw new Error("INVALID_BODY");
  if (declaredLength !== null && total !== declaredLength) throw new Error("INVALID_CONTENT_LENGTH");
  return text;
}

function turnstileContract(env) {
  const expectedHostname = String(env.TURNSTILE_EXPECTED_HOSTNAME || "")
    .trim()
    .toLowerCase()
    .replace(/\.$/, "");
  const expectedAction = String(env.TURNSTILE_EXPECTED_ACTION || "").trim();
  const hostnamePattern = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/;
  if (!expectedHostname || !hostnamePattern.test(expectedHostname)) throw new Error("TURNSTILE_NOT_CONFIGURED");
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(expectedAction)) throw new Error("TURNSTILE_NOT_CONFIGURED");
  return { expectedHostname, expectedAction };
}

async function verifyTurnstile(token, request, env) {
  const required = String(env.REQUIRE_TURNSTILE || "true").toLowerCase() !== "false";
  if (!required) return { success: true, skipped: true };
  if (!env.TURNSTILE_SECRET_KEY) throw new Error("TURNSTILE_NOT_CONFIGURED");
  const { expectedHostname, expectedAction } = turnstileContract(env);
  if (!token || token.length > 2048) return { success: false };
  const payload = new URLSearchParams({
    secret: env.TURNSTILE_SECRET_KEY,
    response: token,
  });
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) payload.set("remoteip", ip);
  const result = await fetch("https://challenges.cloudflare.com/turnstile/v0/siteverify", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: payload,
  });
  if (!result.ok) return { success: false };
  const verification = await result.json().catch(() => null);
  if (!verification || typeof verification !== "object" || verification.success !== true) {
    return { success: false };
  }
  const receivedHostname = String(verification.hostname || "")
    .trim()
    .toLowerCase()
    .replace(/\.$/, "");
  if (receivedHostname !== expectedHostname || verification.action !== expectedAction) {
    return { success: false };
  }
  return { success: true };
}

function buildSubmission(data, request, env) {
  const submissionId = crypto.randomUUID();
  const submittedAt = new Date().toISOString();
  return {
    submissionId,
    submittedAt,
    siteName: safeLine(env.SITE_NAME || "Restaurant website", 120),
    formType: safeLine(data.form_type || "contact", 60),
    name: safeLine(data.name, 120),
    email: safeLine(data.email, 254),
    phone: safeLine(data.phone, 50),
    message: safeBlock(data.message, 5000),
    location: safeLine(data.location, 160),
    eventDate: safeLine(data.event_date, 40),
    eventTime: safeLine(data.event_time, 40),
    partySize: safeLine(data.party_size, 30),
    company: safeLine(data.company, 160),
    budget: safeLine(data.budget, 80),
    preferredContact: safeLine(data.preferred_contact, 40),
    sourceUrl: safeLine(request.headers.get("Referer") || new URL(request.url).origin, 500),
    userAgent: safeLine(request.headers.get("User-Agent"), 500),
  };
}

function submissionText(s) {
  const rows = [
    ["Submission ID", s.submissionId],
    ["Submitted", s.submittedAt],
    ["Form", s.formType],
    ["Name", s.name],
    ["Email", s.email || "Not supplied"],
    ["Phone", s.phone || "Not supplied"],
    ["Preferred contact", s.preferredContact || "Not supplied"],
    ["Location", s.location || "Not supplied"],
    ["Event date", s.eventDate || "Not supplied"],
    ["Event time", s.eventTime || "Not supplied"],
    ["Party size", s.partySize || "Not supplied"],
    ["Company", s.company || "Not supplied"],
    ["Budget", s.budget || "Not supplied"],
    ["Source page", s.sourceUrl],
  ];
  return `${rows.map(([k, v]) => `${k}: ${v}`).join("\n")}\n\nMessage:\n${s.message || "No message supplied"}`;
}

function submissionHtml(s) {
  const rows = [
    ["Submission ID", s.submissionId],
    ["Submitted", s.submittedAt],
    ["Form", s.formType],
    ["Name", s.name],
    ["Email", s.email || "Not supplied"],
    ["Phone", s.phone || "Not supplied"],
    ["Preferred contact", s.preferredContact || "Not supplied"],
    ["Location", s.location || "Not supplied"],
    ["Event date", s.eventDate || "Not supplied"],
    ["Event time", s.eventTime || "Not supplied"],
    ["Party size", s.partySize || "Not supplied"],
    ["Company", s.company || "Not supplied"],
    ["Budget", s.budget || "Not supplied"],
    ["Source page", s.sourceUrl],
  ];
  return `<!doctype html><html><body><h1>${escapeHtml(s.siteName)} website inquiry</h1><table>${rows
    .map(([k, v]) => `<tr><th align="left">${escapeHtml(k)}</th><td>${escapeHtml(v)}</td></tr>`)
    .join("")}</table><h2>Message</h2><p>${escapeHtml(s.message || "No message supplied").replaceAll("\n", "<br>")}</p></body></html>`;
}

async function deliverCloudflareEmail(submission, env) {
  if (!env.CLOUDFLARE_ACCOUNT_ID || !env.CLOUDFLARE_EMAIL_API_TOKEN || !env.FORM_TO_EMAIL || !env.FORM_FROM_EMAIL) {
    throw new Error("EMAIL_NOT_CONFIGURED");
  }
  const endpoint = `https://api.cloudflare.com/client/v4/accounts/${encodeURIComponent(env.CLOUDFLARE_ACCOUNT_ID)}/email/sending/send`;
  const subjectPrefix = safeLine(env.FORM_SUBJECT_PREFIX || "Website inquiry", 80);
  const result = await fetch(endpoint, {
    method: "POST",
    headers: {
      authorization: `Bearer ${env.CLOUDFLARE_EMAIL_API_TOKEN}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      to: env.FORM_TO_EMAIL,
      from: env.FORM_FROM_EMAIL,
      subject: `${subjectPrefix}: ${submission.formType} - ${submission.name}`,
      text: submissionText(submission),
      html: submissionHtml(submission),
      headers: {
        "X-Website-Submission-ID": submission.submissionId,
        "X-Website-Form-Type": submission.formType,
      },
    }),
  });
  const payload = await result.json().catch(() => ({}));
  if (!result.ok || payload.success === false) {
    console.error("Cloudflare Email API failure", result.status, payload);
    throw new Error("EMAIL_DELIVERY_FAILED");
  }
}

async function deliverWebhook(submission, env) {
  if (!env.FORM_WEBHOOK_URL || !env.FORM_WEBHOOK_SECRET) throw new Error("WEBHOOK_NOT_CONFIGURED");
  const result = await fetch(env.FORM_WEBHOOK_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-website-form-secret": env.FORM_WEBHOOK_SECRET,
    },
    body: JSON.stringify(submission),
  });
  if (!result.ok) {
    console.error("Webhook failure", result.status);
    throw new Error("WEBHOOK_DELIVERY_FAILED");
  }
}

async function deliver(submission, env) {
  const provider = String(env.FORM_DELIVERY_PROVIDER || "disabled").toLowerCase();
  if (provider === "cloudflare-email-api") return deliverCloudflareEmail(submission, env);
  if (provider === "webhook") return deliverWebhook(submission, env);
  throw new Error("DELIVERY_NOT_CONFIGURED");
}

export async function onRequestOptions(context) {
  const origin = allowedOrigin(context.request, context.env || {});
  if (origin === false) return responseJson({ ok: false, message: "Origin not allowed." }, 403);
  return new Response(null, {
    status: 204,
    headers: {
      "access-control-allow-origin": origin || new URL(context.request.url).origin,
      "access-control-allow-methods": "POST, OPTIONS",
      "access-control-allow-headers": "content-type",
      "access-control-max-age": "600",
      vary: "Origin",
    },
  });
}

export async function onRequestPost(context) {
  const env = context.env || {};
  const origin = allowedOrigin(context.request, env);
  if (origin === false) return responseJson({ ok: false, message: "This form must be submitted from the restaurant website." }, 403);

  try {
    const data = await parseBody(context.request);
    if (safeLine(data.website, 200)) {
      // Honeypot submissions receive a generic success response so bots do not learn the rule.
      return responseJson({ ok: true, message: "Thank you. Your message was received." }, 200, origin);
    }
    const name = safeLine(data.name, 120);
    const email = safeLine(data.email, 254);
    const phone = safeLine(data.phone, 50);
    const message = safeBlock(data.message, 5000);
    if (name.length < 2) return responseJson({ ok: false, field: "name", message: "Please enter your name." }, 400, origin);
    if (!email && !phone) return responseJson({ ok: false, field: "email", message: "Please enter an email address or phone number." }, 400, origin);
    if (email && !isEmail(email)) return responseJson({ ok: false, field: "email", message: "Please enter a valid email address." }, 400, origin);
    if (!message && !data.event_date && !data.party_size && !data.location) {
      return responseJson({ ok: false, field: "message", message: "Please tell us how we can help." }, 400, origin);
    }
    if (!normalizeBoolean(data.privacy_consent)) {
      return responseJson({ ok: false, field: "privacy_consent", message: "Please confirm that the restaurant may use this information to respond." }, 400, origin);
    }

    const token = safeLine(data["cf-turnstile-response"] || data.turnstile_token, 2049);
    const turnstile = await verifyTurnstile(token, context.request, env);
    if (!turnstile.success) return responseJson({ ok: false, message: "Please complete the anti-spam check and try again." }, 400, origin);

    const submission = buildSubmission({ ...data, name, email, phone, message }, context.request, env);
    await deliver(submission, env);
    return responseJson({
      ok: true,
      submissionId: submission.submissionId,
      message: "Thank you. Your message was sent successfully.",
    }, 200, origin);
  } catch (error) {
    const code = error instanceof Error ? error.message : "UNKNOWN";
    console.error("Contact form failure", code);
    if (code === "BODY_TOO_LARGE") {
      return responseJson({ ok: false, message: "The form data was too large. Please shorten the message and try again without attachments." }, 413, origin);
    }
    if (["INVALID_CONTENT_LENGTH", "UNSUPPORTED_CONTENT_ENCODING", "FILES_NOT_ALLOWED", "UNSUPPORTED_CONTENT_TYPE", "INVALID_BODY"].includes(code)) {
      return responseJson({ ok: false, message: "The form data was invalid. Please try again without attachments." }, 400, origin);
    }
    if (["TURNSTILE_NOT_CONFIGURED", "EMAIL_NOT_CONFIGURED", "WEBHOOK_NOT_CONFIGURED", "DELIVERY_NOT_CONFIGURED"].includes(code)) {
      return responseJson({ ok: false, message: "Online form delivery is not configured yet. Please use the listed phone or email contact method." }, 503, origin);
    }
    return responseJson({ ok: false, message: "We could not send the form. Please use the listed phone or email contact method." }, 502, origin);
  }
}

export async function onRequest(context) {
  return new Response("Method not allowed", {
    status: 405,
    headers: { allow: "POST, OPTIONS" },
  });
}
