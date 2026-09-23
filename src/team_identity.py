"""Season-scoped, conservative team identity resolution."""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass


def normalize_team(value: str) -> str:
    """Return the comparison key for a team name, never a display value."""
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"’": "'", "‘": "'", "`": "'", "“": '"', "”": '"'}))
    text = text.casefold()
    text = text.replace("'", "")
    # Apostrophes and punctuation are formatting, rather than identity, in
    # KVA's PDF team lists.  Keep letters/numbers/whitespace only.
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def _distance(left: str, right: str) -> int:
    """Small dependency-free Levenshtein implementation for short names."""
    if len(left) < len(right):
        left, right = right, left
    row = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        next_row = [i]
        for j, b in enumerate(right, 1):
            next_row.append(min(next_row[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = next_row
    return row[-1]


@dataclass(frozen=True)
class IdentityDecision:
    normalized: str
    canonical_normalized: str
    canonical_name: str
    action: str  # exact, new, learned, uncertain
    score: float | None = None
    candidate: str | None = None


class TeamIdentityResolver:
    """Resolve identities using a caller-owned SQLite connection.

    Fuzzy matching is deliberately limited: >=94 similarity, or exactly one
    edit in a name of at least nine characters, and an eight-point lead over
    the runner-up.  Short names therefore only match exact aliases.
    """
    FUZZY_THRESHOLD = 94.0
    CONFIDENCE_GAP = 8.0
    MIN_SINGLE_EDIT_LENGTH = 9
    DIAGNOSTIC_THRESHOLD = 80.0
    CONTEXT_THRESHOLD = 70.0
    CONTEXT_GAP = 8.0

    def __init__(self, db, persist: bool = True):
        self.db, self.persist = db, persist
        self._seasons: dict[str, tuple[dict[str, tuple[str, str]], dict[str, str]]] = {}
        self.last_uncertain_new = False

    def _season(self, season: str):
        if season not in self._seasons:
            aliases = {row["alias_normalized"]: (row["canonical_normalized"], row["canonical_name"])
                       for row in self.db.execute("""SELECT a.alias_normalized, a.canonical_normalized, c.canonical_name
                           FROM team_alias a JOIN team_identity c
                           ON c.season=a.season AND c.canonical_normalized=a.canonical_normalized
                           WHERE a.season=?""", (season,))}
            canonicals = {row["canonical_normalized"]: row["canonical_name"] for row in self.db.execute(
                "SELECT canonical_normalized, canonical_name FROM team_identity WHERE season=?", (season,))}
            self._seasons[season] = aliases, canonicals
        return self._seasons[season]

    def resolve(self, season: str, raw_name: str) -> IdentityDecision:
        self.last_uncertain_new = False
        normalized = normalize_team(raw_name)
        aliases, canonicals = self._season(season)
        exact = aliases.get(normalized)
        if exact:
            return IdentityDecision(normalized, exact[0], exact[1], "exact")

        candidates = []
        for key, display in canonicals.items():
            score = difflib.SequenceMatcher(None, normalized, key).ratio() * 100
            candidates.append((score, _distance(normalized, key), key, display))
        candidates.sort(reverse=True)
        if candidates:
            score, distance, key, display = candidates[0]
            second = candidates[1][0] if len(candidates) > 1 else float("-inf")
            long_one_edit = distance == 1 and min(len(normalized), len(key)) >= self.MIN_SINGLE_EDIT_LENGTH
            if (score >= self.FUZZY_THRESHOLD or long_one_edit) and score - second >= self.CONFIDENCE_GAP:
                decision = IdentityDecision(normalized, key, display, "learned", score, display)
                self._learn(season, decision)
                return decision
            if score >= self.DIAGNOSTIC_THRESHOLD:
                self._uncertain(season, normalized, key, score)
                return IdentityDecision(normalized, normalized, raw_name, "uncertain", score, display)

        decision = IdentityDecision(normalized, normalized, raw_name, "new")
        self._create(season, decision)
        return decision

    def _create(self, season, decision):
        aliases, canonicals = self._season(season)
        canonicals[decision.normalized] = decision.canonical_name
        aliases[decision.normalized] = (decision.normalized, decision.canonical_name)
        if self.persist:
            self.db.execute("INSERT OR IGNORE INTO team_identity(season, canonical_normalized, canonical_name) VALUES (?, ?, ?)",
                            (season, decision.normalized, decision.canonical_name))
            self.db.execute("INSERT OR IGNORE INTO team_alias(season, alias_normalized, canonical_normalized) VALUES (?, ?, ?)",
                            (season, decision.normalized, decision.normalized))

    def _learn(self, season, decision):
        aliases, _ = self._season(season)
        aliases[decision.normalized] = (decision.canonical_normalized, decision.canonical_name)
        if self.persist:
            self.db.execute("""INSERT INTO team_alias(season, alias_normalized, canonical_normalized) VALUES (?, ?, ?)
                ON CONFLICT(season, alias_normalized) DO UPDATE SET canonical_normalized=excluded.canonical_normalized""",
                            (season, decision.normalized, decision.canonical_normalized))

    def learn_contextual(self, season: str, raw_name: str, canonical_key: str, score: float) -> IdentityDecision:
        aliases, canonicals = self._season(season)
        decision = IdentityDecision(normalize_team(raw_name), canonical_key, canonicals[canonical_key],
                                    "contextual", score, canonicals[canonical_key])
        self._learn(season, decision)
        return decision

    def _uncertain(self, season, alias, canonical, score):
        if self.persist:
            cursor = self.db.execute("""INSERT OR IGNORE INTO team_identity_candidate
                (season, alias_normalized, candidate_normalized, score) VALUES (?, ?, ?, ?)""",
                (season, alias, canonical, score))
            self.last_uncertain_new = cursor.rowcount > 0
