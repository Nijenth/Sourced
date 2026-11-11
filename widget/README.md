# Sourced website widget

Create a widget on the dashboard’s Website widget page. Add the exact origins of the websites that will embed it and select the ready documents visitors can search. Use signed-in access for private documents. Treat every document attached to a public widget as public.

Copy the generated script tag into your website just before `</body>`. It loads asynchronously with a versioned URL and a SHA-384 integrity hash. The widget loads its stylesheet from the same versioned directory.

Release 1.2.0 shows a specific message when the AI provider is busy, down or not set up (the API's `rate_limited`, `provider_unavailable` and `misconfigured` codes). The widget always uses its own wording for these and never displays text from the server.

To release a new widget version, copy `widget.js` and `widget.css` into a new `dist/<major.minor.patch>/` directory and append their SHA-384 hashes to `dist/releases.json`. Keep the files and hashes for earlier releases intact. The API serves every listed release and uses the newest one in new snippets.

For a site with a Content Security Policy, allow the Sourced public origin in `script-src`, `style-src` and `connect-src`, and allow `blob:` in `img-src` for cited-page previews. The embed uses a Shadow DOM and needs no inline scripts or evaluated code. Questions and answers stay in the widget’s memory for the current page.

Enable **Allow a hosted chat link (for apps and direct links)** to use a standalone view or a mobile WebView. The hosted view uses the same API and widget settings.

## Signed-in website embeds

A signed-in widget needs a session token from your own authenticated backend. Keep the server secret key on your backend; see [token issuance and hosted WebViews](../docs/mobile.md). The widget talks to your page through events on its `<script>` element. Each event bubbles to `document`, and its `detail` has `siteKey` and `setSessionToken(token)`.

| Event | Direction | When |
| --- | --- | --- |
| `sourced:ready` | widget → page | Once when the widget starts, and again after each `sourced:request-ready` |
| `sourced:request-ready` | page → widget | Dispatch it on the widget’s script element to get `sourced:ready` again |
| `sourced:session-expired` | widget → page | The API refused the current token (HTTP 401) for a question or a cited-page preview |

Use this order, which works whether your script runs before or after the widget loads:

1. Add your `sourced:ready` and `sourced:session-expired` listeners to `document`.
2. Dispatch `sourced:request-ready` on the widget’s script element. If the widget has already started, it answers with `sourced:ready` straight away. If it has not, the request is ignored and the widget sends `sourced:ready` when it starts.

```js
const siteKey = 'pk_your_public_site_key'
async function freshToken() {
  const response = await fetch('/people-chat/session', { method: 'POST' }) // your authenticated endpoint
  if (!response.ok) return null
  return (await response.json()).token
}
async function connect(event) {
  if (event.detail.siteKey !== siteKey) return
  event.detail.setSessionToken(await freshToken())
}
document.addEventListener('sourced:ready', connect)
document.addEventListener('sourced:session-expired', connect)
document.querySelector(`script[data-site-key="${siteKey}"]`)
  ?.dispatchEvent(new Event('sourced:request-ready'))
```

`sourced:ready` can arrive more than once, so make the handler safe to repeat. Passing the token the widget already has does nothing. Don’t dispatch `sourced:request-ready` from inside a `sourced:ready` listener.

When a token expires or is revoked, the widget says the session expired and dispatches `sourced:session-expired`. Call `setSessionToken` with a fresh token; no reload is needed. Any new token starts a new conversation and clears the question box, because earlier answers and their page previews belong to the previous session, which may be a different person’s. Call `setSessionToken(null)` on sign-out to clear the session and the conversation.

The hosted chat page (`/c/pk_…`) takes its token only from the URL fragment. Its `sourced:session-expired` event has `siteKey` alone, and it doesn’t send `sourced:ready`. Apps refresh it by setting `location.hash = 'token=…'`, as shown in [Sourced in mobile apps](../docs/mobile.md#refresh-the-token-without-reloading).
