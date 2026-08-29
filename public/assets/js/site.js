const year = document.querySelector('[data-year]');
if (year) year.textContent = String(new Date().getFullYear());

const form = document.querySelector('[data-contact-form]');
if (form) {
  const status = form.querySelector('[data-form-status]');
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    status.textContent = 'Sending...';
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch(form.action, {
        method: 'POST',
        headers: {
          'accept': 'application/json',
          'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
        },
        body: new URLSearchParams(new FormData(form)),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.ok) throw new Error(result.message || 'The message could not be sent.');
      status.textContent = result.message;
      form.reset();
      if (window.turnstile) window.turnstile.reset();
    } catch (error) {
      status.textContent = error.message || 'The message could not be sent. Please call the restaurant.';
    } finally {
      button.disabled = false;
    }
  });
}
