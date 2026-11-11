# Sourced: design reference

These are the screens to build (see `docs/fullstack-brief.md`, section 6). They are design mockups, not production code: the `.dc.html` format needs a design viewer to render (`./support.js` and `{{brand}}` placeholders won't work in a plain browser), but the markup is readable and shows the layout, wording, states and styles to match.

"Brightleaf" is a made-up example customer. Text such as hotel limits is placeholder content, not taken from the sample handbook.

| File | Screen | Where it lives |
| --- | --- | --- |
| `Documents.dc.html` | Admin · Documents: upload with the provider notice, status per file (ready, processing with progress, couldn't read), delete | `frontend/` |
| `Ask.dc.html` | Admin · Ask (test): model picker, answers with numbered citations, accuracy-check badge, not-found answer, source panel with verified quote and page preview | `frontend/` |
| `WidgetSettings.dc.html` | Admin · Website widget: embed snippet, access mode (public or signed-in), allowed websites, documents, answer model, accuracy check, fallback text, title, brand colour, live preview | `frontend/` |
| `Widget.dc.html` | The chat bubble open on a customer's own website, in the customer's brand colour | `widget/` |
| `MobileChat.dc.html` | The hosted chat page inside a customer's Android/iOS app; tapping a source opens a bottom sheet with the quote and page | `widget/` (hosted page) |

## Style tokens (admin dashboard)

| Token | Value |
| --- | --- |
| Fonts | Inter 400/500/600 (text), Space Grotesk 500/600 (logo, page titles) |
| Background / card | `#F6F7F9` / `#FFFFFF` |
| Borders | `#E3E6EB`, stronger `#CDD2DA` |
| Text | ink `#14161A`, body `#343944`, muted `#5B6170` |
| Accent (Sourced teal) | `#0F766E`, dark `#0B5A54`, tint `#E7F3F1` |
| Processing | `#EEF1F6` background, `#2D3A57` text |
| Warning | `#FDF1E3` background, `#8A4B08` text |
| Quote highlight | `#FFF4C2` |
| Radius | 8–10 px controls, 14 px cards |
| Touch targets | at least 44 px |

The widget and hosted page take the customer's brand colour (`brand_color` on the widget) instead of the Sourced teal; everything else stays neutral.
