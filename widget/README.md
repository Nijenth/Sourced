# Sourced website widget

Create a widget on the dashboard’s Website widget page. Add the exact origins of the websites that will embed it and select the ready documents visitors can search. The document selections are public. Reserve private documents for signed-in access when it becomes available.

Copy the generated script tag into your website just before `</body>`. It loads asynchronously with a versioned URL and a SHA-384 integrity hash. The widget loads its stylesheet from the same versioned directory. When changing these assets after publishing a version, bump the version in `backend/app/widget_assets.py` so published snippets keep their integrity guarantee.

For a site with a Content Security Policy, allow the Sourced public origin in `script-src`, `style-src` and `connect-src`, and allow `blob:` in `img-src` for cited-page previews. The embed uses a Shadow DOM and needs no inline scripts or evaluated code. Questions and answers stay in the widget’s memory for the current page.

Use the dashboard’s hosted chat link for a standalone view or a mobile WebView. The hosted view uses the same API and widget settings.
