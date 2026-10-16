from datetime import datetime, date, time
from typing import Any, List, Optional, Union, Set
from enum import Enum
from pydantic import BaseModel, field_validator


############################################
# Enumerations are defined here
############################################

class Flow(Enum):
    IMPORT = "IMPORT"
    EXPORT = "EXPORT"
    RE_EXPORT = "RE_EXPORT"

class Freq(Enum):
    A = "A"
    S = "S"
    Q = "Q"
    M = "M"
    W = "W"
    D = "D"
    H = "H"
    B = "B"
    N = "N"

class Indicators(Enum):
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

############################################
# Classes are defined here
############################################
class ProductCreate(BaseModel):
    code: str  # the id
    label: str
    level: int
    observations: Optional[List[int]] = None  # 1:N Relationship
    children: Optional[List[str]] = None  # 1:N Relationship
    parent: Optional[str] = None  # N:1 Relationship (optional)


class ObservationCreate(BaseModel):
    flow: Flow
    freq: Freq
    indicators: Indicators
    obs_value: Optional[float] = None
    time_period: str
    partner: str  # N:1 Relationship (mandatory)
    product: str  # N:1 Relationship (mandatory)
    reporter: str  # N:1 Relationship (mandatory)


class CountryCreate(BaseModel):
    code: str  # the id
    label: str
    observations_as_partner: Optional[List[int]] = None  # 1:N Relationship
    observations_as_reporter: Optional[List[int]] = None  # 1:N Relationship


