# Sourced in mobile apps

In **Website widget**, choose **Only people signed in to your site or app**, select the documents for that audience, and enable **Allow a hosted chat link (for apps and direct links)**. Save the settings and generate a server secret key. Copy it into your backend’s secret manager when Sourced displays it. Keep the `sk_` key out of website code, mobile builds, and logs. Rotation revokes the old secret and its existing tokens.

Your site or app authenticates the user with your existing sign-in. Your server checks that this user may access the widget, then calls `POST /api/v1/widgets/{id}/session-tokens` with `Authorization: Bearer <server secret key>`. The response contains a widget-scoped bearer token with a 15-minute lifetime. The optional `user_ref` comes from your authenticated user record. Use an opaque identifier because the token’s claims are readable.

## Server example

This FastAPI route belongs to your application. The example assumes `require_people_member` verifies the app’s credentials and authorizes access to this widget. The widget ID and secret come from your server configuration.

```python
import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response

from myapp.auth import PeopleMember, require_people_member

router = APIRouter()
SOURCED_ORIGIN = "https://chat.example.com"
WIDGET_ID = os.environ["SOURCED_WIDGET_ID"]


@router.post("/mobile/people-chat/session")
def people_chat_session(
    member: Annotated[PeopleMember, Depends(require_people_member)],
    response: Response,
):
    try:
        result = httpx.post(
            f"{SOURCED_ORIGIN}/api/v1/widgets/{WIDGET_ID}/session-tokens",
            headers={"Authorization": f"Bearer {os.environ['SOURCED_WIDGET_SECRET_KEY']}"},
            json={"user_ref": str(member.id)},
            timeout=5,
            follow_redirects=False,
        )
        result.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(502, "Could not start chat.") from None
    issued = result.json()
    response.headers["Cache-Control"] = "no-store"
    return {"token": issued["token"], "expires_in": issued["expires_in"]}
```

## Android WebView

With Android API 24 or later, add the `INTERNET` permission to your app manifest and the `androidx.webkit:webkit` library. Call `openPeopleChat` after your authenticated request to your own session endpoint returns a token. Substitute the public site key and your Sourced HTTPS host. `onSessionExpired` runs on the main thread when the hosted page reports an expired or revoked token; use it to fetch a fresh token and pass it to `refreshPeopleChat` ([below](#refresh-the-token-without-reloading)).

```kotlin
import android.net.Uri
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject

const val CHAT_HOST = "chat.example.com"
const val CHAT_PATH = "/c/pk_your_public_site_key"

fun openPeopleChat(webView: WebView, token: String, onSessionExpired: () -> Unit) {
    webView.settings.apply {
        javaScriptEnabled = true
        allowFileAccess = false
        allowContentAccess = false
        mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
    }
    val bridge = WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)
    if (bridge) {
        // The bridge object exists only in frames from the Sourced origin.
        WebViewCompat.addWebMessageListener(webView, "sourcedApp", setOf("https://$CHAT_HOST")) {
            _, _, _, isMainFrame, _ -> if (isMainFrame) onSessionExpired()
        }
    }
    webView.webViewClient = object : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url
            return !(request.isForMainFrame && url.scheme == "https" && url.host == CHAT_HOST &&
                (url.port == -1 || url.port == 443) && url.path == CHAT_PATH)
        }

        override fun onPageFinished(view: WebView, url: String) {
            if (!bridge) return
            view.evaluateJavascript(
                "if (window.sourcedApp && !window.sourcedAppListening) { window.sourcedAppListening = true; " +
                    "document.addEventListener('sourced:session-expired', () => sourcedApp.postMessage('expired')) }",
                null,
            )
        }
    }
    val url = Uri.Builder().scheme("https").authority(CHAT_HOST).path(CHAT_PATH)
        .encodedFragment("token=" + Uri.encode(token)).build()
    webView.loadUrl(url.toString())
}

/** Hands the open hosted chat a fresh token without reloading it. Call on the main thread. */
fun refreshPeopleChat(webView: WebView, token: String) {
    val origin = JSONObject.quote("https://$CHAT_HOST")
    val path = JSONObject.quote(CHAT_PATH)
    val hash = JSONObject.quote("token=" + Uri.encode(token))
    webView.evaluateJavascript(
        "if (location.origin === $origin && location.pathname === $path) location.hash = $hash",
        null,
    )
}
```

[Android WebViewClient reference](https://developer.android.com/reference/android/webkit/WebViewClient) · [AndroidX WebViewCompat reference](https://developer.android.com/reference/androidx/webkit/WebViewCompat)

## iOS WKWebView

With iOS 14 or later, initialize the view controller with the token returned by your authenticated app backend and a closure that fetches a fresh one. Retain the view controller for the lifetime of the WebView so its navigation delegate remains active. The expired-session listener runs in an isolated content world, so the page’s own scripts can’t see or call the message handler.

```swift
import UIKit
import WebKit

final class PeopleChatViewController: UIViewController, WKNavigationDelegate {
    private let host = "chat.example.com"
    private let path = "/c/pk_your_public_site_key"
    private var initialToken: String?
    private let fetchToken: () async -> String?
    private var refreshing = false

    init(token: String, fetchToken: @escaping () async -> String?) {
        initialToken = token
        self.fetchToken = fetchToken
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) { return nil }

    override func loadView() {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        let listener = WKUserScript(
            source: "document.addEventListener('sourced:session-expired', () => " +
                "window.webkit.messageHandlers.sourcedSession.postMessage('expired'))",
            injectionTime: .atDocumentEnd, forMainFrameOnly: true, in: .defaultClient)
        config.userContentController.addUserScript(listener)
        config.userContentController.add(SessionExpiredHandler(self), contentWorld: .defaultClient, name: "sourcedSession")
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        view = webView

        guard let token = initialToken else { return }
        initialToken = nil
        var url = URLComponents()
        url.scheme = "https"
        url.host = host
        url.path = path
        url.fragment = "token=\(token)"
        if let target = url.url { webView.load(URLRequest(url: target)) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        let url = action.request.url
        let allowed = action.targetFrame?.isMainFrame == true && url?.scheme == "https" &&
            url?.host == host && (url?.port == nil || url?.port == 443) && url?.path == path
        decisionHandler(allowed ? .allow : .cancel)
    }

    fileprivate func sessionExpired(from frame: WKFrameInfo) {
        guard frame.isMainFrame, frame.securityOrigin.protocol == "https", frame.securityOrigin.host == host,
              !refreshing else { return }
        refreshing = true
        Task { @MainActor in
            defer { refreshing = false }
            if let token = await fetchToken() { refresh(token: token) }
        }
    }

    /// Hands the open hosted chat a fresh token without reloading it.
    func refresh(token: String) {
        guard let webView = view as? WKWebView,
              let origin = try? String(data: JSONEncoder().encode("https://\(host)"), encoding: .utf8),
              let page = try? String(data: JSONEncoder().encode(path), encoding: .utf8),
              let hash = try? String(data: JSONEncoder().encode("token=\(token)"), encoding: .utf8)
        else { return }
        webView.evaluateJavaScript(
            "if (location.origin === \(origin) && location.pathname === \(page)) location.hash = \(hash)")
    }
}

/// Holds the view controller weakly so the content controller doesn't keep it alive.
private final class SessionExpiredHandler: NSObject, WKScriptMessageHandler {
    private weak var owner: PeopleChatViewController?

    init(_ owner: PeopleChatViewController) { self.owner = owner }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        owner?.sessionExpired(from: message.frameInfo)
    }
}
```

[Apple WKNavigationDelegate reference](https://developer.apple.com/documentation/webkit/wknavigationdelegate) · [Apple WKScriptMessageHandler reference](https://developer.apple.com/documentation/webkit/wkscriptmessagehandler)

## Refresh the token without reloading

Session tokens last 15 minutes, and rotating the server secret key revokes them early. When the hosted page sends a question or opens a cited-page preview with a token the API refuses, it says the session expired and dispatches the bubbling `sourced:session-expired` event. The examples above forward that event to the app.

To continue, fetch a fresh token from your backend and set it on the open page with `location.hash = 'token=…'`: `refreshPeopleChat(webView, token)` on Android (`evaluateJavascript`) or `refresh(token:)` on iOS (`evaluateJavaScript`). The page reads the new token from the fragment, clears the fragment again, and stays loaded. Both helpers first check that the WebView still shows your hosted chat page, so a token never goes to another page. The new token starts a new conversation, because the earlier answers and their page previews belong to the previous session. Avoid reloading the page with the token in the URL; there’s no need to.

You can also refresh before you show the chat again, for example when the app returns to the foreground after `expires_in` seconds. Refreshing early still clears the conversation, so don’t refresh on a timer while someone is chatting.

## Session handling

Pass the token in the URL fragment as `/c/pk_…#token=st_…`. The browser sends the path to the server and keeps the fragment local. Hosted chat clears the fragment and keeps the token in memory. After expiration, set a fresh token as shown [above](#refresh-the-token-without-reloading). After a reload, the page has no token; open chat again with a fresh one. Avoid logging the initial navigation URL in your app or logging authorization headers in your backend or proxy.

A native chat screen can call `POST /api/v1/public/chat` with `Authorization: Bearer <session token>` and `{"question": "...", "history": []}`. Fetch any returned cited-page preview with the same Authorization header. The preview is tied to that session and the client IP bucket, so reopen chat after a network change if needed.
