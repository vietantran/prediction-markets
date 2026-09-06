"""Auditable, configurable US topic discovery; labels are not causal claims."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any


def _pattern(term: str) -> re.Pattern[str]:
    # Word boundaries stop `Fed` matching `Federer` or `federalism`.
    words = re.split(r"[\s_-]+", term.casefold())
    body = r"[\s_-]+".join(re.escape(word) for word in words)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)", re.IGNORECASE)


class TopicClassifier:
    """Classify titles plus explicit event context using editable JSON rules.

    Bare generic terms (GDP, election, interest rates) need US context. FOMC,
    the Federal Reserve and US-specific institutions supply that context.
    Unscoped terms therefore remain outside the default universe; use an
    explicitly selected US event/series title as context to improve recall.
    """

    def __init__(self, config_path: str | Path | None = None):
        path = Path(config_path) if config_path else Path(__file__).with_name("topics.json")
        self.config = json.loads(path.read_text(encoding="utf-8-sig"))
        self._groups = {
            key: [(term, _pattern(term)) for term in self.config.get(key, [])]
            for key in ("us_context_terms", "us_state_terms", "foreign_context_terms", "sports_terms")
        }
        self._rules = {}
        for topic, definition in self.config["topics"].items():
            patterns = [(term, _pattern(term)) for term in definition.get("terms", [])]
            patterns += [(label, re.compile(pattern, re.IGNORECASE)) for label, pattern in definition.get("patterns", {}).items()]
            self._rules[topic] = patterns

    @property
    def discovery_queries(self) -> list[str]:
        return list(dict.fromkeys(query for rule in self.config["topics"].values() for query in rule.get("queries", [])))

    def queries_for(self, topics: list[str] | None = None) -> list[str]:
        selected = self.config["topics"] if topics is None else topics
        return list(dict.fromkeys(query for topic in selected for query in self.config["topics"][topic].get("queries", [])))

    def classify(self, text: str) -> dict[str, Any]:
        text = unicodedata.normalize("NFKC", str(text))
        found = {key: [label for label, pattern in rules if pattern.search(text)] for key, rules in self._groups.items()}
        direct_us = bool(found["us_context_terms"])
        state_us = bool(found["us_state_terms"]) or bool(re.search(r"\bGeorgia\b.{0,35}\b(?:senate|governor|gubernatorial|district)\b|\b(?:senate|governor|gubernatorial)\b.{0,35}\bGeorgia\b", text, re.I))
        federal_context = bool(re.search(r"\bFed\b.{0,55}\b(?:rate|cut|hike|hold|chair|interest|meeting|independence)\b", text, re.I))
        district_pattern = self.config["topics"].get("midterms_2026", {}).get("patterns", {}).get("district code")
        # An explicit US congressional district code is also geographic context.
        # Case-sensitive matching here avoids interpreting ordinary 'in-1' as IN-1.
        district_us = bool(district_pattern and re.search(district_pattern, text))
        # Party/chamber shorthand is useful, but must not import foreign elections.
        domestic_shorthand = bool(re.search(r"\b(?:midterms?|congressional|senate|congress)\b|\b(?:house majority|house control|control the house|control of the house|house election)\b", text, re.I)) and not found["foreign_context_terms"]
        scope_ok = direct_us or state_us or federal_context or domestic_shorthand or district_us
        reason = None
        if found["sports_terms"]:
            reason = "sports_context"
        elif found["foreign_context_terms"] and not (direct_us or federal_context):
            reason = "foreign_context_without_explicit_us_link"
        elif not scope_ok:
            reason = "missing_us_context"
        matches = {}
        if reason is None:
            for topic, patterns in self._rules.items():
                evidence = sorted({term for term, pattern in patterns if pattern.search(text)})
                rule = self.config["topics"][topic]
                if evidence and rule.get("requires_election_cycle"):
                    # Prioritize the headline cycle over historical dates in
                    # resolution rules. Normalizers put headline/event titles
                    # before descriptions, each on its own line.
                    lines = [line for line in text.splitlines() if line.strip()]
                    headline_years = re.findall(r"\b20\d{2}\b", lines[0]) if lines else []
                    election_line = next((line for line in lines if re.search(r"\b(?:midterms?|congressional|senate|house|governor|gubernatorial|election|generic ballot|redistricting)\b", line, re.I) and re.search(r"\b20\d{2}\b", line)), "")
                    years = headline_years or re.findall(r"\b20\d{2}\b", election_line)
                    explicit_cycle = "2026" in years or (bool(re.search(r"\bmidterms?\b", text, re.I)) and not years)
                    if not explicit_cycle:
                        evidence = []
                if evidence:
                    matches[topic] = evidence
        if not matches and reason is None:
            reason = "no_target_topic"
        sectors = sorted({sector for topic in matches for sector in self.config["topics"][topic].get("sectors", [])})
        return {
            "topics": sorted(matches), "matched_terms": matches, "sectors": sectors,
            "relevance_score": (5 if "midterms_2026" in matches else 0) + len(matches),
            "exclude_reason": reason,
        }


def classify(text: str, config_path: str | Path | None = None) -> dict[str, Any]:
    """Convenience entry point; reuse TopicClassifier when processing many rows."""
    return TopicClassifier(config_path).classify(text)
