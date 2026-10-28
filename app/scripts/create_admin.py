"""Create (or promote) an administrator:  python -m app.scripts.create_admin you@example.org "Your Name" """
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import User, UserRole


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit('Usage: python -m app.scripts.create_admin EMAIL "FULL NAME"')
    email, name = sys.argv[1].lower(), sys.argv[2]
    password = getpass.getpass("Password (min 10 characters): ")
    if len(password) < 10 or len(password.encode()) > 72:
        sys.exit("Password must be 10-72 characters.")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.role, user.is_active, user.hashed_password = UserRole.ADMIN, True, hash_password(password)
        else:
            db.add(User(email=email, full_name=name, role=UserRole.ADMIN, hashed_password=hash_password(password)))
        db.commit()
    print(f"Administrator ready: {email}")


if __name__ == "__main__":
    main()
