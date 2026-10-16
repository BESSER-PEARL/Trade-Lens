import enum
import os
# Both spellings are bound on purpose. Generated code uses the trailing-
# underscore aliases so a modelled class named List, Optional or DateTime
# cannot shadow them. The PLAIN names are imported too because this file is
# routinely edited afterwards by an LLM, which writes ordinary Python and has
# no reason to know about the aliasing convention; without the plain names
# such edits fail to import:
#
#     Mapped_[Optional[int]]        undefined name 'Optional'
#     mapped_column(DateTime())     undefined name 'DateTime'
#
# so every aliased name below is bound both ways.
from typing import List, Optional, List as List_, Optional as Optional_
from sqlalchemy import (
    create_engine, Enum, UniqueConstraint as _UniqueConstraint, event as _sa_event,
    Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, Interval,
    PickleType, String, Table, Text, Time,
    Column as Column_, ForeignKey as ForeignKey_, Table as Table_,
    Text as Text_, Boolean as Boolean_, String as String_, Date as Date_,
    Time as Time_, DateTime as DateTime_, Float as Float_, Integer as Integer_,
    Interval as Interval_, PickleType as PickleType_,
)
from sqlalchemy.orm import (
    column_property, DeclarativeBase, Mapped, Mapped as Mapped_, mapped_column,
    relationship
)
from datetime import datetime as dt_datetime, time as dt_time, date as dt_date, timedelta as dt_timedelta
from uuid import uuid4 as _uuid4


def _new_str_id() -> str:
    """Server-side surrogate for string primary keys named ``id``.

    A string PK has no autoincrement; without a default every INSERT dies
    on NOT NULL (the id is in nobody's hands: create schemas rightly
    exclude the server-owned ``id``, so the server must mint it).
    """
    return _uuid4().hex


def _configure_sqlite_engine(engine):
    """Enforce declared foreign keys on every SQLite connection."""
    if engine.dialect.name == "sqlite":
        @_sa_event.listens_for(engine, "connect")
        def _enable_foreign_keys(connection, _record):
            previous_autocommit = getattr(connection, "autocommit", None)
            if previous_autocommit is not None:
                connection.autocommit = True
            cursor = connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
            finally:
                cursor.close()
                if previous_autocommit is not None:
                    connection.autocommit = previous_autocommit


class Base(DeclarativeBase):
    pass

# Definitions of Enumerations
class Flow(enum.Enum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    RE_EXPORT = "RE_EXPORT"

class Freq(enum.Enum):
    A = "A"
    S = "S"
    Q = "Q"
    M = "M"
    W = "W"
    D = "D"
    H = "H"
    B = "B"
    N = "N"

class Indicators(enum.Enum):
    APQNTBASE = "APQNTBASE"
    APQNTFLAG = "APQNTFLAG"
    APRODQNT = "APRODQNT"
    EXPQNT = "EXPQNT"
    EXPVAL = "EXPVAL"
    IMPQNT = "IMPQNT"
    IMPVAL = "IMPVAL"
    OWNPRODQNT = "OWNPRODQNT"
    OWNPRODVAL = "OWNPRODVAL"
    OWNQNTBASE = "OWNQNTBASE"
    OWNQNTFLAG = "OWNQNTFLAG"
    OWNVALBASE = "OWNVALBASE"
    OWNVALFLAG = "OWNVALFLAG"
    PQNTBASE = "PQNTBASE"
    PQNTFLAG = "PQNTFLAG"
    PRODQNT = "PRODQNT"
    PRODVAL = "PRODVAL"
    PVALBASE = "PVALBASE"
    PVALFLAG = "PVALFLAG"
    QNTUNIT = "QNTUNIT"
    QUANTITY_100KG = "QUANTITY_100KG"
    QUANTITY_IN_100KG = "QUANTITY_IN_100KG"
    QUANTITY_IN_KG = "QUANTITY_IN_KG"
    QUANTITY_IN_SUPPL_UNIT = "QUANTITY_IN_SUPPL_UNIT"
    QUANTITY_IN_TONS = "QUANTITY_IN_TONS"
    QUANTITY_KG = "QUANTITY_KG"
    QUANTITY_TON = "QUANTITY_TON"
    QUANTITY_TONS = "QUANTITY_TONS"
    SCPRODQNT = "SCPRODQNT"
    SCPRODVAL = "SCPRODVAL"
    SCQNTBASE = "SCQNTBASE"
    SCQNTFLAG = "SCQNTFLAG"
    SCVALBASE = "SCVALBASE"
    SCVALFLAG = "SCVALFLAG"
    SUPPLEMENTARY_QUANTITY = "SUPPLEMENTARY_QUANTITY"
    SUPPL_QUANTITY = "SUPPL_QUANTITY"
    SUPPL_UNIT = "SUPPL_UNIT"
    SUP_QUANTITY = "SUP_QUANTITY"
    VALUE_1000EURO = "VALUE_1000EURO"
    VALUE_EUR = "VALUE_EUR"
    VALUE_IN_EUR = "VALUE_IN_EUR"
    VALUE_IN_EUROS = "VALUE_IN_EUROS"
    VALUE_IN_NAC = "VALUE_IN_NAC"
    VALUE_NAC = "VALUE_NAC"


# Tables definition for many-to-many relationships

# Tables definition
class Product(Base):
    __tablename__ = "product"
    code: Mapped_[str] = mapped_column(String_(100), primary_key=True)
    label: Mapped_[str] = mapped_column(String_(100))
    level: Mapped_[int] = mapped_column(Integer_)
    parent_id: Mapped_[str] = mapped_column(ForeignKey_("product.code"), nullable=True)

class Observation(Base):
    __tablename__ = "observation"
    id: Mapped_[int] = mapped_column(primary_key=True)
    time_period: Mapped_[str] = mapped_column(String_(100))
    obs_value: Mapped_[Optional_[float]] = mapped_column(Float_, nullable=True)
    freq: Mapped_[Freq] = mapped_column(Enum(Freq))
    flow: Mapped_[Flow] = mapped_column(Enum(Flow))
    indicators: Mapped_[Indicators] = mapped_column(Enum(Indicators))
    partner_id: Mapped_[str] = mapped_column(ForeignKey_("country.code"))
    product_id: Mapped_[str] = mapped_column(ForeignKey_("product.code"))
    reporter_id: Mapped_[str] = mapped_column(ForeignKey_("country.code"))

class Country(Base):
    __tablename__ = "country"
    code: Mapped_[str] = mapped_column(String_(100), primary_key=True)
    label: Mapped_[str] = mapped_column(String_(100))


#--- Relationships of the product table
Product.children: Mapped_[List_["Product"]] = relationship("Product", back_populates="parent", foreign_keys=[Product.parent_id])
Product.observations: Mapped_[List_["Observation"]] = relationship("Observation", back_populates="product", foreign_keys=[Observation.product_id])

Product.parent: Mapped_["Product"] = relationship("Product", back_populates="children", uselist=False, foreign_keys=[Product.parent_id], remote_side=[Product.code])

#--- Relationships of the observation table

Observation.partner: Mapped_["Country"] = relationship("Country", back_populates="observations_as_partner", uselist=False, foreign_keys=[Observation.partner_id])

Observation.product: Mapped_["Product"] = relationship("Product", back_populates="observations", uselist=False, foreign_keys=[Observation.product_id])

Observation.reporter: Mapped_["Country"] = relationship("Country", back_populates="observations_as_reporter", uselist=False, foreign_keys=[Observation.reporter_id])

#--- Relationships of the country table
Country.observations_as_partner: Mapped_[List_["Observation"]] = relationship("Observation", back_populates="partner", foreign_keys=[Observation.partner_id])
Country.observations_as_reporter: Mapped_[List_["Observation"]] = relationship("Observation", back_populates="reporter", foreign_keys=[Observation.reporter_id])

# Database connection (override the default with the DATABASE_URL environment variable)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/LuxTrade.db")  # SQLite connection
engine = create_engine(DATABASE_URL)
_configure_sqlite_engine(engine)

if __name__ == "__main__":
    # Create tables in the database only when this module is executed directly,
    # so importing it never touches the database as a side effect.
    if DATABASE_URL.startswith("sqlite"):
        os.makedirs("data", exist_ok=True)  # folder for the default SQLite database
    Base.metadata.create_all(engine, checkfirst=True)