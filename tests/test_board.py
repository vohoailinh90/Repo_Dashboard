"""index.html: what can be proven without a browser. The page's pure logic (repository names, run/step states, cron, the week plan, the
agent guess, the Office snapshot reader, the demo) runs under Node here; cron is checked against a minute-by-minute oracle written separately in
Python. The file is also checked for everything that could leak the token or run foreign code, and for being read-only: it only ever does GET."""

import datetime as dt
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest

from scripts import sync_csp

PAGE = sync_csp.PAGE
HTML = PAGE.read_text(encoding="utf-8")
NODE = shutil.which("node")
UTC = dt.timezone.utc


def _logic() -> str:
    return HTML.split("/* LOGIC:BEGIN", 1)[1].split("*/", 1)[1].split("/* LOGIC:END */", 1)[0]


def _demo() -> dict:
    return json.loads(re.search(r'id="board-demo">(.*?)</script>', HTML, re.S).group(1))


FUNCTIONS = ("parseRepo, parseRepoList, sameRepo, safeWorkflowPath, clip, normInfo, normWorkflow, normRun, normJob, normPull, runState, stepState, stepCounts, "
             "runProgress, lastDone, isFailing, orderWorkflows, sparkHistory, repoHealth, agentKind, agentActivity, parseCron, occurrences, nextOccurrence, "
             "describeCron, extractCrons, hasDispatch, lowOnQuota, ago, fmtDuration, weekPlan, countScheduled, summarize, readOffice, buildDemo, demoOffice, "
             "runStatus, workflowStatus, prStatus, codeOf, runsWorthANode, boardModel, filterBoard, chainOf, waveProgress, boxState, seconds, inferStages, flowOf, jobRows, inboxItems, readyNow, demoSteps, "
             "isStale, isStaleFailing, linkedPull, eventWord, zonedToUtc, zoneOffset, validZone, cronLabel, morePages, gatherJobs, mergeRate, jobKey")


def _node(cases: list) -> list:
    """Run the page's LOGIC block under Node for each [function, args] case and return the results."""
    if NODE is None:
        pytest.skip("node is not installed")
    program = f"""{_logic()}
const fns = {{ {FUNCTIONS} }};
const cases = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map(([name, args]) => fns[name](...args)), (key, value) => (value instanceof Set ? [...value] : value)));
"""
    out = subprocess.run([NODE, "-e", program], input=json.dumps(cases), capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _one(name, *args):
    return _node([[name, list(args)]])[0]


# ---- the page keeps its CSP hashes in step ---------------------------------------------------------------------------------

def test_the_csp_hashes_are_in_step():
    assert sync_csp.synced(HTML) == HTML, "run python3 scripts/sync_csp.py"
    assert sync_csp.main(["--check"]) == 0


def test_a_stale_board_is_reported_and_the_rewrite_fixes_it(tmp_path, monkeypatch, capsys):
    stale = tmp_path / "board.html"
    stale.write_text(HTML.replace("const API =", "const API2 =", 1), encoding="utf-8")
    monkeypatch.setattr(sync_csp, "PAGE", stale)
    assert sync_csp.main(["--check"]) == 1 and "index.html is out of step" in capsys.readouterr().out
    assert sync_csp.main([]) == 0 and sync_csp.main(["--check"]) == 0
    assert stale.read_text(encoding="utf-8") != HTML.replace("const API =", "const API2 =", 1)          # the hash was rewritten


def test_the_policy_allows_one_script_one_style_and_only_api_github_com():
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', HTML).group(1)
    assert re.fullmatch(r"default-src 'none'; script-src 'sha256-[^']+'; style-src 'sha256-[^']+'; connect-src https://api\.github\.com; base-uri 'none'; form-action 'none'", csp)
    assert len(re.findall(r"<script>", HTML)) == 1 and len(re.findall(r"<style>", HTML)) == 1


# ---- the page can neither leak the token nor run foreign code, and it only reads ---------------------------------------------

FORBIDDEN = ["innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "Function(", "document.write", "sessionStorage", "indexedDB", "document.cookie",
             "XMLHttpRequest", "WebSocket", "EventSource", "postMessage", "window.open", "sendBeacon", "importScripts", "import(", "serviceWorker", "location",
             "console.", "caches.", "srcdoc", "javascript:", "navigator.clipboard", "FileReader"]
FORBIDDEN_TAGS = ["<iframe", "<frame", "<link", "<img", "<form", "<object", "<embed", "<base", "<audio", "<video", "<source", "<svg"]


@pytest.mark.parametrize("word", FORBIDDEN)
def test_the_script_never_uses(word):
    assert word not in HTML


@pytest.mark.parametrize("tag", FORBIDDEN_TAGS)
def test_the_page_never_contains(tag):
    assert tag not in HTML.lower()


def test_no_timer_runs_a_string_and_no_attribute_runs_code_or_style():
    assert not re.search(r"set(Timeout|Interval)\(\s*[\"'`]", HTML)
    body = HTML.split("<body>", 1)[1].split("<script", 1)[0]
    assert not re.search(r"\son\w+\s*=", body, re.I) and not re.search(r"\sstyle\s*=", body, re.I)


def test_the_page_only_ever_reads():
    script = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    assert re.findall(r"fetch\(", script) == ["fetch("]                                          # one call site
    assert re.findall(r'method: "(\w+)"', script) == ["GET"]
    assert not re.search(r"\b(POST|PUT|PATCH|DELETE)\b", script)
    assert script.count("Authorization") == 1 and script.count("Bearer") == 1                 # the token goes into one header, built in one place
    assert set(re.findall(r"https?://[A-Za-z0-9.-]+", script)) == {"https://api.github.com", "https://github.com", "http://www.w3.org"}     # where it reads, where its links point, the SVG namespace
    assert script.count("http://www.w3.org") == 1 and 'const SVG_NS = "http://www.w3.org/2000/svg";' in script                                    # a name for XML, never fetched


def test_only_the_list_of_names_is_stored_never_the_token():
    script = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    assert script.count("localStorage") == 2 and script.count('"radar-board.v1"') == 1
    save = script.split("function saveStore()", 1)[1].split("\n}", 1)[0]
    assert "token" not in save and "JSON.stringify({ repos, interval: state.interval, theme: state.theme, mode: state.mode })" in save
    assert "const repos = state.demo ? (state.saved ? state.saved.repos : []) : state.repos;" in save          # the demo's made-up names never replace the real list


def test_every_link_is_built_from_a_checked_name_and_a_whole_number():
    script = re.search(r"<script>(.*?)</script>", HTML, re.S).group(1)
    hrefs = re.findall(r"(?<!function )extLink\(([^;]*?), ", script)
    assert len(hrefs) >= 6 and all(h.strip().startswith(("gh(", "workflowUrl(")) for h in hrefs)       # never a URL that came from GitHub
    assert "function workflowUrl(repo, path) { return safeWorkflowPath(path) ? gh(repo," in script          # the one helper besides gh() builds from gh() and a checked path
    assert "html_url" not in script and "url:" not in script.replace("function", "")


def test_the_demo_names_no_real_repository():
    demo = _demo()
    assert all(r["repo"].startswith("demo/") for r in demo["repos"])
    assert "vohoailinh90" not in json.dumps(demo)


# ---- repository names ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("owner/name", "owner/name"), ("  Owner/Name.js  ", "Owner/Name.js"), ("https://github.com/owner/name", "owner/name"),
    ("https://github.com/owner/name/", "owner/name"), ("http://www.github.com/owner/name.git", "owner/name"), ("github.com/owner/name/tree/main?x=1#y", "owner/name"),
    ("owner/name.git", "owner/name"), ("a/b", "a/b"),
    ("", None), ("owner", None), ("owner/", None), ("/name", None), ("a/b/c", None), ("owner/na me", None), ("owner/../x", None), ("./x", None), ("../x", None),
    ("https://evil.example/owner/name", None), ("https://github.com/owner", None), ("owner/name;rm", None), ("o/" + "n" * 101, None), (None, None), (5, None),
])
def test_parse_repo(text, expected):
    assert _one("parseRepo", text) == expected


def test_parse_repo_list_takes_json_or_lines_and_drops_duplicates_and_junk():
    ok = _one("parseRepoList", '{"repos": ["a/b", "https://github.com/c/d", "A/B", "bad", 7]}')
    assert ok == {"repos": ["a/b", "c/d"], "rejected": ["bad", "7"]}
    assert _one("parseRepoList", "a/b\nc/d, e/f;  a/b") == {"repos": ["a/b", "c/d", "e/f"], "rejected": []}
    assert _one("parseRepoList", '["a/b"]')["repos"] == ["a/b"] and _one("parseRepoList", "[oops")["repos"] == [] and _one("parseRepoList", "")["repos"] == []
    assert _one("sameRepo", "Owner/Name", "owner/name") is True


@pytest.mark.parametrize("path,expected", [(".github/workflows/ci.yml", True), (".github/workflows/a-b_c.1.yaml", True), (".github/workflows/../x.yml", False),
                                           ("dynamic/pages/pages-build-deployment", False), (".github/workflows/a/b.yml", False), (".github/workflows/x.sh", False)])
def test_only_real_workflow_files_are_read(path, expected):
    assert _one("safeWorkflowPath", path) is expected


# ---- what GitHub returns is cut down and checked before it is drawn -------------------------------------------------------------

def test_the_normalisers_keep_known_fields_cut_long_text_and_drop_what_has_no_whole_id():
    run = _one("normRun", {"id": 5, "workflow_id": 2, "run_number": 9, "status": "completed", "conclusion": "success", "event": "push", "head_branch": "x" * 500,
                           "actor": {"login": "y" * 500}, "created_at": "2026-01-01T00:00:00Z", "html_url": "https://evil.example/", "extra": "ignored"})
    assert len(run["branch"]) == 80 and len(run["actor"]) == 60 and "html_url" not in run and "extra" not in run and run["wfId"] == 2
    assert _node([["normRun", [{"id": "5"}]], ["normRun", [{"id": 1.5}]], ["normRun", [None]], ["normJob", [{"id": "x"}]], ["normPull", [{"number": "3"}]],
                  ["normWorkflow", [{"id": None}]]]) == [None] * 6
    job = _one("normJob", {"id": 1, "name": "j", "status": "completed", "steps": [{"name": "s" * 300, "number": -4}] * 100})
    assert len(job["steps"]) == 60 and len(job["steps"][0]["name"]) == 100 and job["steps"][0]["number"] == 0
    pr = _one("normPull", {"number": 4, "title": "t" * 300, "user": {"login": "dependabot[bot]", "type": "Bot"}, "head": {"ref": "claude/x"}})
    assert len(pr["title"]) == 140 and pr["userType"] == "Bot" and pr["branch"] == "claude/x"
    assert _one("normInfo", {"description": None, "stargazers_count": "many", "open_issues_count": -1})["stars"] == 0


# ---- states --------------------------------------------------------------------------------------------------------------------

STATES = [
    ("in_progress", None, "running", "info", True), ("queued", None, "queued", "info", True), ("pending", None, "queued", "info", True), ("requested", None, "queued", "info", True),
    ("waiting", None, "waiting", "warning", True),
    ("completed", "success", "success", "success", False), ("completed", "failure", "failure", "failed", False), ("completed", "timed_out", "timed_out", "failed", False),
    ("completed", "startup_failure", "startup_failure", "failed", False), ("completed", "cancelled", "cancelled", "neutral", False),
    ("completed", "skipped", "skipped", "neutral", False), ("completed", "action_required", "action_required", "warning", False),
    ("completed", "neutral", "neutral", "neutral", False), ("completed", "stale", "stale", "neutral", False),
    ("completed", None, "unknown", "neutral", False), ("completed", "something_new", "unknown", "neutral", False), ("brand_new", None, "unknown", "neutral", False), (None, None, "unknown", "neutral", False),
]


@pytest.mark.parametrize("status,conclusion,key,tone,active", STATES)
def test_every_run_state_has_a_word_a_glyph_and_the_right_tone(status, conclusion, key, tone, active):
    st = _one("runState", {"status": status, "conclusion": conclusion})
    assert (st["key"], st["tone"], st["active"]) == (key, tone, active) and st["label"] and st["glyph"]


def test_a_step_that_has_not_started_says_so_and_the_others_keep_the_run_words():
    assert _one("stepState", {"status": "queued"})["label"] == "Chưa chạy" and _one("stepState", {"status": "in_progress"})["label"] == "Đang chạy"


def test_progress_counts_steps_and_flags_an_estimate_when_a_job_has_not_started():
    done = {"status": "completed", "conclusion": "success"}
    jobs = [{"status": "completed", "steps": [done, done]}, {"status": "in_progress", "steps": [done, {"status": "in_progress"}, {"status": "queued"}]}, {"status": "queued", "steps": []}]
    p = _one("runProgress", jobs)
    assert (p["jobs"], p["jobsDone"], p["stepsTotal"], p["stepsDone"], p["value"], p["max"], p["partial"]) == (3, 1, 5, 3, 3, 5, True)
    full = _one("runProgress", jobs[:2])
    assert full["partial"] is False and _one("runProgress", [{"status": "queued", "steps": []}]) == {"jobs": 1, "jobsDone": 0, "stepsTotal": 0, "stepsDone": 0, "value": 0, "max": 1, "partial": True}
    assert _one("runProgress", None)["max"] == 0 and _one("stepCounts", None)["total"] == 0


def test_the_history_strip_is_oldest_first_and_describes_itself_in_words():
    runs = [{"id": i, "status": "completed", "conclusion": c, "number": i} for i, c in enumerate(["failure", "success", "success", "cancelled"], 1)]
    spark = _one("sparkHistory", runs, 12)
    assert [c["tone"] for c in spark["cells"]] == ["neutral", "success", "success", "failed"]
    assert spark["text"] == "4 lượt gần nhất: 1 đã hủy, 2 thành công, 1 lỗi" and _one("sparkHistory", [], 12)["text"] == "Chưa có lượt chạy"
    assert len(_one("sparkHistory", runs * 5, 12)["cells"]) == 12


def test_a_workflow_is_failing_by_its_last_finished_run_not_by_one_still_going():
    running_after_failure = {"runs": [{"status": "in_progress"}, {"status": "completed", "conclusion": "failure"}]}
    recovered = {"runs": [{"status": "completed", "conclusion": "success"}, {"status": "completed", "conclusion": "failure"}]}
    assert _node([["isFailing", [running_after_failure]], ["isFailing", [recovered]], ["isFailing", [{"runs": []}]]]) == [True, False, False]
    ordered = _one("orderWorkflows", [{"name": "b", "runs": [{"status": "completed", "conclusion": "success"}]}, {"name": "a", "runs": [{"status": "completed", "conclusion": "failure"}]},
                                      {"name": "c", "runs": [{"status": "queued"}]}, {"name": "d", "runs": []}])
    assert [w["name"] for w in ordered] == ["c", "a", "b", "d"]


def test_the_health_of_a_repo():
    ok = lambda *wfs: {"ok": True, "workflows": list(wfs)}        # noqa: E731
    good, bad = {"runs": [{"status": "completed", "conclusion": "success"}]}, {"runs": [{"status": "completed", "conclusion": "failure"}]}
    got = _node([["repoHealth", [x]] for x in (ok(good), ok(good, bad), ok({"runs": []}), ok(), {"ok": False}, None)])
    assert [g["label"] for g in got] == ["Ổn", "Có lỗi", "Chưa có lượt chạy", "Chưa có workflow", "Không đọc được", "Không đọc được"]


# ---- the guess about agents --------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,kind", [
    ("Claude Code Review", "claude"), ("claude/fix-login-redirect", "claude"), ("claude[bot]", "claude"), ("codex/reduce-batch-size", "codex"), ("copilot/fix-1", "copilot"),
    ("Copilot", "copilot"), ("copilot-swe-agent[bot]", "copilot"), ("gemini-review.yml", "gemini"), ("cursor/x", "cursor"), (".github/workflows/agent-budget.yml", "agent"),
    ("dependabot/npm/vite-7.2", "bot"), ("renovate[bot]", "bot"), ("github-actions[bot]", "bot"),
    ("feature/search", None), ("Radar scan", None), ("claudette", None), ("", None), (None, None), ("main", None),
])
def test_agent_kind_is_decided_by_name(text, kind):
    assert _one("agentKind", text) == kind


def _model(**over):
    m = {"repo": "o/r", "ok": True, "workflows": [], "pulls": [], "agentFiles": [], "active": []}
    m.update(over)
    return m


def test_agent_activity_lists_ai_runs_and_agent_or_bot_pull_requests_only():
    wf = {"id": 1, "name": "Claude Code Review", "path": ".github/workflows/claude-review.yml",
          "runs": [{"id": 9, "number": 9, "status": "queued", "branch": "claude/x", "actor": "claude[bot]", "updatedAt": "2026-10-16T10:00:00Z"},
                   {"id": 8, "number": 8, "status": "completed", "conclusion": "success", "branch": "main", "actor": "me", "updatedAt": "2026-10-15T10:00:00Z"},
                   {"id": 7, "number": 7, "status": "completed", "conclusion": "failure", "branch": "main", "actor": "me", "updatedAt": "2026-10-14T10:00:00Z"}]}
    plain = {"id": 2, "name": "CI", "path": ".github/workflows/ci.yml", "runs": [{"id": 5, "number": 5, "status": "in_progress", "branch": "main", "actor": "me"}]}
    pulls = [{"number": 1, "title": "a", "branch": "claude/a", "user": "me", "userType": "User", "updatedAt": "2026-10-16T09:00:00Z"},
             {"number": 2, "title": "b", "branch": "feat/b", "user": "me", "userType": "User"},
             {"number": 3, "title": "c", "branch": "dependabot/x", "user": "dependabot[bot]", "userType": "Bot", "updatedAt": "2026-10-10T09:00:00Z"},
             {"number": 4, "title": "d", "branch": "x", "user": "some-app", "userType": "Bot"}]
    acts = _one("agentActivity", _model(workflows=[wf, plain], pulls=pulls))
    assert [(a["type"], a["kind"], a["number"]) for a in acts] == [("run", "claude", 9), ("pr", "claude", 1), ("run", "claude", 8), ("pr", "bot", 3), ("pr", "bot", 4)]   # the run still going first; one finished run per workflow
    assert _one("agentActivity", {"ok": False}) == [] and _one("agentActivity", None) == []


# ---- cron -------------------------------------------------------------------------------------------------------------------

def _zone(name):
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        pytest.skip(f"no time zone database here for {name}")


def _py_field(text, lo, hi, names=(), base=0):
    """A second, independent reading of one cron field."""
    out = set()
    for item in text.split(","):
        rng, _, step = item.partition("/")
        step = int(step) if step else 1
        if step < 1:
            return None
        val = lambda t: int(t) if t.isdigit() else (names.index(t.upper()) + base if t.upper() in names else None)    # noqa: E731
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            x, y = rng.split("-")
            a, b = val(x), val(y)
        else:
            a = val(rng)
            b = a if "/" not in item else hi
        if a is None or b is None or a < lo or b > hi or a > b:
            return None
        out |= set(range(a, b + 1, step))
    return out


def _py_occurrences(expr, start, end, limit, zone=None):
    """Every matching minute in (start, end), found by looking at each minute in turn (vixie cron: a day must match both day fields when either starts with `*`, either one when both are restricted).
    With a zone the fields are read on that zone's wall clock; a local time that happens twice (the clock put back) counts once, and one that never happens (the clock put forward) is never found."""
    f = expr.split()
    if len(f) != 5:
        return []
    sets = [_py_field(f[0], 0, 59), _py_field(f[1], 0, 23), _py_field(f[2], 1, 31), _py_field(f[3], 1, 12, ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1),
            _py_field(f[4], 0, 7, ("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"), 0)]
    if any(s is None for s in sets):
        return []
    minute, hour, dom, month, dow = sets
    dow = {d % 7 for d in dow}                                                               # 7 is Sunday too
    dom_any, dow_any = f[2].startswith("*"), f[4].startswith("*")
    out, t = [], start + dt.timedelta(minutes=1)
    t = t.replace(second=0, microsecond=0)
    if t <= start:
        t += dt.timedelta(minutes=1)
    tz = _zone(zone) if zone else None
    while t < end and len(out) < limit:
        w = t.astimezone(tz) if tz else t
        wd = (w.weekday() + 1) % 7
        day_ok = (w.day in dom and wd in dow) if (dom_any or dow_any) else (w.day in dom or wd in dow)       # `*` or `*/2` in either: both sets; two restricted: either
        if not w.fold and w.month in month and day_ok and w.hour in hour and w.minute in minute:             # fold: the second pass through a repeated hour
            out.append(int(t.timestamp() * 1000))
        t += dt.timedelta(minutes=1)
    return out


CRONS = ["0 1 * * 3,6", "*/30 * * * *", "0 0 1 * *", "30 1 * * 0", "15 10,22 * * MON-FRI", "5 4 31 * *", "0 */6 * * *", "0 9 1,15 * 1", "59 23 31 DEC *",
         "0 0 * JAN-MAR *", "10-20/5 * * * *", "0 0 29 2 *", "0 12 * * SAT,SUN", "7 * * * *", "0 0 * * *", "*/7 3 * * 1-5", "0 8 15 * *", "20 4 * * 2/2",
         "0 2 */2 * *", "0 2 * * */2", "0 0 1 * */2", "0 2 */2 * 1", "0 0 */10 * 1-5", "0 0 * * 7", "0 0 * * 5-7", "30 6 * * 1-7", "*/90 * * * *", "*/7 * * * *"]
STARTS = [dt.datetime(2026, 10, 16, 23, 17, tzinfo=UTC), dt.datetime(2026, 12, 31, 23, 59, tzinfo=UTC), dt.datetime(2028, 2, 20, 0, 0, tzinfo=UTC)]


def test_cron_runs_match_a_minute_by_minute_oracle():
    cases, expected = [], []
    for expr in CRONS:
        for start in STARTS:
            end = start + dt.timedelta(days=45 if "29 2" in expr or "31" in expr else 20)
            cases.append(["occurrences", [expr, int(start.timestamp() * 1000), int(end.timestamp() * 1000), 25]])
            expected.append(_py_occurrences(expr, start, end, 25))
    got = _node(cases)
    for case, js, py in zip(cases, got, expected):
        assert js == py, case
    assert any(expected), "the oracle found nothing at all: the comparison would prove nothing"


def test_the_oracle_itself_finds_a_leap_day_and_the_or_rule():
    feb29 = _py_occurrences("0 0 29 2 *", dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2029, 1, 1, tzinfo=UTC), 5)
    assert [dt.datetime.fromtimestamp(t / 1000, UTC).date().isoformat() for t in feb29] == ["2028-02-29"]
    either = _py_occurrences("0 9 1,15 * 1", dt.datetime(2026, 10, 1, tzinfo=UTC), dt.datetime(2026, 11, 1, tzinfo=UTC), 50)
    days = sorted({dt.datetime.fromtimestamp(t / 1000, UTC).day for t in either})
    assert days == [1, 5, 12, 15, 19, 26]                                                    # the 1st and 15th, and every Monday
    odd = _py_occurrences("0 2 */2 * *", dt.datetime(2026, 10, 1, tzinfo=UTC), dt.datetime(2026, 10, 12, tzinfo=UTC), 50)
    assert [dt.datetime.fromtimestamp(t / 1000, UTC).day for t in odd] == [1, 3, 5, 7, 9, 11]       # a stepped `*` still ANDs: odd dates only, not every day
    both = _py_occurrences("0 0 1 * */2", dt.datetime(2026, 7, 31, tzinfo=UTC), dt.datetime(2027, 4, 1, tzinfo=UTC), 50)
    assert [dt.datetime.fromtimestamp(t / 1000, UTC).date().isoformat() for t in both] == ["2026-08-01", "2026-09-01", "2026-10-01", "2026-11-01", "2026-12-01"]
    # the 1st only when it is a Sun/Tue/Thu/Sat: Fri 1 Jan, Mon 1 Feb and Mon 1 Mar 2027 are left out (a reader that ignores the weekday set would list them)


@pytest.mark.parametrize("expr", ["0 0 * * 8", "61 * * * *", "* * *", "*/0 * * * *", "5-1 * * * *", "a b c d e", "", "0 24 * * *", "0 0 0 * *", "0 0 * 13 *", "0 0 * * * *", "1-2-3 * * * *", "0 0 * * FUNDAY"])
def test_a_cron_github_would_refuse_is_not_a_schedule(expr):
    assert _one("parseCron", expr) is None and _one("occurrences", expr, 0, 10**13, 5) == []


def test_next_occurrence_and_the_words_for_a_cron():
    start = int(dt.datetime(2026, 10, 16, 23, 17, tzinfo=UTC).timestamp() * 1000)
    assert dt.datetime.fromtimestamp(_one("nextOccurrence", "0 1 * * 3,6", start) / 1000, UTC).isoformat() == "2026-10-17T01:00:00+00:00"       # a Saturday
    assert _one("nextOccurrence", "0 0 30 2 *", start) is None
    got = _node([["describeCron", [c]] for c in ["0 1 * * 3,6", "*/30 * * * *", "0 * * * *", "15 * * * *", "30 1 * * 0", "0 3 1 * *", "0 0 * * *", "5 4 * 6 *", "nonsense"]])
    assert got == ["01:00 UTC, T4 T7", "mỗi 30 phút", "mỗi giờ, phút 00", "mỗi giờ, phút 15", "01:30 UTC, CN", "03:00 UTC, ngày 1 hằng tháng", "hằng ngày 00:00 UTC", "5 4 * 6 *", "nonsense"]


def test_only_plain_crons_get_words_and_the_rest_is_shown_as_written():
    """A description that is wrong is worse than the expression: every one of these used to be put into words, and wrongly."""
    got = _node([["describeCron", [c]] for c in ["0 2 */2 * *", "0 2 * * */2", "0 0 1 * */2", "0 2 */2 * 1", "*/90 * * * *", "*/7 * * * *", "*/15 * * * *", "*/20 * * * *", "0 0 * * 7", "0 0 1,15 * *", "0 0 * 1-6 *"]])
    assert got == ["0 2 */2 * *", "0 2 * * */2", "0 0 1 * */2", "0 2 */2 * 1", "*/90 * * * *", "*/7 * * * *", "mỗi 15 phút", "mỗi 20 phút", "00:00 UTC, CN", "00:00 UTC, ngày 1,15 hằng tháng", "0 0 * 1-6 *"]


WORKFLOW = """name: Radar scan
on:
  schedule:
    # Wednesday and Saturday
    - cron: "0 1 * * 3,6"
    - cron: '30 2 * * 1'   # trailing comment
    - cron: 15 4 * * *
    - cron: "not a cron"
  workflow_dispatch:
    inputs:
      dry_run:
        default: true
jobs:
  x:
    steps:
      - run: echo "cron: 0 0 * * *"
"""


def test_crons_are_read_from_the_schedule_block_only():
    assert _one("extractCrons", WORKFLOW) == ["0 1 * * 3,6", "30 2 * * 1", "15 4 * * *"]
    assert _one("extractCrons", "on:\n  push:\n  # schedule:\n  #   - cron: '0 0 * * *'\n") == []
    assert _one("extractCrons", "on:\n  schedule:\n  - cron: '5 4 * * *'\n  push:\n") == ["5 4 * * *"]               # a list at the key's own indent
    assert _one("extractCrons", "on:\n  schedule: [{cron: '1 2 * * *'}, {cron: \"3 4 * * *\"}]\n") == ["1 2 * * *", "3 4 * * *"]
    assert _one("extractCrons", "on:\r\n  schedule:\r\n    - cron: '9 9 * * *'\r\n") == ["9 9 * * *"] and _one("extractCrons", None) == []
    assert _node([["hasDispatch", [WORKFLOW]], ["hasDispatch", ["on:\n  # workflow_dispatch:\n  push:\n"]], ["hasDispatch", ["on: [push, workflow_dispatch]"]]]) == [True, False, True]


def test_a_cron_written_inside_a_script_is_not_a_schedule():
    doc = """name: x
on:
  schedule:
    - cron: '5 5 * * *'
  workflow_dispatch:
jobs:
  a:
    steps:
      - run: |
          cat > other.yml <<EOF
          on:
            schedule:
              - cron: '0 0 * * *'
          EOF
      - run: >-
          schedule:
            - cron: '1 1 * * *'
      - name: next
        run: echo ok
"""
    assert _one("extractCrons", doc) == ["5 5 * * *"]
    after = "on:\n  push:\njobs:\n  a:\n    steps:\n      - run: |\n          echo hi\n  # and then the schedule\n"
    assert _one("extractCrons", after + "x:\n") == []
    assert _one("extractCrons", "on:\n  schedule:\n    - cron: '9 9 * * *'\njobs:\n  a:\n    steps:\n      - run: |\n          echo\n") == ["9 9 * * *"]


def test_the_page_stops_asking_by_itself_when_the_quota_runs_low_and_resumes_after_the_reset():
    reset = NOW + 600000
    cases = [[{"limit": 5000, "remaining": 4000, "resetMs": reset}, NOW], [{"limit": 5000, "remaining": 250, "resetMs": reset}, NOW], [{"limit": 5000, "remaining": 249, "resetMs": reset}, NOW],
             [{"limit": 5000, "remaining": 0, "resetMs": reset}, reset - 1], [{"limit": 5000, "remaining": 0, "resetMs": reset}, reset], [{"limit": 5000, "remaining": 1, "resetMs": None}, NOW],
             [{"limit": 0, "remaining": 0, "resetMs": None}, NOW], [None, NOW]]
    assert _node([["lowOnQuota", c] for c in cases]) == [False, False, True, True, False, True, False, False]


def test_time_words():
    assert _node([["ago", [10]], ["ago", [100]], ["ago", [3600]], ["ago", [7 * 3600]], ["ago", [3 * 86400]], ["ago", [-3600]], ["ago", [None]],
                  ["fmtDuration", [45]], ["fmtDuration", [252]], ["fmtDuration", [3900]], ["fmtDuration", [-5]], ["fmtDuration", [None]]]) == [
        "vừa xong", "2 phút trước", "60 phút trước", "7 giờ trước", "3 ngày trước", "sau 60 phút", "", "45 giây", "4 phút 12 giây", "1 giờ 05 phút", "0 giây", ""]


# ---- the week and the totals --------------------------------------------------------------------------------------------------

NOW = int(dt.datetime(2026, 10, 16, 23, 17, tzinfo=UTC).timestamp() * 1000)


def _wf(name, crons, runs=()):
    return {"id": 1, "name": name, "path": ".github/workflows/x.yml", "crons": crons, "runs": list(runs)}


def test_a_cron_that_fires_often_is_named_once_instead_of_filling_the_week():
    plan = _one("weekPlan", [_model(repo="o/a", workflows=[_wf("Often", ["*/30 * * * *"]), _wf("Sat", ["0 1 * * 6"]), _wf("Broken", ["x"])]), {"ok": False}], NOW, 7)
    assert [i["wf"] for i in plan["items"]] == ["Sat"] and [f["wf"] for f in plan["frequent"]] == ["Often"]
    assert plan["items"][0]["ms"] == int(dt.datetime(2026, 10, 17, 1, 0, tzinfo=UTC).timestamp() * 1000)
    many = _one("weekPlan", [_model(workflows=[_wf("Six", ["0 1,2,3,4,5,6 * * *"]), _wf("Seven", ["0 1,2,3,4,5,6,7 * * *"])])], NOW, 7)
    assert len(many["items"]) == 42 and {i["wf"] for i in many["items"]} == {"Six"} and [f["wf"] for f in many["frequent"]] == ["Seven"]      # six a day is listed, seven is not


def test_the_scheduled_runs_of_the_next_day_are_counted_run_by_run():
    models = [_model(workflows=[_wf("a", ["*/30 * * * *"]), _wf("b", ["0 1 * * 6"])])]
    assert _one("countScheduled", models, NOW, NOW + 86400000) == 48 + 1
    assert _one("summarize", models, NOW)["upcoming"] == 49


def test_the_totals_count_runs_failures_and_pull_requests():
    run = lambda status, c=None: {"status": status, "conclusion": c, "id": 1, "number": 1}      # noqa: E731
    a = _model(repo="o/a", workflows=[_wf("w1", [], [run("in_progress"), run("completed", "failure")]), _wf("w2", [], [run("queued"), run("waiting"), run("completed", "success")])],
               pulls=[{"number": 1, "branch": "claude/x", "user": "me", "userType": "User", "title": "t"}, {"number": 2, "branch": "dependabot/y", "user": "dependabot[bot]", "userType": "Bot", "title": "t"}])
    b = _model(repo="o/b", workflows=[_wf("w3", [], [run("completed", "success")])])
    s = _one("summarize", [a, b, {"ok": False, "repo": "o/c"}], NOW)
    assert s == {"repos": 3, "unreadable": 1, "running": 1, "queued": 2, "failing": 1, "prs": 2, "agentPrs": 1, "upcoming": 0}


# ---- the Office snapshot is read as untrusted data -------------------------------------------------------------------------------

def _snapshot(**over):
    snap = {"version": 1, "mode": "claude", "now": 1760000056.0, "live": False, "requirement": {"id": "REQ-1", "tier": "T3", "lane": "claude", "strategy": "s"},
            "boss": {"state": "waiting", "task": "t", "waiting_for": "code-reviewer"}, "phase": {"steps": ["analysis", "verify"], "current": "verify"},
            "progress": {"pct": 75, "agents": 3, "total": 3, "running": 1, "done": 2, "blocked": 0}, "budget": [{"id": "base", "cap": 3, "granted": 3, "used": 2}],
            "agents": {"a": {"id": "a", "state": "working", "task": "x", "note": None, "progress": 0.5, "model": "sonnet", "tokens": 100.0, "stalled": False}},
            "log": [{"t": 1760000052.4, "kind": "start", "agent": "a", "text": "x"}]}
    snap.update(over)
    return snap


def test_a_real_looking_snapshot_is_read():
    view = _one("readOffice", _snapshot())
    assert view["ok"] and view["atMs"] == 1760000056000 and view["requirement"]["tier"] == "T3" and view["agents"][0]["progress"] == 0.5
    assert view["boss"]["waitingFor"] == "code-reviewer" and view["progress"]["pct"] == 75 and view["log"][0]["t"] == 1760000052400


def test_the_snapshot_the_office_demo_makes_is_accepted():
    """Needs a checkout of claude-agent-routing-template: set OFFICE_TEMPLATE to its path (skipped otherwise, as in CI)."""
    checkout = os.environ.get("OFFICE_TEMPLATE")
    template = Path(checkout) / "scripts" / "office" / "snapshot.py" if checkout else None
    if template is None or not template.exists():
        pytest.skip("set OFFICE_TEMPLATE to a claude-agent-routing-template checkout to read a snapshot its own code made")
    out = subprocess.run(["python3", str(template), "--demo", "claude-t3", "--at", "0.5"], capture_output=True, text=True, cwd=checkout)
    assert out.returncode == 0, out.stderr
    view = _one("readOffice", json.loads(out.stdout))
    assert view["ok"] and view["agents"] and view["requirement"]["tier"] == "T3"


@pytest.mark.parametrize("snap,words", [(None, "không phải snapshot"), ([], "không phải snapshot"), ("x", "không phải snapshot"), (_snapshot(version=2), "version 1"),
                                        (_snapshot(mode="team"), "mode=claude"), ({"version": 1}, "mode=claude")])
def test_anything_else_is_refused_with_a_reason(snap, words):
    got = _one("readOffice", snap)
    assert got["ok"] is False and words in got["error"]


def test_hostile_or_oversized_snapshot_fields_stay_plain_bounded_data():
    evil = "<img src=x onerror=alert(1)>" * 50
    agents = {f"k{i}": {"id": evil, "state": "__proto__", "task": evil, "progress": 9, "tokens": "lots", "stalled": 1} for i in range(100)}
    view = _one("readOffice", _snapshot(agents=agents, log=[{"t": "x", "kind": evil, "agent": evil, "text": evil}] * 50, budget=[{"id": evil, "cap": -1}] * 30,
                                        requirement={"id": evil, "tier": evil}, progress={"pct": 900}, boss={"state": "constructor", "task": evil}, now="soon"))
    assert view["ok"] and len(view["agents"]) == 40 and len(view["log"]) == 8 and len(view["budget"]) == 8
    a = view["agents"][0]
    assert len(a["id"]) <= 60 and len(a["task"]) <= 160 and a["state"] == "idle" and a["progress"] == 1 and a["tokens"] is None and a["stalled"] is True
    assert view["progress"]["pct"] == 100 and view["boss"]["state"] == "idle" and view["atMs"] is None and view["log"][0]["t"] is None
    assert view["budget"][0]["cap"] == 0 and len(view["requirement"]["tier"]) <= 4


# ---- the demo is built by the same code as live data -------------------------------------------------------------------------

def test_the_demo_builds_the_models_the_page_draws():
    demo = _demo()
    models = _one("buildDemo", demo, NOW)
    assert [m["repo"] for m in models] == ["demo/web-app", "demo/ml-pipeline", "demo/docs-site", "demo/private-repo"]
    assert models[3]["ok"] is False and all(m["ok"] for m in models[:3])
    web = models[0]
    assert [a["wf"]["name"] for a in web["active"]] == ["CI", "Claude Code Review", "Deploy production"]
    for m in models[:3]:
        for a in m["active"]:
            assert str(a["run"]["id"]) in m["jobs"]                                                # every task has its job list (possibly empty: queued); JSON keys are text
        for wf in m["workflows"]:
            assert wf["runs"] and [r["number"] for r in wf["runs"]] == sorted((r["number"] for r in wf["runs"]), reverse=True)      # newest first, as GitHub lists them
            assert len({r["id"] for r in wf["runs"]}) == len(wf["runs"])                           # run ids are unique
            if wf["runs"][0]["status"] == "completed":
                assert str(wf["runs"][0]["id"]) in m["jobs"]                                       # a row can be opened to see its steps
    ci = web["workflows"][0]
    assert ci["runs"][0]["number"] == 212 and ci["runs"][1]["number"] == 211 and ci["runs"][2]["number"] == 210
    steps = web["jobs"][str(ci["runs"][0]["id"])][2]["steps"]                                  # lint, build, then test
    assert [s["status"] for s in steps] == ["completed", "completed", "in_progress", "queued", "queued"]
    totals = _one("summarize", models, NOW)
    assert totals["running"] == 2 and totals["queued"] == 2 and totals["failing"] == 1 and totals["unreadable"] == 1 and totals["prs"] == 4 and totals["agentPrs"] == 2


def test_the_demo_snapshot_goes_through_the_same_reader():
    view = _one("demoOffice", _demo(), NOW)
    assert view["ok"] and view["atMs"] == NOW and {a["id"] for a in view["agents"]} == {"architect", "code-reviewer"} and view["log"][0]["t"] == NOW - 20000


# ---- the board: one graph of what triggers what ------------------------------------------------------------------------------

def _board(models=None):
    models = _one("buildDemo", _demo(), NOW) if models is None else models
    return _one("boardModel", models, NOW)


@pytest.mark.parametrize("status,conclusion,expected", [
    ("in_progress", None, "running"), ("queued", None, "under"), ("waiting", None, "needs"), ("completed", "failure", "needs"), ("completed", "timed_out", "needs"),
    ("completed", "startup_failure", "needs"), ("completed", "action_required", "needs"), ("completed", "success", "done"), ("completed", "skipped", "done"),
    ("completed", "neutral", "done"), ("completed", "cancelled", "blocked"), ("completed", "stale", "blocked"), ("completed", "brand_new", "blocked"), (None, None, "blocked")])
def test_a_run_is_in_one_of_the_seven_statuses(status, conclusion, expected):
    assert _one("runStatus", {"status": status, "conclusion": conclusion}) == expected


def test_a_workflow_takes_the_status_that_needs_the_person_most():
    done = lambda c: {"status": "completed", "conclusion": c, "id": 1}      # noqa: E731
    cases = [{"runs": []}, {"state": "disabled_manually", "runs": [done("success")]}, {"runs": [done("success")]}, {"runs": [done("failure"), done("success")]},
             {"runs": [{"status": "in_progress"}, {"status": "waiting"}, done("success")]}, {"runs": [{"status": "queued"}, done("failure")]}, {"runs": [{"status": "in_progress"}, done("failure")]}]
    assert _node([["workflowStatus", [c]] for c in cases]) == ["ready", "blocked", "done", "needs", "needs", "under", "running"]
    assert _node([["prStatus", [{"draft": True}]], ["prStatus", [{"draft": False}]]]) == ["under", "review"]


@pytest.mark.parametrize("path,code", [(".github/workflows/ci.yml", "CI"), (".github/workflows/radar-scan.yaml", "RADAR-SCAN"), ("dynamic/pages", "WF"), (None, "WF"), (".github/workflows/" + "x" * 40 + ".yml", "X" * 13 + "…")])
def test_the_short_code_of_a_workflow_is_its_file_name(path, code):
    assert _one("codeOf", path) == code


def test_the_demo_makes_a_board_of_four_waves_with_the_counts_the_reference_shows():
    board = _board()
    nodes = board["nodes"]
    assert [sum(1 for n in nodes if n["wave"] == w) for w in range(4)] == [12, 9, 13, 4]
    assert len({n["id"] for n in nodes}) == len(nodes)                                           # ids are unique
    counts = _one("filterBoard", board, {})["counts"]
    assert counts == {"needs": 3, "running": 4, "review": 3, "under": 3, "ready": 12, "blocked": 0, "done": 13}
    waves = [n["wave"] for n in nodes]
    assert waves == sorted(waves)                                                               # laid out wave by wave
    needs_first = [n["status"] for n in nodes if n["wave"] == 2]
    assert needs_first == sorted(needs_first, key=["needs", "running", "review", "under", "ready", "blocked", "done"].index)    # the ones to look at first


def test_edges_exist_only_where_the_data_has_a_relation_and_the_path_to_a_need_is_critical():
    board = _board()
    edges = {(e["from"], e["to"]): e["crit"] for e in board["edges"]}
    assert ("w|demo/web-app|13", "r|demo/web-app|13058") in edges                                  # workflow -> its run
    assert ("r|demo/web-app|13058", "p|demo/web-app|41") in edges                                  # run -> the pull request of the same branch
    assert ("r|demo/web-app|11212", "p|demo/web-app|39") in edges and ("r|demo/ml-pipeline|23003", "p|demo/ml-pipeline|12") in edges
    assert not [e for e in edges if e[1] == "p|demo/web-app|40"]                                  # a dependabot branch no run has touched: no line
    assert ("t|demo/web-app|manual", "w|demo/web-app|14") in edges and ("t|demo/ml-pipeline|cron|0 2 * * 1,4", "w|demo/ml-pipeline|21") in edges
    assert edges[("w|demo/web-app|14", "r|demo/web-app|14009")] is True and edges[("t|demo/web-app|manual", "w|demo/web-app|14")] is True        # upstream of the approval that waits
    assert edges[("w|demo/web-app|11", "r|demo/web-app|11212")] is False and edges[("w|demo/web-app|13", "r|demo/web-app|13058")] is False
    ends = {e["to"] for e in board["edges"]}
    assert all(n["id"] in ends for n in board["nodes"] if n["wave"] in (1, 2))                     # every workflow has a trigger, every run has its workflow
    assert {n["id"] for n in board["nodes"] if n["wave"] == 3 and n["id"] not in ends} == {"p|demo/web-app|40"}      # the pull request nothing ran for stands alone
    assert {n["id"]: n["crit"] for n in board["nodes"]}["r|demo/ml-pipeline|21033"] is True      # the failed run


def test_filters_narrow_the_board_and_the_counts_follow_all_but_the_status():
    board = _board()
    everything = _one("filterBoard", board, {})
    assert len(everything["nodes"]) == 38 and everything["total"] == 38
    only_web = _one("filterBoard", board, {"repo": "DEMO/web-app"})
    assert {n["repo"] for n in only_web["nodes"]} == {"demo/web-app"} and sum(only_web["counts"].values()) == len(only_web["nodes"])
    needs = _one("filterBoard", board, {"status": "needs"})
    assert {n["status"] for n in needs["nodes"]} == {"needs"} and needs["counts"]["done"] == 13                         # the counts still show the other statuses
    nightly = _one("filterBoard", board, {"q": " nightly "})["nodes"]
    assert sorted(n["kind"] for n in nightly) == ["run", "workflow"] and {n["title"] for n in nightly} == {"Nightly E2E"}
    q = _one("filterBoard", board, {"q": "claude"})
    assert {n["id"] for n in q["nodes"]} >= {"w|demo/web-app|13", "r|demo/web-app|13058", "p|demo/web-app|41"}
    hidden = _one("filterBoard", board, {"rest": "hide"})
    assert not [n for n in hidden["nodes"] if n["status"] in ("done", "blocked")] and len(hidden["nodes"]) == 38 - 13
    shown = _one("filterBoard", board, {"rest": "hide", "status": "done"})
    assert len(shown["nodes"]) == 13                                                                            # asking for "done" outranks hiding it
    assert all(e["from"] in {n["id"] for n in hidden["nodes"]} and e["to"] in {n["id"] for n in hidden["nodes"]} for e in hidden["edges"])


def test_the_chain_of_a_card_is_everything_upstream_and_downstream_of_it_and_nothing_beside_it():
    board = _board()
    chain = _one("chainOf", board["edges"], "r|demo/web-app|13058")
    assert set(chain["nodes"]) == {"r|demo/web-app|13058", "w|demo/web-app|13", "t|demo/web-app|event|pull_request", "p|demo/web-app|41"}
    assert set(chain["edges"]) == {"w|demo/web-app|13>r|demo/web-app|13058", "t|demo/web-app|event|pull_request>w|demo/web-app|13", "r|demo/web-app|13058>p|demo/web-app|41"}
    assert set(_one("chainOf", board["edges"], "nope")["nodes"]) == {"nope"} and _one("chainOf", [], "x")["edges"] == []
    wave = _one("waveProgress", board["nodes"], 2)
    assert wave == {"total": 13, "done": 8} and _one("waveProgress", board["nodes"], 0) == {"total": 12, "done": 12} and _one("waveProgress", [], 1) == {"total": 0, "done": 0}


def test_a_board_is_built_from_what_is_there_and_ignores_what_is_not():
    assert _board([]) == {"nodes": [], "edges": []} and _board([{"ok": False, "repo": "o/x"}, None]) == {"nodes": [], "edges": []}
    off = _model(repo="o/r", info={"defaultBranch": "main"}, workflows=[{"id": 1, "name": "Off", "path": ".github/workflows/off.yml", "state": "disabled_manually", "crons": ["0 1 * * *"], "dispatch": True, "runs": []}])
    nodes = _board([off])["nodes"]
    assert {n["kind"]: n["status"] for n in nodes} == {"cron": "blocked", "manual": "blocked", "workflow": "blocked"}                # a switched-off workflow arms nothing
    main = _model(repo="o/r", info={"defaultBranch": "main"}, pulls=[{"number": 1, "title": "t", "branch": "main", "user": "u", "userType": "User", "draft": False}],
                  workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active", "crons": [], "dispatch": False,
                              "runs": [{"id": 5, "number": 1, "status": "completed", "conclusion": "success", "event": "push", "branch": "main", "actor": "u", "startedAt": None, "updatedAt": None}]}])
    assert _board([main])["edges"] and not [e for e in _board([main])["edges"] if e["to"].startswith("p|")]                          # a run on the default branch is not "the" run of a PR to it


# ---- a run as a pipeline ------------------------------------------------------------------------------------------------------

def _job(name, start, end, status="completed", conclusion="success", steps=None):
    iso = lambda s: None if s is None else dt.datetime.fromtimestamp(NOW / 1000 + s, UTC).isoformat()          # noqa: E731
    return {"id": ["build", "lint", "test", "deploy", "late", "a", "b", "c", "d"].index(name) + 1, "name": name, "status": status, "conclusion": conclusion if status == "completed" else None, "startedAt": iso(start), "completedAt": iso(end), "steps": steps or []}


def test_jobs_that_run_together_are_one_stage_and_the_next_starts_when_they_have_all_ended():
    jobs = [_job("deploy", None, None, "queued"), _job("test", 100, None, "in_progress"), _job("build", 0, 90), _job("lint", 0, 40), _job("late", 150, None, "in_progress")]
    assert [[j["name"] for j in stage] for stage in _one("inferStages", jobs)] == [["build", "lint"], ["test", "late"], ["deploy"]]       # sorted by time; unstarted ones wait at the end
    chain = [_job("a", 0, 10), _job("b", 10, 20), _job("c", 25, 30)]
    assert [[j["name"] for j in stage] for stage in _one("inferStages", chain)] == [["a"], ["b"], ["c"]]                                  # starting at the moment the last ended is a new stage
    overlap = [_job("a", 0, 50), _job("b", 10, 20), _job("c", 30, 40), _job("d", 49, 60)]
    assert [[j["name"] for j in stage] for stage in _one("inferStages", overlap)] == [["a", "b", "c", "d"]]                                # all inside what ran
    assert _node([["inferStages", [None]], ["inferStages", [[]]]]) == [[], []]


def test_the_demo_pipeline_reads_like_the_reference_start_then_stages_then_the_pull_request():
    models = _one("buildDemo", _demo(), NOW)
    web = models[0]
    run = next(a["run"] for a in web["active"] if a["run"]["number"] == 212)
    pr = next(p for p in web["pulls"] if p["number"] == 39)
    flow = _one("flowOf", run, web["jobs"][str(run["id"])], pr, NOW)
    titles = [[b["title"] for b in col["boxes"]] for col in flow["columns"]]
    assert titles == [["Bắt đầu"], ["lint", "build"], ["test"], ["deploy-preview"], ["PR #39"]]
    assert [col.get("group", False) for col in flow["columns"]] == [False, True, False, False, False]
    states = [[b["state"] for b in col["boxes"]] for col in flow["columns"]]
    assert states == [["done"], ["done", "done"], ["running"], ["queued"], ["queued"]]
    assert flow["columns"][0]["boxes"][0]["lines"] == ["Pull request", "feat/settings", "demo-dev"]
    assert flow["columns"][2]["boxes"][0]["lines"][0] == "2/5 bước" and flow["columns"][1]["boxes"][1]["lines"] == ["5/5 bước", "1 phút 35 giây"]
    assert flow["totals"]["jobs"] == 4 and flow["totals"]["steps"] == 14 and flow["attempt"] == 0


def test_a_job_waiting_for_the_person_puts_the_person_in_the_flow_and_a_retry_is_counted():
    models = _one("buildDemo", _demo(), NOW)
    web, ml = models[0], models[1]
    deploy = next(a["run"] for a in web["active"] if a["run"]["number"] == 9)
    flow = _one("flowOf", deploy, web["jobs"][str(deploy["id"])], None, NOW)
    assert [[b["title"] for b in col["boxes"]] for col in flow["columns"]] == [["Bắt đầu"], ["Bạn"], ["approval gate"], ["deploy"], ["Kết quả"]]
    assert flow["columns"][1]["boxes"][0]["state"] == "waiting" and flow["columns"][2]["boxes"][0]["lines"][-1] == "chờ bạn duyệt"
    train = next(a["run"] for a in ml["active"] if a["run"]["number"] == 34)
    assert _one("flowOf", train, ml["jobs"][str(train["id"])], None, NOW)["attempt"] == 2


def test_a_finished_run_ends_in_its_result_and_a_run_with_no_jobs_still_has_a_start_and_an_end():
    base = {"id": 1, "number": 3, "status": "completed", "conclusion": "failure", "event": "push", "branch": "x", "actor": "me", "startedAt": "2026-10-16T10:00:00Z", "updatedAt": "2026-10-16T10:05:00Z", "attempt": 1}
    flow = _one("flowOf", base, [_job("a", 0, 10, "completed", "failure")], None, NOW)
    assert flow["columns"][-1]["boxes"][0] == {"title": "Kết quả", "state": "failed", "glyph": "✕", "lines": ["Lỗi"]} and flow["totals"]["seconds"] == 300
    for jobs in (None, []):
        bare = _one("flowOf", {**base, "status": "queued", "conclusion": None}, jobs, None, NOW)
        assert [col["boxes"][0]["title"] for col in bare["columns"]] == ["Bắt đầu", "Kết quả"] and bare["columns"][-1]["boxes"][0]["state"] == "queued"


def test_box_states_and_step_rows():
    assert _node([["boxState", [{"status": s, "conclusion": c}]] for s, c in [("in_progress", None), ("queued", None), ("waiting", None), ("completed", "success"), ("completed", "failure"),
                                                                              ("completed", "timed_out"), ("completed", "cancelled"), ("completed", "skipped"), ("completed", "action_required"), ("x", None)]]) == [
        "running", "queued", "waiting", "done", "failed", "failed", "cancelled", "skipped", "waiting", "skipped"]
    steps = _one("demoSteps", "ok:A:3|run:B|wait:C", NOW)
    assert [s["status"] for s in steps] == ["completed", "in_progress", "queued"] and steps[0]["completedAt"] == steps[1]["startedAt"] and steps[2]["startedAt"] is None
    rows = _one("jobRows", {"steps": steps})
    assert [(r["name"], r["state"], r["seconds"]) for r in rows] == [("A", "done", 3), ("B", "running", None), ("C", "queued", None)] and _one("jobRows", {"steps": None}) == []
    assert _node([["seconds", ["2026-10-16T10:00:00Z", "2026-10-16T10:01:30Z"]], ["seconds", [None, "x"]], ["seconds", ["2026-10-16T10:01:30Z", "2026-10-16T10:00:00Z"]]]) == [90, None, None]


# ---- what needs the person, and what is ready ----------------------------------------------------------------------------------

def test_the_inbox_lists_what_needs_the_person_newest_first_and_nothing_else():
    items = _one("inboxItems", _one("buildDemo", _demo(), NOW))
    got = [(i["type"], i["title"]) for i in items]
    assert ("approval", "Deploy production #9") in got and ("failed", "Train model #33") in got and ("error", "Không đọc được repo") in got
    assert ("review", "#41 Sửa chuyển hướng sau khi đăng nhập") in got and ("review", "#12 Giảm batch size cho GPU nhỏ") in got
    assert not [i for i in items if "#40" in i["title"] or "#39" in i["title"]]                       # dependabot and a draft are not waiting for anyone
    assert len(items) == 5 and items[-1]["when"] is None                                              # an unreadable repo has no time: last
    assert [i["when"] for i in items[:-1]] == sorted((i["when"] for i in items[:-1]), reverse=True)
    off = _model(workflows=[{"id": 1, "name": "Old", "path": "x", "state": "disabled_inactivity", "runs": [{"id": 1, "status": "completed", "conclusion": "failure", "number": 1}]}])
    assert _one("inboxItems", [off, None]) == []


def test_ready_now_is_what_can_be_started_by_hand_and_is_not_already_going():
    ready = _one("readyNow", _one("buildDemo", _demo(), NOW))
    assert [(r["repo"], r["title"]) for r in ready] == [("demo/docs-site", "Deploy docs"), ("demo/web-app", "Nightly E2E")]       # CI, Train model and Deploy production are already going
    assert all(r["code"] and r["path"] for r in ready) and _one("readyNow", [None, {"ok": False}]) == []


# ---- forks, old failures and queued jobs: what an independent review found ---------------------------------------------------------

def test_a_run_belongs_to_a_pull_request_only_when_both_come_from_the_same_repository():
    pr = lambda head, branch="fix": {"number": 7, "title": "t", "branch": branch, "headRepo": head, "user": "u", "userType": "User", "draft": False}      # noqa: E731
    run = lambda branch="fix", head="o/r": {"id": 1, "branch": branch, "headRepo": head}                                                                    # noqa: E731
    m = lambda *pulls: _model(repo="o/r", info={"defaultBranch": "main"}, pulls=list(pulls))                                                                # noqa: E731
    got = _node([["linkedPull", [m(pr("o/r")), run()]],                         # same repository, same branch: linked
                 ["linkedPull", [m(pr("someone/r")), run()]],                   # a fork's branch that happens to be called "fix" is another thing
                 ["linkedPull", [m(pr("someone/r")), run(head="someone/r")]],   # a run of the fork itself belongs to the fork's pull request
                 ["linkedPull", [m(pr("O/R")), run()]],                         # names are not case sensitive on GitHub
                 ["linkedPull", [m(pr("o/r", "main")), run("main")]],           # the default branch is nobody's pull request
                 ["linkedPull", [m(pr(None)), run()]],                          # a pull request that does not say where it comes from is not guessed
                 ["linkedPull", [m(pr("o/r")), {"id": 2, "branch": "fix"}]],    # a run that does not say is taken as the repository's own
                 ["linkedPull", [m(pr("o/r")), None]], ["linkedPull", [m(pr("o/r")), run("")]]])
    assert [g and g["number"] for g in got] == [7, None, 7, 7, None, None, 7, None, None]
    board = _board([_model(repo="o/r", info={"defaultBranch": "main"}, pulls=[pr("someone/r")],
                           workflows=[{"id": 1, "name": "CI", "path": ".github/workflows/ci.yml", "state": "active", "crons": [], "dispatch": False,
                                       "runs": [{"id": 5, "number": 1, "status": "completed", "conclusion": "success", "event": "push", "branch": "fix", "headRepo": "o/r", "actor": "u", "startedAt": None, "updatedAt": None}]}])])
    assert [n for n in board["nodes"] if n["kind"] == "pr"] and not [e for e in board["edges"] if e["to"].startswith("p|")]                               # no line to a pull request of a fork


def test_the_head_repository_of_a_run_and_of_a_pull_request_is_read_from_the_answer():
    run = {"id": 1, "workflow_id": 2, "head_branch": "fix", "head_repository": {"full_name": "someone/r"}}
    pull = {"number": 3, "title": "t", "head": {"ref": "fix", "repo": {"full_name": "someone/r"}}, "user": {"login": "u", "type": "User"}}
    got = _node([["normRun", [run]], ["normRun", [{"id": 1, "head_repository": None}]], ["normPull", [pull]], ["normPull", [{**pull, "head": {"ref": "fix", "repo": None}}]]])
    assert got[0]["headRepo"] == "someone/r" and got[1]["headRepo"] == "" and got[2]["headRepo"] == "someone/r" and got[3]["headRepo"] == ""


def test_the_words_for_an_event_are_looked_up_not_inherited():
    assert _node([["eventWord", [e]] for e in ("push", "schedule", "workflow_dispatch", "constructor", "toString", "__proto__", "something_new")]) == [
        "Push", "Lịch (cron)", "Chạy tay", "constructor", "toString", "__proto__", "something_new"]


def test_a_failure_nothing_has_followed_for_two_weeks_is_old_news_and_not_a_call_for_the_person():
    day = 86400000
    at = lambda days_ago: dt.datetime.fromtimestamp((NOW - days_ago * day) / 1000, UTC).isoformat()          # noqa: E731
    failed = lambda days_ago: {"id": 3, "number": 3, "status": "completed", "conclusion": "failure", "updatedAt": at(days_ago), "startedAt": at(days_ago)}        # noqa: E731
    fresh, old, edge, over = ({"runs": [failed(d)]} for d in (1, 15, 14, 14.01))
    assert _node([["isFailing", [w, NOW]] for w in (fresh, old, edge, over)]) == [True, False, True, False]                        # exactly two weeks is still fresh
    assert _node([["isStaleFailing", [w, NOW]] for w in (fresh, old, over)]) == [False, True, True]
    assert _node([["isFailing", [old]], ["isStaleFailing", [old]]]) == [True, False]                                              # no clock: taken as fresh
    assert _node([["runStatus", [failed(1), NOW]], ["runStatus", [failed(15), NOW]], ["workflowStatus", [old, NOW]], ["workflowStatus", [fresh, NOW]]]) == ["needs", "blocked", "blocked", "needs"]
    health = lambda *wfs: {"ok": True, "workflows": list(wfs)}                                                                     # noqa: E731
    assert [h["label"] for h in _node([["repoHealth", [health(fresh, old), NOW]], ["repoHealth", [health(old), NOW]], ["repoHealth", [health(old, {"runs": [{"status": "completed", "conclusion": "success", "updatedAt": at(1)}]}), NOW]]])] == ["Có lỗi", "Lỗi cũ", "Lỗi cũ"]
    ordered = _one("orderWorkflows", [{"name": "b", "runs": [{"status": "completed", "conclusion": "success"}]}, {"name": "a", "runs": [failed(15)]}], NOW)
    assert [w["name"] for w in ordered] == ["a", "b"] and [w["name"] for w in _one("orderWorkflows", [{"name": "b", "runs": [{"status": "completed", "conclusion": "success"}]}, {"name": "a", "runs": [failed(1)]}], NOW)] == ["a", "b"]
    got = _one("inboxItems", [_model(workflows=[{"id": 1, "name": "Old", "path": "x", "state": "active", "runs": [failed(15)]}, {"id": 2, "name": "New", "path": "y", "state": "active", "runs": [failed(2)]}])], NOW)
    assert [i["title"] for i in got] == ["New #3"]                                                                                   # the old one is not in the inbox
    summary = _one("summarize", [_model(workflows=[{"id": 1, "name": "Old", "path": "x", "state": "active", "runs": [failed(15)]}])], NOW)
    assert not summary["failing"]


def test_a_job_that_is_still_queued_never_opens_a_stage_even_when_github_gave_it_a_start_time():
    queued = _job("deploy", 5, None, "queued")              # GitHub fills started_at of a queued job with the time it was created
    jobs = [queued, _job("build", 0, 90), _job("test", 100, None, "in_progress")]
    assert [[j["name"] for j in stage] for stage in _one("inferStages", jobs)] == [["build"], ["test"], ["deploy"]]
    assert [[j["name"] for j in stage] for stage in _one("inferStages", [{**queued, "status": "pending"}, _job("build", 0, 10)])] == [["build"], ["deploy"]]
    assert [[j["name"] for j in stage] for stage in _one("inferStages", [{**queued, "status": "requested"}])] == [["deploy"]]


# ---- schedules that name a time zone ------------------------------------------------------------------------------------------------

ZONED = [("America/New_York", ["30 5 * * 1-5", "30 2 * * *", "30 1 * * *", "0 0 * * *", "15 */6 * * *", "0 9 8 * *", "0 3 * * 0"]),
         ("Europe/London", ["30 1 * * *", "0 1 * * *", "30 0 * * *", "0 9 * * 1-5"]),
         ("Australia/Sydney", ["30 2 * * *", "0 3 * * *", "0 9 * * 1"]),
         ("Australia/Lord_Howe", ["15 2 * * *", "45 1 * * *", "0 9 * * *"]),                 # a clock change of half an hour
         ("Asia/Kolkata", ["30 9 * * *", "0 0 * * 0"]), ("Asia/Tokyo", ["0 9 * * *", "59 23 * * *"]), ("America/Sao_Paulo", ["0 0 * * *"]), ("UTC", ["0 9 * * *"])]
ZONED_WINDOWS = [(dt.datetime(2026, 3, 1, tzinfo=UTC), dt.datetime(2026, 4, 12, tzinfo=UTC)),           # US, EU and Australian clock changes of the northern spring
                 (dt.datetime(2026, 10, 1, tzinfo=UTC), dt.datetime(2026, 11, 10, tzinfo=UTC))]         # and of the autumn


def test_a_cron_in_a_time_zone_matches_a_minute_by_minute_oracle_across_clock_changes():
    cases, expected = [], []
    for zone, exprs in ZONED:
        _zone(zone)
        for expr in exprs:
            for start, end in ZONED_WINDOWS:
                cases.append(["occurrences", [f"CRON_TZ={zone} {expr}", int(start.timestamp() * 1000), int(end.timestamp() * 1000), 400]])
                expected.append(_py_occurrences(expr, start, end, 400, zone))
    for case, js, py in zip(cases, _node(cases), expected):
        assert js == py, case
    assert len(cases) == 46 and all(expected), "every comparison must have something to compare: an empty list on both sides would prove nothing"


def _utc(*ms):
    return [dt.datetime.fromtimestamp(m / 1000, UTC).strftime("%Y-%m-%dT%H:%MZ") for m in ms]


def _ms(text):
    return int(dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def test_a_zone_moves_the_run_with_the_season_and_the_clock_change_hours_are_listed_once_or_not_at_all():
    _zone("America/New_York")
    nine = "CRON_TZ=America/New_York 0 9 * * *"
    got = _node([["occurrences", [nine, _ms("2026-01-10T00:00Z"), _ms("2026-01-11T00:00Z"), 5]], ["occurrences", [nine, _ms("2026-07-10T00:00Z"), _ms("2026-07-11T00:00Z"), 5]]])
    assert [_utc(*g) for g in got] == [["2026-01-10T14:00Z"], ["2026-07-10T13:00Z"]]                  # 09:00 is 14:00 UTC in winter and 13:00 UTC in summer
    gap = _one("occurrences", "CRON_TZ=America/New_York 30 2 * * *", _ms("2026-03-07T00:00Z"), _ms("2026-03-11T00:00Z"), 10)
    assert _utc(*gap) == ["2026-03-07T07:30Z", "2026-03-09T06:30Z", "2026-03-10T06:30Z"]              # the 8th has no 02:30: the clocks went from 02:00 to 03:00
    twice = _one("occurrences", "CRON_TZ=America/New_York 30 1 * * *", _ms("2026-10-31T12:00Z"), _ms("2026-11-02T12:00Z"), 10)
    assert _utc(*twice) == ["2026-11-01T05:30Z", "2026-11-02T06:30Z"]                                  # 01:30 happens twice on 1 November: once, the first
    got = _node([["occurrences", ["CRON_TZ=Asia/Kolkata 30 9 * * *", _ms("2026-10-16T00:00Z"), _ms("2026-10-17T00:00Z"), 5]],
                 ["occurrences", ["CRON_TZ=Australia/Lord_Howe 0 9 * * *", _ms("2026-06-10T00:00Z"), _ms("2026-06-11T00:00Z"), 5]],
                 ["occurrences", ["CRON_TZ=Australia/Lord_Howe 0 9 * * *", _ms("2026-01-10T00:00Z"), _ms("2026-01-11T00:00Z"), 5]]])
    assert [_utc(*g) for g in got] == [["2026-10-16T04:00Z"], ["2026-06-10T22:30Z"], ["2026-01-10T22:00Z"]]       # a half-hour zone; a zone that is 10:30 east in winter and 11 in summer
    assert _one("nextOccurrence", "CRON_TZ=Asia/Tokyo 0 9 * * *", NOW) == _ms("2026-10-17T00:00Z")
    assert _one("occurrences", "CRON_TZ=Asia/Tokyo 0 9 * * *", _ms("2026-10-17T00:00Z"), _ms("2026-10-18T00:00Z"), 5) == []       # nothing at the very start: the window is open at both ends


def test_a_zone_the_browser_does_not_know_or_a_name_that_is_not_one_is_not_a_schedule():
    names = ["America/New_York", "UTC", "Asia/Tokyo", "Etc/GMT+5", "Mars/Phobos", "+05:00", "05:00", "", "America/New York", "x" * 70, "a'b", "../x", "America/Argentina/Buenos_Aires/x/y", None, "Asia/Tokyo; rm"]
    assert _node([["validZone", [n]] for n in names]) == [True, True, True, True, False, False, False, False, False, False, False, False, False, False, False]
    got = _node([["parseCron", [e]] for e in ("CRON_TZ=Asia/Tokyo 0 9 * * *", "0 9 * * *", "CRON_TZ=Mars/Phobos 0 9 * * *", "CRON_TZ=Asia/Tokyo", "CRON_TZ=Asia/Tokyo 0 9 * *", "cron_tz=Asia/Tokyo 0 9 * * *", "CRON_TZ=+09:00 0 9 * * *")])
    assert [g and g["tz"] for g in got] == ["Asia/Tokyo", None, None, None, None, None, None] and got[1] is not None


def test_a_zoned_schedule_is_told_in_its_own_zone_and_a_plain_one_in_utc():
    got = _node([["describeCron", [e]] for e in ("CRON_TZ=Asia/Tokyo 17 9 * * *", "0 1 * * 3,6", "CRON_TZ=Europe/London 30 8 * * 1-5", "CRON_TZ=Asia/Kolkata 5 * * * *", "CRON_TZ=Asia/Tokyo */20 * * * *", "CRON_TZ=Asia/Tokyo 0 0 1 */2 *")] +
                 [["cronLabel", [e]] for e in ("CRON_TZ=Asia/Tokyo 17 9 * * *", "0 1 * * 3,6", "  5   4 * * 1  ", "not a cron")])
    assert got == ["hằng ngày 09:17 Asia/Tokyo", "01:00 UTC, T4 T7", "08:30 Europe/London, T2 T3 T4 T5 T6", "mỗi giờ, phút 05 (Asia/Kolkata)", "mỗi 20 phút", "0 0 1 */2 * (Asia/Tokyo)",
                   "17 9 * * * (Asia/Tokyo)", "0 1 * * 3,6 (UTC)", "5 4 * * 1 (UTC)", "not a cron"]


def test_the_timezone_next_to_a_cron_is_read_in_every_way_yaml_can_say_it():
    wf = """on:
  schedule:
    - cron: '30 5 * * 1-5'
      timezone: 'America/New_York'
    - timezone: Asia/Tokyo
      cron: "0 9 * * *"
    - cron: '0 1 * * 3'
    - {cron: "15 4 * * *", timezone: "Europe/Paris"}
    - cron: '0 2 * * *'
      timezone: Mars/Phobos
    - cron: 'CRON_TZ=Asia/Tokyo 0 3 * * *'
  workflow_dispatch:
jobs:
  x:
    steps:
      - name: not a schedule
        with:
          timezone: Asia/Tokyo
          cron: '0 7 * * *'
"""
    assert _one("extractCrons", wf) == ["CRON_TZ=America/New_York 30 5 * * 1-5", "CRON_TZ=Asia/Tokyo 0 9 * * *", "0 1 * * 3", "CRON_TZ=Europe/Paris 15 4 * * *"]
    flow = "on:\n  schedule: [{cron: '0 1 * * *', timezone: 'Asia/Tokyo'}, {cron: \"0 2 * * *\"}, {cron: '0 3 * * *', timezone: Nowhere/Land}]\n"
    assert _one("extractCrons", flow) == ["CRON_TZ=Asia/Tokyo 0 1 * * *", "0 2 * * *"]
    shell = "on:\n  schedule:\n    - cron: '0 1 * * *'\njobs:\n  a:\n    steps:\n      - run: |\n          echo timezone: Asia/Tokyo\n"
    assert _one("extractCrons", shell) == ["0 1 * * *"]                                                 # a line of a script is text, not YAML
    assert _node([["extractCrons", [None]], ["extractCrons", [""]], ["extractCrons", ["on:\n  schedule:\n    - timezone: Asia/Tokyo\n"]]]) == [[], [], []]       # a zone with no cron is nothing


def test_the_week_plan_and_the_count_follow_the_zone_of_the_schedule():
    wf = {"id": 1, "name": "Tokyo morning", "path": ".github/workflows/t.yml", "state": "active", "runs": [], "crons": ["CRON_TZ=Asia/Tokyo 0 9 * * *", "0 1 * * *"]}
    plan = _one("weekPlan", [_model(workflows=[wf])], NOW, 1)
    assert [(_utc(i["ms"])[0], i["cron"]) for i in plan["items"]] == [("2026-10-17T00:00Z", "CRON_TZ=Asia/Tokyo 0 9 * * *"), ("2026-10-17T01:00Z", "0 1 * * *")]
    assert _one("countScheduled", [_model(workflows=[wf])], NOW, NOW + 86400000) == 2


# ---- a run with more than a hundred jobs ---------------------------------------------------------------------------------------------

def _gh_job(i):
    return {"id": i, "name": f"j{i}", "status": "completed", "conclusion": "success", "steps": []}


def test_the_pages_of_jobs_still_to_ask_for_follow_the_total_the_run_reports_and_stop_at_a_limit():
    hundred = [_gh_job(i) for i in range(100)]
    cases = [{"jobs": hundred, "total_count": 100}, {"jobs": hundred, "total_count": 101}, {"jobs": hundred, "total_count": 250}, {"jobs": hundred, "total_count": 10 ** 6},
             {"jobs": hundred[:99], "total_count": 500}, {"jobs": hundred}, {"jobs": hundred, "total_count": "9"}, None, {"jobs": hundred, "total_count": -5}]
    assert _node([["morePages", [c]] for c in cases]) == [[], [2], [2, 3], [2, 3, 4, 5], [], [], [], [], []]


def test_the_jobs_of_all_pages_are_joined_once_each_and_the_missing_ones_are_counted():
    first = {"jobs": [_gh_job(i) for i in range(100)], "total_count": 230}
    got = _node([["gatherJobs", [first, [[_gh_job(i) for i in range(100, 200)], [_gh_job(i) for i in range(200, 230)]]]],           # all there
                 ["gatherJobs", [first, [[_gh_job(i) for i in range(100, 200)], None]]],                                            # a page that failed
                 ["gatherJobs", [first, []]],                                                                                       # the later pages not asked for
                 ["gatherJobs", [{"jobs": [_gh_job(1), _gh_job(1), _gh_job(2)], "total_count": 3}, [[_gh_job(2)]]]],              # a job listed twice counts once
                 ["gatherJobs", [{"jobs": [_gh_job(1)]}, []]]])                                                                    # no total: nothing to say is missing
    assert [(len(g["jobs"]), g["omitted"]) for g in got] == [(230, 0), (200, 30), (100, 130), (2, 1), (1, 0)]


# ---- answers that arrive in any order, and a run that is run again --------------------------------------------------------------------

def test_a_late_quota_reading_never_raises_the_count_and_an_old_window_never_replaces_the_current_one():
    rate = lambda remaining, reset, limit=5000: {"remaining": remaining, "limit": limit, "resetMs": reset}      # noqa: E731
    cases = [(rate(100, 1000), rate(120, 1000)),            # a late answer with a higher count: the lower one stays
             (rate(100, 1000), rate(90, 1000)),             # a lower count is the newer truth
             (rate(100, 1000), rate(4990, 2000)),           # a new window: the count starts again
             (rate(100, 2000), rate(4990, 1000)),           # an answer from a window that has ended
             (None, rate(50, 1000)), (rate(50, 1000), None),
             (rate(100, None), rate(120, None)), (rate(100, 1000), rate(120, None)), (rate(100, None), rate(120, 1000)),       # no reset time: no window to compare
             (rate(100, 1000), rate(120, 1000, 60))]
    assert _node([["mergeRate", list(c)] for c in cases]) == [rate(100, 1000), rate(90, 1000), rate(4990, 2000), rate(100, 2000), rate(50, 1000), rate(50, 1000),
                                                              rate(120, None), rate(120, None), rate(120, 1000), rate(100, 1000, 60)]


def test_the_jobs_read_for_one_attempt_of_a_run_are_not_taken_for_the_next():
    runs = [{"id": 7}, {"id": 7, "attempt": 0}, {"id": 7, "attempt": 1}, {"id": 7, "attempt": 2}, {"id": 8, "attempt": 2}]
    assert _node([["jobKey", ["o/r", r]] for r in runs]) == ["o/r#7#1", "o/r#7#1", "o/r#7#1", "o/r#7#2", "o/r#8#2"]


# ---- a repository with many open pull requests -------------------------------------------------------------------------------------

def test_a_pull_request_that_a_run_belongs_to_is_a_card_even_beyond_the_twelve_newest():
    pulls = [{"number": n, "title": f"t{n}", "branch": f"feat/{n}", "headRepo": "o/r", "user": "u", "userType": "User", "draft": False} for n in range(1, 15)]
    run = lambda i, branch: {"id": i, "number": i, "status": "completed", "conclusion": "success", "event": "pull_request", "branch": branch, "headRepo": "o/r", "actor": "u", "startedAt": None, "updatedAt": None}    # noqa: E731
    wf = lambda i, branch: {"id": i, "name": f"CI{i}", "path": f".github/workflows/ci{i}.yml", "state": "active", "crons": [], "dispatch": False, "runs": [run(i, branch)]}    # noqa: E731
    model = lambda *wfs: _model(repo="o/r", info={"defaultBranch": "main"}, pulls=pulls, workflows=list(wfs))    # noqa: E731
    prs = lambda board: sorted(n["ref"]["pr"] for n in board["nodes"] if n["kind"] == "pr")    # noqa: E731
    assert prs(_board([model()])) == list(range(1, 13))                                                      # nothing belongs to the 13th and 14th: they are left out
    both = _board([model(wf(1, "feat/14"), wf(2, "feat/3"))])
    assert prs(both) == list(range(1, 13)) + [14]                                                            # the 14th has a run on the board: it is a card; the 3rd is not doubled
    assert any(e["from"] == "r|o/r|1" and e["to"] == "p|o/r|14" for e in both["edges"]) and any(e["from"] == "r|o/r|2" and e["to"] == "p|o/r|3" for e in both["edges"])
