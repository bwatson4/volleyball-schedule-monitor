from config import POOLS, TIME_FORMAT
import logging
import re
from datetime import datetime


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
        for pool in self.pools:
            if line.lower().startswith(pool.lower()):
                self.current_pool = pool
                return True
        return False

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
            if any(nxt.lower().startswith(p.lower()) for p in self.pools):
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

                if start_raw and end_raw and self.current_date:
                    start_24 = self._pm_to_24h(start_raw)
                    end_24 = self._pm_to_24h(end_raw)

                    start_dt = datetime.strptime(
                        f"{self.current_date} {start_24}", "%Y-%m-%d %H:%M"
                    )
                    end_dt = datetime.strptime(
                        f"{self.current_date} {end_24}", "%Y-%m-%d %H:%M"
                    )

                    for t in teams:
                        alias = self._matched_alias(t["name"])
                        if alias:
                            LOG.info('Schedule team %r matched configured alias %r', t["name"], alias)
                            # A date alone collides when the team has two pools that day.
                            stable_uid = f"volleyball-{self.current_date:%Y%m%d}-{gym_for_block}-{pool_for_block}-{start_24}".lower().replace(" ", "-")
                            self.events.append({
                                "uid": stable_uid,
                                "summary": f"{t['name']} Volleyball",
                                "description": f"Team: {t['name']}; Gym: {gym_for_block}, Pool: {pool_for_block}",
                                "start": start_dt,
                                "end": end_dt,
                            })

                i = next_i
                continue

            i += 1

        return self.events
