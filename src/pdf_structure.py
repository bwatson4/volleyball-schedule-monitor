"""Coordinate reconstruction of vertically stacked pool tables.

PDF reading order is deliberately ignored. Unsupported or ambiguous layouts
return a reason so callers can retain the legacy text parsers.
"""
import re

from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextLine

POOL = re.compile(r"^([A-Z])\s+POOL(?:\s*[-–].*)?$", re.I)
TIME = re.compile(r"(\d{1,2}:\d{2})\s*[-–]\s*(\d{1,2}:\d{2})")


def pool_tables(path, gyms):
    blocks = []
    for page in extract_pages(str(path)):
        lines = []

        def visit(item):
            if isinstance(item, LTTextLine):
                text = item.get_text().strip()
                if text:
                    lines.append((item.x0, item.y0, item.x1, item.y1, text))
            elif hasattr(item, "__iter__"):
                for child in item:
                    visit(child)

        visit(page)
        headings = [line for line in lines if POOL.fullmatch(line[4])]
        for heading in headings:
            x, y, _, _, label = heading
            # A column consists of headings aligned on the same left edge.
            column = [line for line in lines if abs(line[0] - x) <= 3]
            above = [(line, gym) for line in column for gym in gyms
                     if line[1] > y and line[4].casefold().startswith(gym.casefold())]
            if not above:
                return [], "pool heading has no gym above it"
            gym = min(above, key=lambda pair: pair[0][1] - y)[1]
            boundaries = [line[1] for line in column if line[1] < y
                          and (POOL.fullmatch(line[4])
                               or any(line[4].casefold().startswith(g.casefold()) for g in gyms)
                               or re.match(r"^(game|round)\s*\d+", line[4], re.I))]
            bottom = max(boundaries, default=page.y0)
            region = [line for line in lines if bottom < line[1] < y and line[0] > x + 3]
            times = [line for line in region if TIME.fullmatch(line[4])]
            numeric = [line for line in region if re.fullmatch(r"\d+", line[4])]
            if not numeric:
                return [], "pool has no numeric position column"
            slot_x = min(line[0] for line in numeric)
            slots = sorted([line for line in region if abs(line[0] - slot_x) <= 3
                            and re.match(r"^\d+(?:\s+\S.*)?$", line[4])],
                           key=lambda line: -line[1])
            if len(times) != 1 or not slots:
                return [], "pool requires numbered slots and exactly one time range"
            time = times[0]
            teams = []
            for slot in slots:
                inline = re.fullmatch(r"(\d+)\s+(.+)", slot[4])
                if inline:
                    teams.append({"num": inline[1], "name": inline[2]})
                    continue
                # Names share the slot baseline, between the number and time columns.
                names = [line for line in region if line[0] > slot[2]
                         and line[2] < time[0] and abs(line[1] - slot[1]) <= 2
                         and line not in slots and line not in times]
                if len(names) != 1:
                    return [], "numbered slot has missing or ambiguous team text"
                teams.append({"num": slot[4], "name": names[0][4]})
            if [int(t["num"]) for t in teams] != list(range(1, len(teams) + 1)):
                return [], "pool positions are not a consecutive sequence"
            blocks.append((gym, f"{POOL.fullmatch(label)[1].upper()} POOL", teams,
                           *TIME.fullmatch(time[4]).groups()))
    if not blocks:
        return [], "no coordinate pool headings"
    if len({block[1] for block in blocks}) != len(blocks):
        return [], "duplicate pool headings are ambiguous"
    return blocks, None
