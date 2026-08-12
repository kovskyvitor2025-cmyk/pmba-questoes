import os
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app import app, db, User, Materia, Conteudo, Question, PaymentRequest, Feedback, Answer, Comment

# Execute from the project root.
# SOURCE_DB_URL defaults to the local SQLite database used by this project.
# TARGET DATABASE_URL must point to an EMPTY PostgreSQL database.
source_url = os.environ.get("SOURCE_DB_URL", "sqlite:///instance/questoes.db")
target_url = os.environ.get("DATABASE_URL")
if not target_url:
    raise SystemExit("Defina DATABASE_URL apontando para o PostgreSQL de destino.")

if target_url.startswith("postgres://"):
    target_url = target_url.replace("postgres://", "postgresql+psycopg2://", 1)
elif target_url.startswith("postgresql://"):
    target_url = target_url.replace("postgresql://", "postgresql+psycopg2://", 1)

source_engine = create_engine(source_url)
target_engine = create_engine(target_url)

models = [User, Materia, Conteudo, Question, PaymentRequest, Feedback, Answer, Comment]

with app.app_context():
    # Create the schema on the target using the same SQLAlchemy models.
    db.metadata.create_all(target_engine)

with Session(source_engine) as source, Session(target_engine) as target:
    counts = {}
    # Insert in FK-safe order.
    for model in models:
        rows = source.scalars(select(model).order_by(model.id)).all()
        for row in rows:
            data = {c.name: getattr(row, c.name) for c in model.__table__.columns}
            target.add(model(**data))
        target.commit()
        counts[model.__name__] = len(rows)

print("Migração concluída:")
for name, count in counts.items():
    print(f"  {name}: {count}")
print("Confira as quantidades no PostgreSQL antes de liberar o site.")
