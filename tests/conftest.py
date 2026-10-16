# SPDX-License-Identifier: MIT
"""A small synthetic SDMX-ML 2.1 structure message shaped like Eurostat Comext and LUSTAT."""
import pytest


def pytest_configure(config):
    """Stop at once, with instructions, when the wrong BESSER is imported (e.g. via PYTHONPATH)."""
    try:
        import besser_sdmx  # noqa: F401  (runs the guard)
    except ImportError as exc:  # WrongBesserError, or BESSER not installed
        pytest.exit(f"\n{exc}\n", returncode=2)

NS = ('xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
      'xmlns:s="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/structure" '
      'xmlns:c="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/common"')


def _codelist(cid: str, codes: list[tuple[str, str]]) -> str:
    items = "".join(f'<s:Code id="{c}"><c:Name xml:lang="en">{n}</c:Name></s:Code>' for c, n in codes)
    return f'<s:Codelist id="{cid}" agencyID="TEST" version="1.0"><c:Name xml:lang="en">{cid}</c:Name>{items}</s:Codelist>'


def _dim(did: str, pos: int, cl: str) -> str:
    return (f'<s:Dimension id="{did}" position="{pos}"><s:ConceptIdentity><Ref id="{did}"/></s:ConceptIdentity>'
            f'<s:LocalRepresentation><s:Enumeration><Ref id="{cl}" class="Codelist"/></s:Enumeration>'
            f'</s:LocalRepresentation></s:Dimension>')


def product_codes() -> list[tuple[str, str]]:
    codes = [("TOTAL", "Total")]
    for ch in ("01", "84", "85"):
        codes.append((ch, f"CHAPTER {ch}"))
        for h in range(1, 5):
            hs4 = f"{ch}{h:02d}"
            codes.append((hs4, f"Heading {hs4}"))
            for s in range(1, 3):
                hs6 = f"{hs4}{s:02d}"
                codes.append((hs6, f"Subheading {hs6}"))
                codes.append((f"{hs6}00", f"CN8 {hs6}00"))
    return codes


AREAS = [("WORLD", "All countries of the world"), ("EU27_2020", "European Union - 27 countries"),
         ("BE", "Belgium (incl. Luxembourg 'LU' -> 1998)"), ("DE", "Germany"), ("FR", "France"), ("LU", "Luxembourg"),
         ("QV", "Countries not specified")] + [(f"X{chr(65 + i)}", f"Country {i}") for i in range(55)]


@pytest.fixture
def structure_xml() -> bytes:
    codelists = "".join([
        _codelist("CL_FREQ", [("A", "Annual"), ("M", "Monthly")]),
        _codelist("CL_AREA", AREAS),
        _codelist("CL_PRODUCT", product_codes()),
        _codelist("CL_FLOW", [("1", "IMPORT"), ("2", "EXPORT")]),
        _codelist("CL_RANK", [(str(i), f"Rank {i}") for i in range(1, 61)]),
    ])
    dims = "".join([
        _dim("freq", 1, "CL_FREQ"), _dim("reporter", 2, "CL_AREA"), _dim("partner", 3, "CL_AREA"),
        _dim("product", 4, "CL_PRODUCT"), _dim("flow", 5, "CL_FLOW"), _dim("rank", 6, "CL_RANK"),
    ])
    # The attribute nests <Dimension> references: they must not be read as dimensions.
    attrs = ('<s:AttributeList id="AttributeDescriptor"><s:Attribute id="OBS_FLAG" assignmentStatus="Conditional">'
             '<s:ConceptIdentity><Ref id="OBS_FLAG"/></s:ConceptIdentity><s:AttributeRelationship>'
             '<s:Dimension><Ref id="freq"/></s:Dimension><s:Dimension><Ref id="flow"/></s:Dimension>'
             '</s:AttributeRelationship></s:Attribute></s:AttributeList>')
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<m:Structure {NS}><m:Header><m:ID>T</m:ID></m:Header><m:Structures>
<s:Dataflows><s:Dataflow id="DF_TEST" agencyID="TEST" version="1.0"><c:Name xml:lang="en">Test trade</c:Name>
<s:Structure><Ref id="DSD_TEST" agencyID="TEST" version="1.0" class="DataStructure"/></s:Structure></s:Dataflow></s:Dataflows>
<s:Codelists>{codelists}</s:Codelists>
<s:Concepts><s:ConceptScheme id="CS" agencyID="TEST" version="1.0"><c:Name xml:lang="en">Concepts</c:Name>
<s:Concept id="partner"><c:Name xml:lang="en">Partner</c:Name></s:Concept></s:ConceptScheme></s:Concepts>
<s:DataStructures><s:DataStructure id="DSD_TEST" agencyID="TEST" version="1.0"><c:Name xml:lang="en">Test DSD</c:Name>
<s:DataStructureComponents><s:DimensionList id="DimensionDescriptor">{dims}
<s:TimeDimension id="TIME_PERIOD" position="7"><s:ConceptIdentity><Ref id="TIME_PERIOD"/></s:ConceptIdentity></s:TimeDimension>
</s:DimensionList>{attrs}<s:MeasureList id="MeasureDescriptor"><s:PrimaryMeasure id="OBS_VALUE">
<s:ConceptIdentity><Ref id="OBS_VALUE"/></s:ConceptIdentity></s:PrimaryMeasure></s:MeasureList>
</s:DataStructureComponents></s:DataStructure></s:DataStructures></m:Structures></m:Structure>'''
    return xml.encode("utf-8")
