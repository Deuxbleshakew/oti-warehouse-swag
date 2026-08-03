# Oti-Warehouse Swag v5.9 Access & Polish

## Requester portal

- Added per-user themes: Warehouse Dark, Light, High Contrast, and System.
- Added per-user favorite items and a Favorites catalog filter.
- Added prominent In Stock, Low Stock, and Out of Stock labels.
- Out-of-stock items cannot be added to the cart.
- Removed the visible frame from enlarged product images.
- Catalog cards and protected product images now obey server-side visibility rules.

## Project sharing and catalog permissions

- Added project members with Viewer, Editor, and Owner access levels.
- Project members can see shared order status, tracking, and proof photos.
- Editors and owners can update pending shared orders; viewers are read-only.
- Added restricted projects so unrelated users do not see them in the project list.
- Added per-user catalog access by category, brand, or individual item.
- Restrictions are enforced by the API, including search results, direct item URLs,
  item images, favorites, count requests, order submission, and pending-order edits.
- Historical orders remain visible to their requester and assigned project members.

## Projects and addresses

- Added a Projects tab to the Python admin app.
- Admins can create, edit, deactivate/remove, and assign members to projects.
- Projects can use a fixed saved address or a variable per-order address.
- Variable project orders create a private snapshot while retaining the template's
  assigned members, so the same people can continue following the order.

## Picking and inventory

- Picking slips now include compact product thumbnails and remain sorted by location.
- Removed Boxes and Tracking fields from the picking slip. Tracking remains in Mark Done.
- Picking-slip images are embedded without leaving an authentication token in the file.
- Completing a recount requires the physical quantity and automatically adjusts stock.
- Deleted part numbers are released for reuse while historical records retain snapshots.

## Notifications

- The Python admin app shows a topmost new-order alert, unread badge, and optional sound.
- Optional Twilio SMS alerts can include order number, requester, project, unit count,
  incomplete status, and a link to the deployed portal.
- SMS credentials are read only from environment variables and provider failures never
  block order submission.

## Python admin app polish

- Added order summary cards for Pending, Incomplete, Approved, Picking, and Fulfilled.
- Improved shared styling for cards, badges, forms, comboboxes, buttons, and tables.
- Added Projects and Users & Access management views.
- Kept the existing dark warehouse / safety-yellow visual identity.

## Compatibility

- Existing SQLite and PostgreSQL databases upgrade additively.
- PostgreSQL migrations use Render-safe timestamp and boolean syntax.
- New tables are created automatically and existing records are not replaced.
