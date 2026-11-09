"""Conservative parsers for legacy Unix and Windows FTP LIST listings."""
from datetime import datetime
import re
from smftp.models.entries import Entry


def parse_list_line(line: str) -> Entry | None:
    if not line.strip() or line.startswith("total "):
        return None
    parts = line.split(None, 8)
    if len(parts) == 9 and re.fullmatch(r"[dl-][rwxstST-]{9}[+@.]?", parts[0]):
        permissions, _, _, _, size, month, day, when, name = parts
        if permissions.startswith("l"):
            name = name.split(" -> ", 1)[0]
        months = dict(zip("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), range(1, 13)))
        now = datetime.now()
        if ":" in when:
            hour, minute = map(int, when.split(":"))
            stamp = datetime(now.year, months[month], int(day), hour, minute)
            if stamp.timestamp() > now.timestamp() + 86400:
                stamp = stamp.replace(year=stamp.year - 1)
        else:
            stamp = datetime(int(when), months[month], int(day))
        return Entry(name, permissions.startswith("d"), int(size), stamp.timestamp(), permissions)
    match = re.match(r"^(\d{2})-(\d{2})-(\d{2,4})\s+(\d{1,2}):(\d{2})(AM|PM)\s+(<DIR>|\d+)\s+(.+)$", line, re.I)
    if match:
        month, day, year, hour, minute, ampm, size, name = match.groups()
        year = int(year)
        year = year + (2000 if year < 70 else 1900) if year < 100 else year
        hour = int(hour) % 12 + (12 if ampm.upper() == "PM" else 0)
        date = datetime(year, int(month), int(day), hour, int(minute))
        return Entry(name, size.upper() == "<DIR>", 0 if size.upper() == "<DIR>" else int(size), date.timestamp())
    raise ValueError("Format LIST non reconnu ; la liste ne peut pas être interprétée avec fiabilité.")
