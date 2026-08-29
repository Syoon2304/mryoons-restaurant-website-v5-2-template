function jsonError(message, status = 500) {
  return new Response(JSON.stringify({ ok: false, message }), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

export async function onRequest(context) {
  try {
    const response = await context.next();
    const wrapped = new Response(response.body, response);
    wrapped.headers.set("cache-control", "no-store");
    wrapped.headers.set("x-content-type-options", "nosniff");
    wrapped.headers.set("referrer-policy", "strict-origin-when-cross-origin");
    wrapped.headers.set("x-frame-options", "DENY");
    return wrapped;
  } catch (error) {
    console.error("API function error", error);
    return jsonError("The website could not process that request. Use the listed phone or email fallback.", 500);
  }
}
