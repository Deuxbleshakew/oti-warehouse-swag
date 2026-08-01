# Version 5.4 — visible Project & Delivery workflow

- Added a dedicated **Project & Delivery** tab so the new event fields are no longer hidden below the catalog/cart.
- Added a large catalog button that opens the event and shipping setup directly.
- Added a visible `v5.4 EVENT SHIPPING` badge on both login and the signed-in header.
- Kept direct manual quantity entry on every item card.
- Delivery address, event date, UPS Ground transit days, deliver-by date, and ship-by date are shown together in one setup screen.
- Added no-cache response headers for `/` and `/order.html` so browsers and hosting proxies do not keep serving an older HTML build after redeployment.
- `/health` and `/version` now expose the deployed build name (`5.4-event-shipping`) for quick verification.
