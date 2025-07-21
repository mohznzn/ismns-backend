from sqlalchemy import create_engine, Column, String, Integer
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()

class Resultat(Base):
    __tablename__ = 'resultats'
    id = Column(Integer, primary_key=True)
    nom = Column(String)
    prenom = Column(String)
    email = Column(String)
    telephone = Column(String)
    qcm_id = Column(String)
    score = Column(Integer)

# Connexion à la base SQLite
engine = create_engine('sqlite:///qcm.db')
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
