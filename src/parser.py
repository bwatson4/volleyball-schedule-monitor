from config import POOLS, TIME_FORMAT
import logging
import re
from datetime import datetime

from src.season import season_for_date


LOG = logging.getLogger("schedule_monitor.parser")


class ScheduleParser:
    def __init__(self, text, team_names=None, gyms=None, pools=POOLS):
        self.text = text
        self.lines = self._normalize_lines(text)
        self.events = []

        self.team_names = [] if team_names is None else (team_names if isinstance(team_names, list) else [team_names])
        self.team_names_norm = [self._norm_team(n) for n in self.team_names if str(n).strip()]
        
        self.gyms = gyms or []
        self.pools = pools

        self.current_date = None
        self.current_gym = None
        self.current_pool = None
        self.uid = None
        self.session_counts = {}
    
    @staticmethod
    def _norm_team(s: str) -> str:
        # lower, collapse whitespace, remove common punctuation variance
        s = s.lower()
        s = re.sub(r"\s+", " ", s).strip()
        return s
    
    def _matched_alias(self, name: str) -> str | None:
        n = self._norm_team(name)
        for alias, normalized in zip(self.team_names, self.team_names_norm):
            if n == normalized:
                return alias
        return None

    def _normalize_lines(self, text):
        return [ln.strip() for ln in text.splitlines() if ln.strip()]

    def detect_date(self, line):
        date_match = re.search(r"([A-Z][a-z]+ \d{1,2}, \d{4})", line)
        if not date_match:
            return None

        try:
            self.current_date = datetime.strptime(date_match.group(1), "%B %d, %Y").date()
            self.uid = self.current_date.strftime("%Y%m%d")
            return True
        except ValueError:
            return None

    def detect_gym(self, line):
        for gym in self.gyms:
            if line.lower().startswith(gym.lower()):
                self.current_gym = gym
                return True
        return False

    def detect_pool(self, line):
        """Recognize KVA's single-letter pool heading without a fixed ceiling."""
        match = re.match(r"^\s*([A-Z])\s+POOL(?:\s*[-–].*)?\s*$", line, re.IGNORECASE)
        if not match:
            return False
        self.current_pool = f"{match.group(1).upper()} POOL"
        return True

    def extract_block(self, start_index):
        """
        Extract all lines for the current pool block.
        Capture the gym and pool at the start so they
        are not overwritten by subsequent lines.
        """
        block = []
        j = start_index + 1

        gym_for_block = self.current_gym
        pool_for_block = self.current_pool

        while j < len(self.lines):
            nxt = self.lines[j]

            # Stop at new gym, new pool, or new date
            if self.detect_date(nxt):
                break
            if any(nxt.lower().startswith(g.lower()) for g in self.gyms):
                break
            if self.detect_pool(nxt):
                break

            block.append(nxt)
            j += 1

        return block, j, gym_for_block, pool_for_block

    def extract_time(self, block_lines):
        time_pat = re.compile(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})")
        for ln in block_lines:
            m = time_pat.search(ln)
            if m:
                return m.group(1), m.group(2)
        return None, None

    def extract_teams(self, block_lines):
        teams = []
        time_pat = re.compile(r"(\d{1,2}:\d{2})-(\d{1,2}:\d{2})")

        for ln in block_lines:
            m = re.match(r"^\s*(\d+)\s+(.*)$", ln)
            if m:
                name = m.group(2)
                name = time_pat.sub("", name)
                name = re.sub(r"\s+", " ", name).strip()
                teams.append({
                    "num": m.group(1),
                    "name": name
                })
        return teams

    def _pool_teams(self, teams):
        """Return the other teams in a shared KVA pool/session block.

        KVA pool listings do not establish head-to-head fixtures, so these are
        deliberately named ``pool_teams`` rather than opponents.  Identity is
        normalized only to remove aliases and duplicate extracted text; the
        spelling from the PDF remains the display value.  Each entry carries
        both values so the parsed event is useful independently of calendar
        rendering or the history database.
        """
        configured = set(self.team_names_norm)
        result, seen = [], set()
        for team in teams:
            normalized = self._norm_team(team["name"])
            if not normalized or normalized in configured or normalized in seen:
                continue
            seen.add(normalized)
            result.append({"name": team["name"], "normalized_name": normalized})
        return result

    def _append_matching_events(self, teams, gym, pool, start_raw, end_raw):
        """Append configured-team events for one already reconstructed session."""
        if not (start_raw and end_raw and self.current_date):
            return
        start_24 = self._pm_to_24h(start_raw)
        end_24 = self._pm_to_24h(end_raw)
        start_dt = datetime.strptime(f"{self.current_date} {start_24}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{self.current_date} {end_24}", "%Y-%m-%d %H:%M")
        pool_teams = self._pool_teams(teams)
        for team in teams:
            alias = self._matched_alias(team["name"])
            if not alias:
                continue
            LOG.info('Schedule team %r matched configured alias %r', team["name"], alias)
            # A team has one logical scheduled session per date.  Gym, pool and
            # time are mutable revision data, not identity.
            session_key = (self.current_date.isoformat(), self._norm_team(team["name"]))
            self.session_counts[session_key] = self.session_counts.get(session_key, 0) + 1
            stable_uid = f"volleyball-schedule-monitor-{self.current_date:%Y%m%d}-{self._norm_team(team['name']).replace(' ', '-')}-{self.session_counts[session_key]}"
            self.events.append({
                "uid": stable_uid,
                "source_team": team["name"],
                "date": self.current_date.isoformat(),
                "season": season_for_date(self.current_date),
                "summary": f"{team['name']} Volleyball",
                "description": f"Team: {team['name']}; Gym: {gym}, Pool: {pool}",
                "start": start_dt,
                "end": end_dt,
                "gym": gym,
                "pool": pool,
                "pool_position": team["num"],
                "pool_teams": pool_teams,
            })

    def _flattened_blocks(self):
        """Reconstruct pdfminer output where KVA columns are flattened by row.

        This layout places every gym/pool heading before a repeated numeric slot
        run, then the team names, and finally one time range per session.  It is
        accepted only when those independent counts agree.
        """
        blocks, gym = [], None
        for line in self.lines:
            for configured_gym in self.gyms:
                if line.casefold().startswith(configured_gym.casefold()):
                    gym = configured_gym
                    break
            pool_match = re.match(r"^\s*([A-Z])\s+POOL(?:\s*[-–].*)?\s*$", line, re.IGNORECASE)
            if pool_match and gym:
                blocks.append((gym, f"{pool_match.group(1).upper()} POOL"))
        if not blocks:
            return []

        # Find the longest contiguous run of slot numbers.  Matchup values such
        # as "1v5" deliberately do not qualify.
        runs, run = [], []
        for index, line in enumerate(self.lines):
            if re.fullmatch(r"\d+", line):
                run.append((index, line))
            elif run:
                runs.append(run)
                run = []
        if run:
            runs.append(run)
        if not runs:
            return []
        slots = max(runs, key=len)
        block_count = len(blocks)
        if len(slots) < block_count or len(slots) % block_count:
            return []
        teams_per_block = len(slots) // block_count
        slot_pattern = [value for _, value in slots[:teams_per_block]]
        if not slot_pattern or any(
            [value for _, value in slots[offset:offset + teams_per_block]] != slot_pattern
            for offset in range(0, len(slots), teams_per_block)
        ):
            return []

        team_start = slots[-1][0] + 1
        team_count = block_count * teams_per_block
        team_names = self.lines[team_start:team_start + team_count]
        if len(team_names) != team_count or any(
            re.fullmatch(r"\d+", name) or re.search(r"\d+v\d+", name, re.IGNORECASE)
            for name in team_names
        ):
            return []
        time_pattern = re.compile(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})")
        times = [match.groups() for line in self.lines[team_start + team_count:] for match in [time_pattern.search(line)] if match]
        if len(times) < block_count:
            return []

        return [
            (gym_name, pool, [
                {"num": slot_pattern[position], "name": team_names[block_index * teams_per_block + position]}
                for position in range(teams_per_block)
            ], *times[block_index])
            for block_index, (gym_name, pool) in enumerate(blocks)
        ]

    def _parse_flattened_layout(self):
        for line in self.lines:
            if self.detect_date(line):
                break
        if not self.current_date:
            return
        for gym, pool, teams, start_raw, end_raw in self._flattened_blocks():
            self._append_matching_events(teams, gym, pool, start_raw, end_raw)

    @staticmethod
    def _pm_to_24h(tstr: str) -> str:
        hour, minute = map(int, tstr.split(":"))
        if TIME_FORMAT.lower() == "12 hour":
            if hour != 12:
                hour += 12
        return f"{hour:02d}:{minute:02d}"

    def parse(self):
        i = 0
        while i < len(self.lines):
            line = self.lines[i]

            # Detect date lines
            if self.detect_date(line):
                # some lines contain both gym and date
                self.detect_gym(line)
                i += 1
                continue

            # Detect gym lines (update current gym)
            if self.detect_gym(line):
                i += 1
                continue

            # Detect pool lines → extract block
            if self.detect_pool(line):
                block, next_i, gym_for_block, pool_for_block = self.extract_block(i)
                start_raw, end_raw = self.extract_time(block)
                teams = self.extract_teams(block)
                self._append_matching_events(teams, gym_for_block, pool_for_block, start_raw, end_raw)

                i = next_i
                continue

            i += 1

        if not self.events:
            self._parse_flattened_layout()
        return self.events
