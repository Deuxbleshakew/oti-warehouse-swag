# Oti-Warehouse Swag v5.8 Full Queue

## Ordering website
- Sticky header and workflow tabs remain visible while the catalog scrolls.
- New desktop workspace with a collapsible filter rail, responsive 2/3-column catalog, and sticky cart/readiness panel.
- Product photos use a consistent contained image area so the full item remains visible.
- Explicit **Add to Cart / Update Cart** workflow with manual quantities and estimated-quantity state.
- Quick filters for low stock, open recount requests, frequently ordered items, recently viewed items, and categories.
- Order-readiness summary shows pending project, event, address, estimates, and UPS Ground ship-by information.
- Mobile layout uses one-column cards, a filter drawer, large touch targets, and a fixed Review Order button.
- Deleted orders are removed from live requester updates cleanly.

## Admin application
- Any non-current user can be removed from active user management while historical records retain a **Deleted User** label.
- Any catalog part can be removed from active use while order and inventory history retain its original part number and a **Deleted Item** label.
- Any order can be removed from order views without returning already consumed stock. Tracking records and proof photos are removed, while the audit tombstone remains.
- Related stock-ledger rows are relabeled **Deleted Order #...** after order deletion.
- Inventory-history rows can be deleted. Manual adjustments are reversed automatically; order-generated rows do not alter current stock.
- Pressing **Pick Order** opens a printable location-sorted warehouse pick slip, with missing locations grouped last.

## Database and deployment
- Additive `deleted_at` columns are migrated automatically on SQLite and PostgreSQL.
- PostgreSQL migrations retain the v5.7.1 Render-safe timestamp and boolean syntax.
- Existing databases and historical records are preserved during upgrade.
