"""python -m app.utils.seed  -> creates demo users, one venue, one screen with seats."""
from app.auth.security import hash_password
from app.database import SessionLocal
from app.models import Screen, Seat, User, Venue
from app.models.enums import Role, SeatCategory

USERS = [
    ("Admin", "admin@example.com", "9000000001", "Admin@123", Role.ADMIN),
    ("Olivia Organizer", "organizer@example.com", "9000000002", "Organizer@123", Role.ORGANIZER),
    ("Charlie Customer", "customer@example.com", "9000000003", "Customer@123", Role.CUSTOMER),
    ("Charlie Two", "customer2@example.com", "9000000004", "Customer@123", Role.CUSTOMER),
]


def main():
    db = SessionLocal()
    venue = db.query(Venue).filter(Venue.name == "PVR Demo Cinemas").first()
    if not venue:
        venue = Venue(name="PVR Demo Cinemas", city="Hyderabad", address="Banjara Hills", is_active=True)
        db.add(venue)
        db.flush()
        screen = Screen(venue_id=venue.id, name="Screen 1", total_seats=0)
        db.add(screen)
        db.flush()
        n = 0
        for row, cat in (("A", SeatCategory.SILVER), ("B", SeatCategory.SILVER), ("C", SeatCategory.GOLD),
                         ("D", SeatCategory.GOLD), ("E", SeatCategory.RECLINER)):
            for num in range(1, 13):
                db.add(Seat(screen_id=screen.id, row_label=row, seat_number=num, category=cat))
                n += 1
        screen.total_seats = n
    for name, email, phone, pw, role in USERS:
        if not db.query(User).filter(User.email == email).first():
            db.add(User(name=name, email=email, phone=phone, password_hash=hash_password(pw), role=role,
                        is_active=True, is_approved=True))
    if not db.query(User).filter(User.email == "staff@example.com").first():
        db.add(User(name="Gate Staff", email="staff@example.com", phone="9000000005",
                    password_hash=hash_password("Staff@123"), role=Role.STAFF, is_active=True, is_approved=True,
                    venue_id=venue.id))
    db.commit()
    print("Seeded. Logins: admin@example.com / Admin@123, organizer@example.com / Organizer@123, "
          "customer@example.com / Customer@123, staff@example.com / Staff@123")


if __name__ == "__main__":
    main()
