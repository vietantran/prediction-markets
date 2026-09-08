"""Portable report output must preserve analytical types and treat titles as text."""
import csv

import markdown
from bs4 import BeautifulSoup
from openpyxl import load_workbook

from pmresearch.research_report import markdown_table, workbook


def test_workbook_keeps_numeric_measures_blank_missing_and_identifier_text(tmp_path):
    path = tmp_path / "observations.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "cik", "total_return", "value", "volume", "note"])
        writer.writerow(["TEST", "0000012345", "-0.125", "3354200000", "1000", "=HYPERLINK(\"https://example.org\")"])
        writer.writerow(["EMPTY", "0000012346", "", "", "", "+not a measure"])
    out = tmp_path / "report.xlsx"
    workbook([path], out, ["Frozen source test"])
    sheet = load_workbook(out, data_only=False)["observations"]
    assert sheet["A2"].value == "TEST"
    assert sheet["B2"].value == "0000012345"
    assert sheet["C2"].value == -.125
    assert sheet["C2"].data_type == "n"
    assert sheet["D2"].value == 3354200000
    assert sheet["E2"].value == 1000
    assert sheet["C3"].value is None
    assert sheet["F2"].data_type != "f"
    assert sheet["F3"].data_type != "f"


def test_market_title_does_not_become_html_or_markdown_image():
    title = '<script>alert(1)</script> ![remote](https://example.org/track.png) A | B'
    table = markdown_table([{"title": title, "missing": None}], [("title", "Title"), ("missing", "Missing")])
    soup = BeautifulSoup(markdown.markdown(table, extensions=["tables"]), "html.parser")
    assert soup.find("script") is None
    assert soup.find("img") is None
    assert len(soup.select("tbody tr td")) == 2
    assert "Unavailable" in soup.get_text()
