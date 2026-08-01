"""
scripts/seed_demo.py — populates the database with realistic demo data for
local testing. Safe to run once on a fresh database (created via
init_db.py first). Re-running will raise on the unique constraints rather
than duplicate data — that's intentional, it's a seed script, not a
fixture factory.

    python scripts/init_db.py
    python scripts/seed_demo.py
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.db.session import SessionLocal
from backend.models.models import (User, Role, Project, Item, Order,
                                    OrderLine, Approval, InventoryTransaction,
                                    AuditLog)
from backend.auth.security import hash_password


def main():
    db = SessionLocal()
    try:
        # ---- Roles ----------------------------------------------------------
        role_names = ["requester", "approver", "admin"]
        roles = {}
        for name in role_names:
            r = Role(name=name, description=f"{name.capitalize()} role")
            db.add(r)
            roles[name] = r
        db.flush()   # assign IDs without committing yet

        # ---- Users ------------------------------------------------------------
        max_user = User(username="max", full_name="Max",
                        password_hash=hash_password("changeme123"),
                        active=True)
        max_user.roles = [roles["admin"], roles["approver"]]

        fabiana = User(username="fabiana", full_name="Fabiana",
                      password_hash=hash_password("changeme123"),
                      active=True)
        fabiana.roles = [roles["requester"]]

        albert = User(username="albert", full_name="Albert",
                     password_hash=hash_password("changeme123"),
                     active=True)
        albert.roles = [roles["requester"], roles["approver"]]

        db.add_all([max_user, fabiana, albert])
        db.flush()

        # ---- Projects -----------------------------------------------------
        gov_services = Project(
            name="VA Hospital Job Fair 2027",
            description="Outreach booth for veterans",
            purpose="Recruiting + awareness",
            owner="Fabiana", event_date="2027-03-15",
            delivery_date="2027-03-10", location="Newark VA Hospital",
            attendees=300, budget=500.0, status="planning")
        retail = Project(
            name="Retail Trade Show", description="Convention booth",
            owner="Albert", event_date="2027-05-01",
            location="Convention Center", attendees=150, budget=1200.0,
            status="planning")
        db.add_all([gov_services, retail])
        db.flush()

        # ---- Items ----------------------------------------------------------
        items = [
            Item(code="OT-PEN-001", name="Oticon Blue Pen", category="Pens & Writing",
                brand="OT", measures='5.5"', location="Shelf B-3",
                qty_on_hand=500, reorder_threshold=50, cost=0.75),
            Item(code="OT-BAG-001", name="Canvas Tote", category="Bags & Totes",
                brand="OT", measures="15x16 in", location="Shelf A-1",
                qty_on_hand=80, reorder_threshold=20, cost=4.50),
            Item(code="GS-PRT-001", name="Government Services Notebook",
                category="Print / Paper", brand="GS", measures="8.5x5.5 in",
                location="Shelf B-1", qty_on_hand=256, reorder_threshold=50,
                cost=2.10),
            Item(code="BDG-DRK-001", name="Bernafon Mug", category="Drinkware",
                brand="BDG", measures="16 oz", location="Shelf C-2",
                qty_on_hand=60, reorder_threshold=20, cost=3.25),
        ]
        db.add_all(items)
        db.flush()

        # ---- Opening-stock inventory transactions --------------------------
        for it in items:
            db.add(InventoryTransaction(
                item_id=it.id, delta=it.qty_on_hand,
                reason="Initial stock (seed)", source="admin_app",
                user_id=max_user.id))

        # ---- One sample order end-to-end (pending -> approved) -------------
        order = Order(requester_user_id=fabiana.id, project_id=gov_services.id,
                     status="approved", notes="Booth setup")
        db.add(order)
        db.flush()
        line1 = OrderLine(order_id=order.id, item_id=items[0].id,
                          qty_requested=300, qty_approved=300)
        line2 = OrderLine(order_id=order.id, item_id=items[2].id,
                          qty_requested=50, qty_approved=50)
        db.add_all([line1, line2])
        db.add(Approval(order_id=order.id, approver_user_id=max_user.id,
                        decision="approved", reason="Looks good"))
        # reflect the approval in stock + transaction history, same as the
        # backend's approval service will do for real requests
        items[0].qty_on_hand -= 300
        items[2].qty_on_hand -= 50
        db.add(InventoryTransaction(item_id=items[0].id, delta=-300,
                                    reason=f"Order #{order.id} approved",
                                    source="admin_app", user_id=max_user.id))
        db.add(InventoryTransaction(item_id=items[2].id, delta=-50,
                                    reason=f"Order #{order.id} approved",
                                    source="admin_app", user_id=max_user.id))

        # ---- Audit trail for the seed actions themselves -------------------
        db.add(AuditLog(user_id=max_user.id, action="seed.bootstrap",
                        object_type="system", object_id="",
                        new_value="Demo data seeded", source="admin_app"))

        db.commit()
        print("Seed complete.")
        print(f"  Users: max/changeme123 (admin+approver), "
              f"fabiana/changeme123 (requester), albert/changeme123 "
              f"(requester+approver)")
        print(f"  Projects: {gov_services.name!r}, {retail.name!r}")
        print(f"  Items: {len(items)}")
        print(f"  Sample order #{order.id}: approved, 2 lines")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
