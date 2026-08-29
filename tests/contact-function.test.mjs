import test from 'node:test';
import assert from 'node:assert/strict';
import { onRequestPost } from '../functions/api/contact.js';
import { onRequestGet as healthGet } from '../functions/api/health.js';

const baseEnv = {
  FORM_DELIVERY_PROVIDER: 'cloudflare-email-api',
  SITE_NAME: 'Test Restaurant',
  FORM_TO_EMAIL: 'owner@restaurant.test',
  FORM_FROM_EMAIL: 'website@restaurant.test',
  FORM_SUBJECT_PREFIX: 'Website inquiry',
  CLOUDFLARE_ACCOUNT_ID: 'account-id',
  CLOUDFLARE_EMAIL_API_TOKEN: 'token-kept-in-runtime-env',
  REQUIRE_TURNSTILE: 'true',
  TURNSTILE_SECRET_KEY: 'turnstile-runtime-secret',
  TURNSTILE_EXPECTED_HOSTNAME: 'restaurant.test',
  TURNSTILE_EXPECTED_ACTION: 'restaurant_contact',
  ALLOWED_ORIGINS: 'https://restaurant.test',
};

function requestFrom(fields, origin = 'https://restaurant.test') {
  const body = new URLSearchParams(fields);
  return new Request('https://restaurant.test/api/contact', {
    method: 'POST',
    headers: { Origin: origin, Referer: 'https://restaurant.test/contact' },
    body,
  });
}

function installFetchMock({
  turnstileSuccess = true,
  turnstileHostname = baseEnv.TURNSTILE_EXPECTED_HOSTNAME,
  turnstileAction = baseEnv.TURNSTILE_EXPECTED_ACTION,
  emailSuccess = true,
} = {}) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, options = {}) => {
    const target = String(url);
    calls.push({ target, options });
    if (target.includes('/turnstile/v0/siteverify')) {
      return Response.json({
        success: turnstileSuccess,
        hostname: turnstileHostname,
        action: turnstileAction,
      });
    }
    if (target.includes('/email/sending/send')) {
      return Response.json(
        emailSuccess
          ? { delivered: ['owner@restaurant.test'], queued: [], permanent_bounces: [], message_id: 'test-message-id' }
          : { errors: [{ code: 10001, message: 'test failure' }] },
        { status: emailSuccess ? 200 : 400 },
      );
    }
    throw new Error(`Unexpected fetch target: ${target}`);
  };
  return { calls, restore: () => { globalThis.fetch = original; } };
}

test('valid form verifies Turnstile and sends only to fixed recipient', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: requestFrom({
        form_type: 'general-contact',
        name: 'Owner Test',
        email: 'guest@customer.test',
        message: 'Please confirm a table request.',
        privacy_consent: 'true',
        'cf-turnstile-response': 'valid-token',
        to: 'attacker@unapproved.test',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(result.ok, true);
    assert.equal(mock.calls.length, 2);
    const emailCall = mock.calls.find((call) => call.target.includes('/email/sending/send'));
    const payload = JSON.parse(emailCall.options.body);
    assert.equal(payload.to, baseEnv.FORM_TO_EMAIL);
    assert.notEqual(payload.to, 'attacker@unapproved.test');
    assert.match(payload.subject, /general-contact/);
  } finally {
    mock.restore();
  }
});

test('missing privacy consent is rejected before delivery', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: requestFrom({ name: 'Guest', email: 'guest@customer.test', message: 'Hello' }),
      env: baseEnv,
    });
    assert.equal(response.status, 400);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('honeypot receives generic success without external delivery', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: requestFrom({ website: 'spam', name: 'Bot', email: 'bot@spam.test', privacy_consent: 'true' }),
      env: baseEnv,
    });
    assert.equal(response.status, 200);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('wrong browser origin is rejected', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: requestFrom({ name: 'Guest', email: 'guest@customer.test', message: 'Hello', privacy_consent: 'true' }, 'https://unapproved.test'),
      env: baseEnv,
    });
    assert.equal(response.status, 403);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('failed Turnstile verification blocks delivery', async () => {
  const mock = installFetchMock({ turnstileSuccess: false });
  try {
    const response = await onRequestPost({
      request: requestFrom({
        name: 'Guest', email: 'guest@customer.test', message: 'Hello', privacy_consent: 'true', 'cf-turnstile-response': 'bad-token',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 400);
    assert.equal(mock.calls.filter((call) => call.target.includes('/email/sending/send')).length, 0);
  } finally {
    mock.restore();
  }
});

test('Turnstile hostname mismatch blocks delivery', async () => {
  const mock = installFetchMock({ turnstileHostname: 'lookalike.test' });
  try {
    const response = await onRequestPost({
      request: requestFrom({
        name: 'Guest', email: 'guest@customer.test', message: 'Hello', privacy_consent: 'true', 'cf-turnstile-response': 'valid-token',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 400);
    assert.equal(mock.calls.filter((call) => call.target.includes('/email/sending/send')).length, 0);
  } finally {
    mock.restore();
  }
});

test('Turnstile action mismatch blocks delivery', async () => {
  const mock = installFetchMock({ turnstileAction: 'different_action' });
  try {
    const response = await onRequestPost({
      request: requestFrom({
        name: 'Guest', email: 'guest@customer.test', message: 'Hello', privacy_consent: 'true', 'cf-turnstile-response': 'valid-token',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 400);
    assert.equal(mock.calls.filter((call) => call.target.includes('/email/sending/send')).length, 0);
  } finally {
    mock.restore();
  }
});

test('missing Turnstile hostname or action contract fails closed', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: requestFrom({
        name: 'Guest', email: 'guest@customer.test', message: 'Hello', privacy_consent: 'true', 'cf-turnstile-response': 'valid-token',
      }),
      env: { ...baseEnv, TURNSTILE_EXPECTED_ACTION: '' },
    });
    assert.equal(response.status, 503);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('unconfigured delivery returns an owner-safe fallback status', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: requestFrom({
        name: 'Guest', email: 'guest@customer.test', message: 'Hello', privacy_consent: 'true', 'cf-turnstile-response': 'valid-token',
      }),
      env: { ...baseEnv, FORM_DELIVERY_PROVIDER: 'disabled' },
    });
    assert.equal(response.status, 503);
    const result = await response.json();
    assert.match(result.message, /phone or email/i);
  } finally {
    mock.restore();
  }
});

test('health endpoint reports state without revealing secret values', async () => {
  const response = await healthGet({ env: baseEnv });
  assert.equal(response.status, 200);
  const data = await response.json();
  assert.equal(data.formDeliveryConfigured, true);
  assert.equal(data.turnstileConfigured, true);
  assert.equal(data.turnstileHostnameConfigured, true);
  assert.equal(data.turnstileActionConfigured, true);
  const serialized = JSON.stringify(data);
  assert.equal(serialized.includes(baseEnv.CLOUDFLARE_EMAIL_API_TOKEN), false);
  assert.equal(serialized.includes(baseEnv.TURNSTILE_SECRET_KEY), false);
  assert.equal(serialized.includes(baseEnv.TURNSTILE_EXPECTED_HOSTNAME), false);
  assert.equal(serialized.includes(baseEnv.TURNSTILE_EXPECTED_ACTION), false);
});


test('multipart bodies are rejected so files and unbounded multipart payloads cannot be submitted', async () => {
  const form = new FormData();
  form.set('name', 'Guest');
  form.set('email', 'guest@customer.test');
  form.set('message', 'Hello');
  form.set('privacy_consent', 'true');
  const response = await onRequestPost({
    request: new Request('https://restaurant.test/api/contact', {
      method: 'POST',
      headers: { Origin: 'https://restaurant.test' },
      body: form,
    }),
    env: baseEnv,
  });
  assert.equal(response.status, 400);
  const result = await response.json();
  assert.match(result.message, /without attachments/i);
});

test('oversized streaming body without Content-Length is cancelled before delivery', async () => {
  let pulls = 0;
  let cancelled = false;
  const body = new ReadableStream({
    pull(controller) {
      pulls += 1;
      controller.enqueue(new Uint8Array(8192).fill(97));
      if (pulls >= 10) controller.close();
    },
    cancel() {
      cancelled = true;
    },
  });
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: new Request('https://restaurant.test/api/contact', {
        method: 'POST',
        headers: {
          Origin: 'https://restaurant.test',
          'content-type': 'application/x-www-form-urlencoded',
        },
        body,
        duplex: 'half',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 413);
    assert.equal(cancelled, true);
    assert.ok(pulls < 10);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('invalid Content-Length is rejected before reading the body', async () => {
  let bodyRead = false;
  const request = {
    url: 'https://restaurant.test/api/contact',
    headers: new Headers({
      Origin: 'https://restaurant.test',
      'content-type': 'application/x-www-form-urlencoded',
      'content-length': 'not-a-number',
    }),
    body: {
      getReader() {
        bodyRead = true;
        throw new Error('body must not be read');
      },
    },
  };
  const response = await onRequestPost({
    request,
    env: baseEnv,
  });
  assert.equal(response.status, 400);
  assert.equal(bodyRead, false);
});

test('declared Content-Length over the limit is rejected before delivery', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: new Request('https://restaurant.test/api/contact', {
        method: 'POST',
        headers: {
          Origin: 'https://restaurant.test',
          'content-type': 'application/x-www-form-urlencoded',
          'content-length': String(32 * 1024 + 1),
        },
        body: 'name=Guest',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 413);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('declared Content-Length must match the bytes actually received', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: new Request('https://restaurant.test/api/contact', {
        method: 'POST',
        headers: {
          Origin: 'https://restaurant.test',
          'content-type': 'application/x-www-form-urlencoded',
          'content-length': '100',
        },
        body: 'name=Guest',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 400);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});

test('compressed request bodies are rejected before decoding', async () => {
  const mock = installFetchMock();
  try {
    const response = await onRequestPost({
      request: new Request('https://restaurant.test/api/contact', {
        method: 'POST',
        headers: {
          Origin: 'https://restaurant.test',
          'content-type': 'application/x-www-form-urlencoded',
          'content-encoding': 'gzip',
        },
        body: 'compressed-placeholder',
      }),
      env: baseEnv,
    });
    assert.equal(response.status, 400);
    assert.equal(mock.calls.length, 0);
  } finally {
    mock.restore();
  }
});
