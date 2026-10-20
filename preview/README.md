# POLISH HORIZONT static preview

This is a mock-only, offline screen with synthetic fixtures embedded in `index.html`. It does not query SQLite, contact feeds, call an API, or send telemetry. All sample source URLs use the reserved `example.test` domain; links are examples, not live sources. No fictional locations are plotted on a map.

## Open locally

Open `index.html` directly in a browser (for example, double-click the file or use the browser's **Open File** command). No server, package installation, build step, or network connection is needed. The preview uses local `styles.css` and `app.js` files next to the HTML.

Use the PL/EN controls to change language. Choose one timestamp field before entering a range. Date/time inputs are interpreted as UTC; the start is inclusive and the end is exclusive. A missing value for the selected field remains unknown and is not replaced with another timestamp. Source country labels describe provenance only, not verified incident geography.
