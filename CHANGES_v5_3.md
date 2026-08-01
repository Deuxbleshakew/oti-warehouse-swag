# Version 5.3: Event Delivery Planning

- Added a complete delivery address to each new project/event order.
- Event date is now required for event orders.
- Delivery is automatically targeted for the business day before the event.
- Latest warehouse ship date is calculated by subtracting UPS Ground transit
  business days from the delivery target.
- Weekends and common observed U.S. federal holidays are skipped.
- Destination state auto-fills a conservative transit estimate based on the
  supplied outbound map; the requester can override it from 1–6 days for an
  exact ZIP quote.
- The supplied transit map is available inside the order form as a reference.
- Address, transit time, delivery date, and ship-by date are stored with the
  project/order and displayed in requester history and the approval screen.
- Existing SQLite/Postgres databases receive the new project columns through a
  safe additive startup upgrade. Existing orders remain readable.
