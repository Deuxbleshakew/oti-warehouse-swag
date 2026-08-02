# Oti-Warehouse Swag v5.7 Workflow

## Requester web page

- Delivery address can be left blank at submission. The order is accepted and marked **Incomplete / Address pending** until completed.
- Each requested quantity can be marked **Estimated** and confirmed later.
- Pending orders can be edited by their requester from **My Orders**, including line quantities, estimated flags, address, event details, attendees, and notes.
- Requester editing closes automatically after approval or rejection.
- Completed orders show all tracking numbers and authenticated completion photos.
- Every catalog item has a lightweight **Request Count** action with an optional note.
- UPS Ground transit days are calculated automatically from the destination state and cannot be overridden.
- Added polished motion and feedback: card lift, button/stepper press states, sliding toasts, fading dialogs/lightbox, tab cross-fades, and real loading spinners.

## Admin desktop app

- Orders is now a complete, filterable order-history view for pending, approved, picking, fulfilled, and rejected orders.
- Incomplete orders are clearly flagged and cannot be approved until the address and estimated quantities are completed.
- Approved orders can be moved to **Picking**.
- Picking orders can be marked **Done** only with at least one tracking number and at least one completion photo. Multiple tracking numbers and photos are supported.
- Any order can be edited by an admin/approver, with stock corrected automatically when approved or fulfilled quantities change.
- Inventory shows recount-request badges and lets an admin resolve requests after a physical count.
- Added editable Inventory History. Correcting an old adjustment also corrects current on-hand stock.
- Added safe item and user deletion. Records with operational history must be deactivated instead of erased.
- Password guidance is visible in Users. Saved passwords remain hidden and can only be reset.
- Added hover/press styling, best-effort dialog fades, and animated loading indicators for network work.

## Backend and data

- Added order lifecycle: `pending -> approved -> picking -> fulfilled`.
- Added estimated line quantities, picking/fulfillment timestamps, tracking records, proof-photo storage, and count-request records.
- Proof photos require authentication and are only available to the requester or an admin/approver.
- Order edits and inventory-history corrections are audit logged.
- Existing databases upgrade additively on startup. Existing rows and tables are preserved.

## v5.7.1 Render deployment fix

- Made the additive database upgrade dialect-aware.
- PostgreSQL now receives `TIMESTAMP WITHOUT TIME ZONE` instead of SQLite's
  `DATETIME` type.
- PostgreSQL boolean columns now use `DEFAULT FALSE` instead of numeric
  `DEFAULT 0`.
- Local SQLite upgrades remain supported with their original compatible types.
