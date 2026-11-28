"""
python3 -m unittest scripts/gate/tests_lane.py     (from the repository root; needs bash, not Django or PostgreSQL)

lane.sh's pg_up, run against a stub pg_isready and service: a lane that starts PostgreSQL goes on only once the
server takes connections (O622). After the container restart of 25 November, g59's pg-a and pg-b started a server
that was recovering from the unclean shutdown; `service postgresql start` returned while it was still starting,
and pg-a's DROP DATABASE and its run met "the database system is starting up" before any test ran.
"""
import os
import pathlib
import re
import subprocess
import tempfile
import textwrap
import unittest

LANE = pathlib.Path(__file__).resolve().parent / "lane.sh"


def pg_up_source():
    found = re.search(r"^pg_up\(\) \{.*?^\}$", LANE.read_text(), re.S | re.M)
    if not found:
        raise AssertionError("lane.sh has no pg_up() function")
    return found.group(0)


class PgUpTests(unittest.TestCase):
    def run_pg_up(self, answers):
        """
        pg_up against a server whose pg_isready gives these exit codes in turn and then the last one for ever.
        Returns (exit code, output, how many times pg_isready was asked, the service calls made).
        """
        with tempfile.TemporaryDirectory() as stubs:
            state = pathlib.Path(stubs)
            (state / "answers").write_text(" ".join(str(a) for a in answers))
            (state / "pg_isready").write_text(textwrap.dedent("""\
                #!/bin/bash
                read -ra a < "$STATE/answers"
                n=$(cat "$STATE/asked" 2>/dev/null || echo 0); echo $((n + 1)) > "$STATE/asked"
                [ "$n" -lt "${#a[@]}" ] && exit "${a[$n]}"; exit "${a[-1]}"
                """))
            (state / "service").write_text('#!/bin/bash\necho "$*" >> "$STATE/started"\n')
            for stub in ("pg_isready", "service"):
                os.chmod(state / stub, 0o755)
            # sleep is a no-op, so the 600 seconds' limit is counted, not waited
            script = f"sleep() {{ :; }}\n{pg_up_source()}\npg_up\n"
            env = dict(os.environ, PATH=f"{stubs}:{os.environ['PATH']}", STATE=stubs)
            done = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            asked = int((state / "asked").read_text()) if (state / "asked").exists() else 0
            calls = (state / "started").read_text().splitlines() if (state / "started").exists() else []
            return done.returncode, done.stdout + done.stderr, asked, calls

    def test_a_server_down_is_started_and_waited_for_until_it_takes_connections(self):
        # down (2), then starting up three times (1), then taking connections (0)
        code, out, asked, calls = self.run_pg_up([2, 1, 1, 1, 0])
        self.assertEqual((code, out), (0, ""))
        self.assertEqual(asked, 5)
        self.assertEqual(calls, ["postgresql start"])

    def test_a_server_still_starting_is_waited_for(self):
        # The second lane of two finds the server the first one started still recovering: pg_isready says 1.
        code, out, asked, calls = self.run_pg_up([1, 1, 0])
        self.assertEqual((code, asked), (0, 3))
        self.assertEqual(calls, ["postgresql start"])

    def test_a_server_up_is_left_alone(self):
        code, out, asked, calls = self.run_pg_up([0])
        self.assertEqual((code, asked, calls), (0, 2, []))  # once before the start, once by the wait

    def test_a_server_that_never_takes_connections_stops_the_lane_with_a_reason(self):
        code, out, asked, calls = self.run_pg_up([2, 1])
        self.assertEqual(code, 4)
        self.assertIn("PostgreSQL is not taking connections after 600 seconds", out)
        self.assertEqual(asked, 1 + 301)  # the look before the start, then the wait's at 0, 2, ... 600 seconds

    def test_nothing_else_in_the_lane_starts_postgresql(self):
        # The mig lane's PostgreSQL-only step started it the same way as pg_half: one helper, so neither can drift.
        text = LANE.read_text()
        code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        self.assertEqual(code.count("service postgresql start"), 1)
        self.assertIn("service postgresql start", pg_up_source())
        before = [line for line in text.splitlines() if "DROP DATABASE" in line]
        self.assertEqual(len(before), len(re.findall(r"^\s+pg_up$", text, re.M)),
                         "every block that drops a test database calls pg_up first")


if __name__ == "__main__":
    unittest.main()
