"""GROBID TEI normalization tests."""

from clio_web_search.grobid import parse_tei


def test_parse_tei_preserves_references_and_contexts() -> None:
    tei = """<?xml version="1.0"?>
    <TEI xmlns="http://www.tei-c.org/ns/1.0">
      <teiHeader><fileDesc><titleStmt>
        <title>Research Paper</title><author><persName>Alice Researcher</persName></author>
      </titleStmt><publicationStmt><idno type="DOI">10.1234/paper</idno></publicationStmt>
      <sourceDesc/></fileDesc></teiHeader>
      <text><body><p>Prior work <ref type="bibr" target="#b0">[1]</ref> is useful.</p></body>
      <back><listBibl><biblStruct xml:id="b0"><analytic>
        <title level="a">Prior Work</title><author><persName>Bob Author</persName></author>
        <idno type="DOI">10.5678/prior</idno>
      </analytic></biblStruct></listBibl></back></text>
    </TEI>"""

    parsed = parse_tei(tei)

    assert parsed["metadata"] == {
        "title": "Research Paper",
        "authors": ["Alice Researcher"],
        "doi": "10.1234/paper",
    }
    assert parsed["references"][0]["doi"] == "10.5678/prior"
    assert parsed["citation_contexts"] == [
        {
            "reference_index": 1,
            "marker": "[1]",
            "text": "Prior work [1] is useful.",
        }
    ]


def test_parse_tei_reads_grobid_source_description_authors_and_dates() -> None:
    """GROBID header authors live under sourceDesc rather than titleStmt."""

    tei = """<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc>
    <titleStmt><title>Attention Is All You Need</title></titleStmt>
    <publicationStmt><publisher>Example Press</publisher>
      <date type="published" when="2017-06-12"/></publicationStmt>
    <sourceDesc><biblStruct><analytic><author><persName>
      <forename>Ashish</forename><surname>Vaswani</surname>
    </persName></author></analytic><monogr><title level="j">Proceedings</title></monogr>
    </biblStruct></sourceDesc></fileDesc></teiHeader></TEI>"""

    parsed = parse_tei(tei)

    assert parsed["metadata"] == {
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani"],
        "published_at": "2017-06-12",
        "journal": "Proceedings",
        "publisher": "Example Press",
    }
