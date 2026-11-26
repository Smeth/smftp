"""Conservative parsers for legacy Unix and Windows FTP LIST listings."""
import calendar
from datetime import datetime
import re
from smftp.i18n import tr
from smftp.models.entries import Entry

# English, French and German abbreviations seen in localized "ls -l" output.
MONTHS = {
    **{name: number for number, name in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)},
    "janv": 1, "févr": 2, "fevr": 2, "fév": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6,
    "juil": 7, "août": 8, "aout": 8, "sept": 9, "déc": 12,
    "mär": 3, "mrz": 3, "okt": 10, "dez": 12,
}
UNIX_MODE = re.compile(r"[-bcdlps][rwxsStTlL-]{9}[+@.]?")
WINDOWS = re.compile(r"^(\d{2})-(\d{2})-(\d{2,4})\s+(\d{1,2}):(\d{2})\s*(AM|PM)?\s+(<DIR>|\d+)\s+(.+)$", re.I)


def month_number(token: str) -> int | None:
    return MONTHS.get(token.lower().rstrip("."))


def unix_date(tokens, index, now):
    """Date starting at tokens[index]: "Jan 2 2025", "Jan 2 12:30", "2 janv. 12:30" or "2025-01-02 12:30"."""
    first, second = tokens[index][0], tokens[index + 1][0]
    third = tokens[index + 2][0] if index + 2 < len(tokens) else ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", first) and re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", second):
        year, month, day = map(int, first.split("-"))
        hour, minute = map(int, second.split(":")[:2])
        return datetime(year, month, day, hour, minute), index + 1
    month, day = month_number(first), second
    if month is None and month_number(second) is not None and first.isdigit():
        month, day = month_number(second), first  # Day first (some localized servers).
    if month is None or not day.isdigit() or not 1 <= int(day) <= 31:
        return None
    if re.fullmatch(r"\d{1,2}:\d{2}", third):
        hour, minute = map(int, third.split(":"))
        stamp = datetime(now.year, month, int(day), hour, minute)
        if stamp.timestamp() > now.timestamp() + 86400:
            stamp = stamp.replace(year=stamp.year - 1)
        return stamp, index + 2
    if re.fullmatch(r"\d{4}", third):
        return datetime(int(third), month, int(day)), index + 2
    return None


def to_timestamp(stamp: datetime, offset: int | None) -> float:
    """Server-local naive time -> timestamp. ``offset`` is the server clock minus UTC
    in minutes; None (unknown) reads the time as this computer's local time."""
    if offset is None:
        return stamp.timestamp()
    return calendar.timegm(stamp.timetuple()) - offset * 60


def parse_unix(line: str, offset: int | None = None) -> Entry | None:
    tokens = [(m.group(), m.end()) for m in re.finditer(r"\S+", line)]
    if not tokens or not UNIX_MODE.fullmatch(tokens[0][0]):
        return None
    permissions = tokens[0][0]
    now = datetime.now()
    # Owner and group may be missing (NAS, embedded servers): find the date instead
    # of counting fields. The size is the token just before it.
    for index in range(2, len(tokens) - 2):
        size_text = tokens[index - 1][0]
        device = size_text.endswith(",") or (index >= 3 and tokens[index - 2][0].endswith(","))
        if not (size_text.isdigit() or device):
            continue
        found = unix_date(tokens, index, now)
        if found is None:
            continue
        stamp, last = found
        if last + 1 >= len(tokens):
            continue  # No name after this date.
        name = line[tokens[last][1]:].lstrip(" ")
        kind = permissions[0]
        if kind == "l":
            name = name.split(" -> ", 1)[0]
        size = int(size_text) if size_text.isdigit() and kind == "-" else 0
        return Entry(name, kind == "d", size, to_timestamp(stamp, offset), permissions)
    return None


def parse_list_line(line: str, offset: int | None = None) -> Entry | None:
    """One LIST line; None for lines without a file ("total 12"), ValueError when not understood."""
    if not line.strip() or line.startswith("total "):
        return None
    entry = parse_unix(line, offset)
    if entry is not None:
        return entry
    match = WINDOWS.match(line)
    if match:
        month, day, year, hour, minute, ampm, size, name = match.groups()
        year = int(year)
        year = year + (2000 if year < 70 else 1900) if year < 100 else year
        hour = int(hour)
        if ampm:
            hour = hour % 12 + (12 if ampm.upper() == "PM" else 0)
        date = datetime(year, int(month), int(day), hour, int(minute))
        return Entry(name, size.upper() == "<DIR>", 0 if size.upper() == "<DIR>" else int(size), to_timestamp(date, offset))
    raise ValueError(tr("Format LIST non reconnu ; la liste ne peut pas être interprétée avec fiabilité."))


def parse_listing(lines, offset: int | None = None) -> tuple[list[Entry], int]:
    """Entries of a LIST answer and the number of lines that were not understood.

    One unusual line no longer makes the whole folder unreadable. A listing where
    nothing at all is understood still raises: its format is unknown.
    """
    entries, skipped = [], 0
    for line in lines:
        try:
            entry = parse_list_line(line, offset)
        except (ValueError, KeyError, IndexError):
            skipped += 1
            continue
        if entry is not None and entry.name not in (".", ".."):
            entries.append(entry)
    if skipped and not entries:
        raise ValueError(tr("Format LIST non reconnu ; la liste ne peut pas être interprétée avec fiabilité."))
    return entries, skipped
