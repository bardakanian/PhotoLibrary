"""Turn the search box text into an SQL filter on filename and capture date.

Whole words that look like dates narrow by date: ``2023``, ``May``, ``May 2023``,
``2023-05``, ``2023-05-14``, ``14 May 2023``. Everything else must appear in the filename.
"""

import re
from dataclasses import dataclass, field

MONTHS = {
    name: number
    for number, names in enumerate([
        ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
        ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
        ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"), ("december", "dec"),
    ], start=1)
    for name in names
}
_FULL_DATE = re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$")
_YEAR_MONTH = re.compile(r"^(\d{4})[-/.](\d{1,2})$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_DAY = re.compile(r"^\d{1,2}(st|nd|rd|th)?$")


@dataclass(frozen=True)
class SearchFilter:
    name_terms: tuple[str, ...] = ()
    year: int | None = None
    month: int | None = None
    day: int | None = None
    text: str = field(default="", compare=False)

    @property
    def is_empty(self) -> bool:
        return not self.name_terms and self.year is None and self.month is None and self.day is None

    def sql(self) -> tuple[str, list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        for term in self.name_terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("file_name LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")
        for column, value in (("capture_year", self.year), ("capture_month", self.month), ("capture_day", self.day)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)
        return " AND ".join(clauses), params


def parse_query(text: str) -> SearchFilter:
    year = month = day = None
    names: list[str] = []
    loose_numbers: list[str] = []
    for token in text.replace(",", " ").split():
        lowered = token.lower()
        if match := _FULL_DATE.match(lowered):
            candidate = tuple(int(part) for part in match.groups())
            if 1 <= candidate[1] <= 12 and 1 <= candidate[2] <= 31:
                year, month, day = candidate
                continue
        elif match := _YEAR_MONTH.match(lowered):
            candidate_year, candidate_month = int(match.group(1)), int(match.group(2))
            if 1 <= candidate_month <= 12:
                year, month = candidate_year, candidate_month
                continue
        elif _YEAR.match(lowered) and year is None:
            year = int(lowered)
            continue
        elif lowered.rstrip(".") in MONTHS and month is None:
            month = MONTHS[lowered.rstrip(".")]
            continue
        elif _DAY.match(lowered):
            # Only a day if a month name is also present; otherwise it's part of a filename.
            loose_numbers.append(token)
            continue
        names.append(lowered)

    for token in loose_numbers:
        number = int(re.sub(r"\D", "", token))
        if month is not None and day is None and 1 <= number <= 31:
            day = number
        else:
            names.append(token.lower())
    return SearchFilter(tuple(names), year, month, day, text=text.strip())
