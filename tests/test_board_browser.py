"""index.html in a real browser (Chromium through Playwright), with api.github.com answered by the test.

Skipped when Playwright or a browser is missing (CI installs neither): tests/test_board.py is what always runs. These prove what no source check can:
that the token goes only into one header to one host, that the page only reads, that text from GitHub stays text, and what a person sees and can do."""

import datetime as dt
import hashlib
import json
import os
import re
import time
from pathlib import Path

import pytest

from scripts import sync_csp

sync_api = pytest.importorskip("playwright.sync_api")

# The page is served from an address of its own, as it would be from `python3 -m http.server`, not opened as a file: Chromium may swap the process of a file:// page on
# reload, and then a localStorage write made an instant before can be missing when the reloaded page reads it (seen under load). Over http(s) the write is ordered.
PAGE_URL = "https://radar.test/board.html"
TOKEN = "github_pat_TESTONLY_not_a_real_token_0123456789"
REPO = "demo/app"
STORE_KEY = "radar-board.v1"
VIEWS = ["home", "board", "repos", "schedule", "inbox", "agents", "health", "settings"]
CORS = {"access-control-allow-origin": "*", "access-control-expose-headers": "etag, retry-after, x-ratelimit-remaining, x-ratelimit-limit, x-ratelimit-reset"}
PREFLIGHT = {**CORS, "access-control-allow-headers": "authorization, accept, x-github-api-version, if-none-match", "access-control-allow-methods": "GET, OPTIONS"}
EVIL = '<img src=x onerror="window.__pwn=1">'


def iso(seconds_ago: float = 0) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as pw:
        try:
            exe = os.environ.get("RADAR_CHROMIUM") or None
            b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        except Exception as exc:                                       # no browser installed in this environment
            pytest.skip(f"Chromium is not available: {str(exc)[:80]}")
        yield b
        b.close()


def run_of(run_id, wf, number, status, conclusion, ago, event="push", branch="main", head=REPO):
    return {"head_repository": {"full_name": head}, "id": run_id, "workflow_id": wf, "run_number": number, "status": status, "conclusion": conclusion, "event": event, "head_branch": branch, "actor": {"login": "me"},
            "created_at": iso(ago), "run_started_at": iso(ago), "updated_at": iso(ago - 100)}


def app_routes(repo: str = REPO) -> dict:
    """The answers of a small repository: CI with a run in progress (one job, three steps) on a branch that has an agent pull request, a nightly that failed, a weekly
    workflow whose only run is older than the 40 newest of the repository (so it is missing from the repository's own list), a schedule, and .claude/agents."""
    base = f"/repos/{repo}"
    run_of_here = lambda *a, **k: run_of(*a, head=repo, **k)          # noqa: E731  (the runs and the pull request come from this repository, not from a fork)
    return {
        f"{base}/actions/workflows/1/runs": (200, {"workflow_runs": [run_of_here(101, 1, 8, "in_progress", None, 120, branch="claude/fix-x"), run_of_here(100, 1, 7, "completed", "success", 4000)]}),
        f"{base}/actions/workflows/2/runs": (200, {"workflow_runs": [run_of_here(90, 2, 3, "completed", "failure", 90000, "schedule"), run_of_here(80, 2, 2, "completed", "success", 180000, "schedule")]}),
        f"{base}/actions/workflows/3/runs": (200, {"workflow_runs": [run_of_here(70, 3, 1, "completed", "success", 500000)]}),
        f"{base}/contents/.github/workflows/weekly.yml": (200, "on:\n  schedule:\n    - cron: '0 3 * * 1'\n"),
        base: (200, {"full_name": repo, "description": "An app", "stargazers_count": 7, "private": False, "archived": False, "pushed_at": iso(300), "open_issues_count": 3, "default_branch": "main"}),
        f"{base}/actions/runs": (200, {"workflow_runs": [
            run_of_here(101, 1, 8, "in_progress", None, 120, branch="claude/fix-x"), run_of_here(100, 1, 7, "completed", "success", 4000), run_of_here(90, 2, 3, "completed", "failure", 90000, "schedule")]}),
        f"{base}/actions/workflows": (200, {"workflows": [
            {"id": 1, "name": EVIL + "CI", "path": ".github/workflows/ci.yml", "state": "active"},
            {"id": 2, "name": "Nightly", "path": ".github/workflows/nightly.yml", "state": "active"},
            {"id": 3, "name": "Weekly scan", "path": ".github/workflows/weekly.yml", "state": "active"}]}),
        f"{base}/pulls": (200, [{"number": 5, "title": "Fix <b>x</b>", "head": {"ref": "claude/fix-x", "repo": {"full_name": repo}}, "user": {"login": "me", "type": "User"}, "draft": False, "updated_at": iso(60), "created_at": iso(60)}]),
        f"{base}/contents/.github/workflows/ci.yml": (200, "on:\n  push:\n  workflow_dispatch:\n"),
        f"{base}/contents/.github/workflows/nightly.yml": (200, "on:\n  schedule:\n    - cron: '0 18 * * *'\n"),
        f"{base}/contents/.claude/agents": (200, [{"name": "architect.md", "type": "file"}, {"name": "README.txt", "type": "file"}, {"name": "sub", "type": "dir"}]),
        f"{base}/actions/runs/101/jobs": (200, {"jobs": [{"id": 1, "name": "build", "status": "in_progress", "started_at": iso(100), "steps": [
            {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1, "started_at": iso(100), "completed_at": iso(97)},
            {"name": "Test", "status": "in_progress", "conclusion": None, "number": 2, "started_at": iso(97)},
            {"name": "Deploy", "status": "queued", "conclusion": None, "number": 3}]}]}),
        f"{base}/actions/runs/100/jobs": (200, {"jobs": [{"id": 2, "name": "build", "status": "completed", "conclusion": "success", "started_at": iso(4000), "completed_at": iso(3800), "steps": [
            {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1}]}]}),
        f"{base}/actions/runs/90/jobs": (200, {"jobs": [{"id": 3, "name": "nightly", "status": "completed", "conclusion": "failure", "started_at": iso(90000), "completed_at": iso(89000), "steps": [
            {"name": "Checkout", "status": "completed", "conclusion": "success", "number": 1}, {"name": "Run e2e", "status": "completed", "conclusion": "failure", "number": 2}]}]}),
    }


class Github:
    """api.github.com as the test wants it: records every request, answers from `routes` ({path: (status, body)}), with ETags and a rate limit."""

    def __init__(self):
        self.requests: list[dict] = []
        self.routes: dict = app_routes()
        self.remaining = 4990
        self.reset = int(time.time()) + 3000                      # one window: GitHub gives every answer of it the same reset time
        self.not_modified = 0
        self.extra: dict = {}                                     # headers added to every answer (a Retry-After, for example)
        self.remaining_at: dict = {}                              # {path: count}: the count that the answer for one path reports, instead of `remaining`

    def handle(self, route, request):
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=PREFLIGHT)
        path = request.url.split("https://api.github.com", 1)[1]
        clean = path.split("?", 1)[0]
        self.requests.append({"method": request.method, "path": path, "clean": clean, "headers": request.headers})
        rate = {"x-ratelimit-remaining": str(self.remaining_at.get(clean, self.remaining)), "x-ratelimit-limit": "5000", "x-ratelimit-reset": str(self.reset), **self.extra}
        if clean not in self.routes:
            return route.fulfill(status=404, headers={**CORS, **rate}, content_type="application/json", body='{"message":"Not Found"}')
        status, body = self.routes[clean]
        if callable(body):
            status, body = body()
        text = body if isinstance(body, str) else json.dumps(body)
        etag = '"' + hashlib.sha1(text.encode()).hexdigest()[:10] + '"'
        if request.headers.get("if-none-match") == etag:
            self.not_modified += 1
            return route.fulfill(status=304, headers={**CORS, **rate, "etag": etag})
        return route.fulfill(status=status, headers={**CORS, **rate, "etag": etag}, content_type="text/plain" if isinstance(body, str) else "application/json", body=text)

    def paths(self, method="GET") -> list:
        return [r["clean"] for r in self.requests if r["method"] == method]

    def conditional(self, since: int) -> list:
        """The requests made after the first `since`, each with whether it asked 'only if changed'."""
        return [("if-none-match" in r["headers"]) for r in self.requests[since:]]


def serve(context):
    context.route("https://radar.test/**", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=sync_csp.PAGE.read_text(encoding="utf-8")))


def new_page(browser, repos=(REPO,), width=1280, interval=0, tz="Asia/Ho_Chi_Minh", scheme="dark", theme=None, mode=None):
    """A fresh context whose saved list holds `repos` (set once, so a reload keeps whatever the page saved since)."""
    context = browser.new_context(viewport={"width": width, "height": 900}, timezone_id=tz, locale="vi-VN", color_scheme=scheme)
    seed = {"repos": list(repos), "interval": interval}
    if theme:
        seed["theme"] = theme
    if mode:
        seed["mode"] = mode
    context.add_init_script(f"if (!window.localStorage.getItem('{STORE_KEY}')) window.localStorage.setItem('{STORE_KEY}', {json.dumps(json.dumps(seed))});")
    gh = Github()
    context.route("https://api.github.com/**", gh.handle)
    serve(context)
    page = context.new_page()
    seen, problems = [], []
    page.on("request", lambda r: seen.append(r.url))
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on("console", lambda m: problems.append(m.text) if m.type in ("error", "warning") else None)
    page.gh, page.seen, page.problems = gh, seen, problems
    return context, page


def wait(page, expression, seconds=8):
    """Wait until a JavaScript expression is true. Playwright's own wait_for_function evals a string, which the page's CSP (no unsafe-eval, on
    purpose) refuses; evaluate() goes through the debugging protocol instead."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if page.evaluate(f"() => Boolean({expression})"):
            return
        time.sleep(0.05)
    raise AssertionError(f"still false after {seconds}s: {expression}")


def text(page, selector):
    return page.inner_text(selector)


def open_tab(page, name):
    page.click(f"#tab-{name}")
    wait(page, f"!document.getElementById('view-{name}').hidden")


def loaded(page):
    """Home has drawn the pipeline of the run in progress: the repository was read."""
    wait(page, "document.querySelector('#home-runs .pipe') !== null")


@pytest.fixture
def ui(browser):
    context, page = new_page(browser)
    page.goto(PAGE_URL)
    loaded(page)
    yield page
    context.close()


def quiet(page, seconds=0.5, limit=10):
    """Wait until the page has sent nothing for a while (a look in progress has finished), and return how many requests there have been."""
    last, since, deadline = -1, time.monotonic(), time.monotonic() + limit
    while time.monotonic() < deadline:
        n = len(page.gh.requests)
        if n != last:
            last, since = n, time.monotonic()
        elif time.monotonic() - since >= seconds:
            return n
        page.wait_for_timeout(50)
    raise AssertionError("the page keeps sending")


def counts(page, container):
    return {b.get_attribute("data-status"): int(b.locator(".n").inner_text()) for b in page.locator(f"#{container} button.cnt").all()}


# ---- Home: the pipeline of a task --------------------------------------------------------------------------------------------

def test_the_page_loads_clean_and_only_reads(ui):
    assert ui.problems == []                                                         # no CSP violation, no script error
    assert ui.title() == "GitHub Radar: bảng theo dõi repo"
    assert ui.gh.requests and all(r["method"] == "GET" for r in ui.gh.requests)
    assert all("authorization" not in r["headers"] for r in ui.gh.requests)          # no token, no header
    assert {u.split("/")[2] for u in ui.seen if u.startswith("http")} == {"api.github.com", "radar.test"}              # the page itself, and GitHub: no one else
    assert {u for u in ui.seen if "radar.test" in u} <= {PAGE_URL, "https://radar.test/favicon.ico"}                  # the page loads nothing of its own: one document


def test_a_running_task_is_drawn_as_a_pipeline_with_its_job_and_each_step(ui):
    pipe = ui.locator("#home-runs .pipe")
    assert pipe.count() == 1
    head = pipe.inner_text()
    assert "Đang chạy" in head and "demo/app" in head and "lượt #8" in head and "nhánh claude/fix-x" in head and "đã chạy 2 phút" in head
    titles = ui.eval_on_selector_all("#home-runs .pipe .fbox .t", "els => els.map(e => e.lastElementChild.textContent)")
    assert titles == ["Bắt đầu", "build", "PR #5"]                                    # start, the job, and the pull request of the same branch
    states = ui.eval_on_selector_all("#home-runs .pipe .fbox", "els => els.map(e => e.className.split(' ').find(c => c.startsWith('st-')))")
    assert states == ["st-done", "st-running", "st-queued"]
    assert ui.eval_on_selector_all("#home-runs .pipe .rows li", "els => els.map(e => e.className)") == ["st-done", "st-running", "st-queued"]       # the steps, in order
    assert ui.get_attribute("#home-runs progress", "value") == "1" and ui.get_attribute("#home-runs progress", "max") == "3" and "1/3 bước" in head
    assert "chưa bắt đầu" in pipe.inner_text() and ui.locator("#home-runs .legend span").count() == 5


def test_the_status_bar_counts_what_is_on_the_board_and_a_click_goes_to_it(ui):
    assert counts(ui, "home-chips") == {"needs": 2, "running": 2, "review": 1, "under": 0, "ready": 4, "blocked": 0, "done": 3}
    ui.click("#home-chips button[data-status='needs']")
    wait(ui, "!document.getElementById('view-board').hidden")
    assert ui.get_attribute("#board-chips button[data-status='needs']", "aria-pressed") == "true" and text(ui, "#f-count") == "2 / 12"


def test_the_side_panels_say_what_needs_the_person_and_what_is_ready(ui):
    assert text(ui, "#needs-count") == "2"
    needs = text(ui, "#needs-you")
    assert "Nightly #3" in needs and "Fix <b>x</b>" in needs and "PR từ claude chờ bạn soát" in needs
    assert ui.query_selector("#needs-you b b") is None                               # the title is text, not markup
    assert text(ui, "#ready-count") == "0" and "Nightly" in text(ui, "#ready-now") and "theo lịch" in text(ui, "#ready-now")      # nothing to start by hand (CI is going); the nightly is due within a day


def test_the_inbox_lists_the_same_things_with_a_badge(ui):
    assert text(ui, "#inbox-count") == "2"
    open_tab(ui, "inbox")
    items = ui.eval_on_selector_all("#inbox-list > li", "els => els.map(e => e.innerText.replace(/\\s+/g, ' '))")
    assert len(items) == 2 and any("Lỗi" in i and "Nightly #3" in i for i in items) and any("Chờ soát" in i and "PR từ claude" in i for i in items)
    assert ui.get_attribute("#inbox-list a >> nth=0", "href").startswith("https://github.com/demo/app/")


# ---- Board ------------------------------------------------------------------------------------------------------------------

def board(page):
    open_tab(page, "board")
    wait(page, "document.querySelectorAll('#wavegrid button.node').length > 0")
    wait(page, "document.querySelectorAll('#edges path').length > 0")


def test_the_board_has_four_waves_with_cards_and_lines_where_the_data_relates_them(ui):
    board(ui)
    assert ui.locator("#wavegrid .wave").count() == 4 and text(ui, "#f-count") == "12 / 12"
    heads = ui.eval_on_selector_all("#wavegrid .wave > h3", "els => els.map(e => e.innerText.replace(/\\s+/g, ' '))")
    assert heads == ["Wave 0 · Kích hoạt 4", "Wave 1 · Workflow 1/3", "Wave 2 · Lượt chạy 2/4", "Wave 3 · PR / nhánh 0/1"]
    assert ui.locator("#edges path").count() == 10                                    # 5 trigger->workflow, 4 workflow->run, 1 run->pull request of the same branch
    assert ui.locator("#edges path.crit").count() == 2                                # upstream of the failed nightly
    d = ui.eval_on_selector_all("#edges path", "els => els.map(e => e.getAttribute('d'))")
    assert all(re.fullmatch(r"M[\d.]+ [\d.]+ C[\d. ]+", x) for x in d)                # coordinates and nothing else
    assert ui.eval_on_selector("#edges", "e => e.namespaceURI") == "http://www.w3.org/2000/svg"
    assert "<img" not in ui.inner_html("#wavegrid") and ui.query_selector("#wavegrid img") is None and ui.evaluate("window.__pwn") is None


def test_a_card_lights_up_its_chain_and_dims_the_rest_and_a_second_click_lets_go(ui):
    board(ui)
    card = ui.locator("button.node[data-id='r|demo/app|101']")
    card.click()
    assert ui.locator("button.node.focus").count() == 1 and ui.locator("button.node.chain").count() == 4 and ui.locator("button.node.dim").count() == 7
    wait(ui, "document.querySelectorAll('#edges path.chain').length === 4")             # the lines are redrawn on the next animation frame, not in the click itself
    assert ui.locator("#edges path.chain").count() == 4 and ui.get_attribute("button.node.focus", "aria-pressed") == "true"
    assert "Mở lượt chạy #8 trên GitHub" in text(ui, "#board-detail") and ui.get_attribute("#board-detail a", "href") == "https://github.com/demo/app/actions/runs/101"
    ui.locator("button.node[data-id='p|demo/app|5']").click()                         # another card: the chain moves
    assert ui.locator("button.node.focus").count() == 1 and ui.get_attribute("button.node.focus", "data-id") == "p|demo/app|5"
    ui.locator("button.node[data-id='p|demo/app|5']").click()
    assert ui.locator("button.node.focus").count() == 0 and ui.locator("button.node.dim").count() == 0 and "Chọn một thẻ" in text(ui, "#board-detail")


def test_cards_work_from_the_keyboard(ui):
    board(ui)
    ui.locator("button.node[data-id='w|demo/app|2']").focus()
    ui.keyboard.press("Enter")
    assert ui.get_attribute("button.node.focus", "data-id") == "w|demo/app|2"
    assert ui.evaluate("document.activeElement.dataset.id") == "w|demo/app|2"        # a redraw does not take the focus away


def test_a_pull_request_from_a_fork_with_the_same_branch_name_is_not_joined_to_the_run(browser):
    context, page = new_page(browser)
    pulls = page.gh.routes[f"/repos/{REPO}/pulls"][1]
    pulls[0]["head"]["repo"]["full_name"] = "someone/app"                                 # the same branch name, but in a fork
    page.goto(PAGE_URL)
    loaded(page)
    assert page.eval_on_selector_all("#home-runs .pipe .fbox .t", "els => els.map(e => e.lastElementChild.textContent)") == ["Bắt đầu", "build", "Kết quả"]
    board(page)
    assert page.locator("button.node[data-kind='pr']").count() == 1 and page.locator("#edges path").count() == 9          # still on the board, with no line from the run
    context.close()


def test_a_failure_older_than_two_weeks_is_marked_old_and_no_longer_calls_for_the_person(browser):
    context, page = new_page(browser)
    old = run_of(90, 2, 3, "completed", "failure", 20 * 86400, "schedule", head=REPO)
    page.gh.routes[f"/repos/{REPO}/actions/workflows/2/runs"] = (200, {"workflow_runs": [old]})
    page.gh.routes[f"/repos/{REPO}/actions/runs"][1]["workflow_runs"] = [r for r in page.gh.routes[f"/repos/{REPO}/actions/runs"][1]["workflow_runs"] if r["id"] != 90] + [old]
    page.goto(PAGE_URL)
    loaded(page)
    chips = counts(page, "home-chips")
    assert chips["needs"] == 0 and chips["blocked"] == 2                                                  # the nightly and its run: nothing asks for the person
    assert text(page, "#inbox-count") == "1" and "Nightly" not in text(page, "#needs-you")               # only the agent's pull request is left
    open_tab(page, "repos")
    assert "Lỗi cũ" in text(page, "#repo-grid") and "Có lỗi" not in text(page, "#repo-grid")
    board(page)
    assert "(cũ)" in page.locator("button.node[data-id='r|demo/app|90']").inner_text().lower()          # the chip is set in capitals
    context.close()


def test_the_filters_narrow_the_board_and_reset_brings_it_back(ui):
    board(ui)
    ui.fill("#f-q", "nightly")
    wait(ui, "document.getElementById('f-count').textContent === '2 / 12'")
    assert {b.get_attribute("data-kind") for b in ui.locator("#wavegrid button.node").all()} == {"workflow", "run"}
    ui.fill("#f-q", "")
    ui.select_option("#f-status", "review")
    wait(ui, "document.getElementById('f-count').textContent === '1 / 12'")
    assert counts(ui, "board-chips")["done"] == 3                                     # the counts still show the other statuses
    ui.select_option("#f-status", "")
    ui.select_option("#f-repo", REPO)
    assert text(ui, "#f-count") == "12 / 12"
    ui.click("#board-chips button[data-status='needs']")
    assert text(ui, "#f-count") == "2 / 12" and ui.input_value("#f-status") == "needs"            # the chip and the list box agree
    ui.click("#f-reset")
    assert text(ui, "#f-count") == "12 / 12" and ui.input_value("#f-q") == "" and ui.input_value("#f-status") == ""


def test_done_cards_can_be_shown_folded_or_hidden_and_the_board_can_be_grouped_by_repo(ui):
    board(ui)
    assert ui.locator("button.node.compact").count() == 3 and ui.get_attribute("#rest-collapse", "aria-pressed") == "true"
    ui.click("#rest-show")
    assert ui.locator("button.node.compact").count() == 0
    ui.click("#rest-hide")
    assert text(ui, "#f-count") == "9 / 12"
    ui.click("#grp-repo")
    wait(ui, "document.querySelectorAll('#wavegrid .wave').length === 1")
    wait(ui, "document.querySelectorAll('#edges path').length === 0")                  # the lines are cleared on the next animation frame
    assert ui.inner_text("#wavegrid .wave > h3").replace("\n", " ").startswith("demo/app") and ui.locator("#edges path").count() == 0       # one column per repo, no lines
    ui.click("#grp-flow")
    wait(ui, "document.querySelectorAll('#edges path').length > 0")


def test_text_from_github_stays_text_everywhere_it_is_drawn(ui):
    assert ui.evaluate("window.__pwn") is None and ui.query_selector("#home-runs img") is None
    assert EVIL + "CI" in text(ui, "#home-runs .pipe header")
    board(ui)
    assert ui.query_selector("#wavegrid img") is None and any(EVIL + "CI" in t for t in ui.eval_on_selector_all("#wavegrid .title", "els => els.map(e => e.textContent)"))
    open_tab(ui, "repos")
    assert EVIL + "CI" in text(ui, "details.wf .name") and ui.query_selector("#repo-grid img") is None
    open_tab(ui, "agents")
    assert "Fix <b>x</b>" in text(ui, "#agents-body") and ui.query_selector("#agents-body li b") is None


# ---- Repo, Schedule, Agents ---------------------------------------------------------------------------------------------------

def repos(page):
    open_tab(page, "repos")
    wait(page, "document.querySelector('#repo-grid .repo h3 a') !== null")


def test_a_row_for_a_finished_workflow_loads_its_steps_when_opened(ui):
    repos(ui)
    row = ui.locator("details.wf", has_text="Nightly")
    assert "Lỗi" in row.inner_text() and not [p for p in ui.gh.paths() if p.endswith("/runs/90/jobs")]            # nothing is read until it is opened
    row.locator("summary").click()
    wait(ui, "document.querySelectorAll('details.wf[open] .rows li.st-failed').length === 1")
    assert [p for p in ui.gh.paths() if p.endswith("/runs/90/jobs")] and "Run e2e" in row.inner_text()
    states = row.locator(".fbox").evaluate_all("els => els.map(e => e.className.split(' ').find(c => c.startsWith('st-')))")
    assert states == ["st-done", "st-failed", "st-failed"]                                                       # start, the failed job, the result


def test_a_failed_read_of_a_finished_runs_jobs_is_forgotten_after_half_a_minute_and_tried_again(browser):
    context, page = new_page(browser)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    good = page.gh.routes[f"/repos/{REPO}/actions/runs/90/jobs"]
    page.gh.routes[f"/repos/{REPO}/actions/runs/90/jobs"] = (500, {"message": "boom"})
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    row = page.locator("details.wf", has_text="Nightly")
    open_row = "document.querySelector('details.wf[open]')"
    row.locator("summary").click()
    wait(page, f"{open_row} !== null && {open_row}.textContent.includes('Chưa đọc được các bước')")           # the first read failed
    page.gh.routes[f"/repos/{REPO}/actions/runs/90/jobs"] = good                                              # GitHub is back
    row.locator("summary").click()
    wait(page, f"{open_row} === null")
    row.locator("summary").click()                                                                            # folded and opened again, within the half minute: the failure is still what is shown
    wait(page, f"{open_row} !== null")
    assert "Chưa đọc được các bước" in row.inner_text() and "Run e2e" not in row.inner_text()
    page.clock.run_for(31_000)
    row.locator("summary").click()
    wait(page, f"{open_row} === null")
    row.locator("summary").click()                                                                            # after the half minute the failure is forgotten and the jobs are asked for again
    wait(page, f"{open_row} !== null && {open_row}.textContent.includes('Run e2e')")
    context.close()


def test_a_failed_first_read_of_the_steps_of_a_running_task_is_forgotten_after_half_a_minute_and_tried_again(browser):
    context, page = new_page(browser)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    asked = paged_jobs(context, 3, fail_once_page=1)                                                           # the first read of the run in progress fails; GitHub is back at once
    page.goto(PAGE_URL)
    pipe = "document.querySelector('#home-runs .pipe')"
    wait(page, f"{pipe} !== null && {pipe}.textContent.includes('Chưa đọc được các bước')")
    assert asked == [1]
    open_tab(page, "settings")
    open_tab(page, "home")                                                                                     # drawn again within the half minute: the failure is what is shown, nothing is asked
    assert "Chưa đọc được các bước" in text(page, "#home-runs .pipe") and asked == [1]
    page.clock.run_for(31_000)
    open_tab(page, "settings")
    open_tab(page, "home")                                                                                     # after it the failure is forgotten and the steps are asked for again
    wait(page, f"{pipe}.querySelector('.total') !== null")
    assert "3 job" in text(page, "#home-runs .pipe .total") and asked == [1, 1] and "Chưa đọc được các bước" not in text(page, "#home-runs .pipe")
    context.close()


def test_a_first_read_of_the_steps_of_a_running_task_that_lost_a_page_is_asked_for_again_after_half_a_minute(browser):
    context, page = new_page(browser)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    asked = paged_jobs(context, 250, fail_once_page=3)
    page.goto(PAGE_URL)
    loaded(page)
    assert "200 job" in text(page, "#home-runs .pipe .total") and "50 job nữa chưa đọc được" in text(page, "#home-runs .pipe p.bad")           # the third page failed
    open_tab(page, "settings")
    open_tab(page, "home")
    assert "200 job" in text(page, "#home-runs .pipe .total") and asked.count(3) == 1                           # within the half minute what was read is shown, nothing is asked
    page.clock.run_for(31_000)
    open_tab(page, "settings")
    open_tab(page, "home")
    wait(page, "document.querySelector('#home-runs .pipe .total') !== null && document.querySelector('#home-runs .pipe .total').textContent.includes('250 job')")
    assert asked.count(3) == 2 and page.locator("#home-runs .pipe p.bad").count() == 0
    context.close()


def test_the_steps_of_a_running_task_read_in_full_are_not_asked_for_again_when_the_page_is_drawn_later(browser):
    context, page = new_page(browser)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    asked = paged_jobs(context, 3)
    page.goto(PAGE_URL)
    loaded(page)
    assert asked == [1]
    page.clock.run_for(31_000)
    open_tab(page, "settings")
    open_tab(page, "home")                                                                                     # what the repository read stays until the repository is read again
    assert "3 job" in text(page, "#home-runs .pipe .total") and asked == [1]
    context.close()


def test_a_job_with_more_steps_than_are_kept_says_how_many_are_left_out(browser):
    context, page = new_page(browser)
    done = lambda i: {"name": f"step {i}", "status": "completed", "conclusion": "success", "number": i, "started_at": iso(100), "completed_at": iso(90)}      # noqa: E731
    steps = [done(i) for i in range(1, 301)] + [{"name": f"step {i}", "status": "in_progress" if i == 301 else "queued", "conclusion": None, "number": i} for i in range(301, 351)]
    page.gh.routes[f"/repos/{REPO}/actions/runs/101/jobs"] = (200, {"jobs": [{"id": 1, "name": "build", "status": "in_progress", "started_at": iso(100), "steps": steps}]})
    page.goto(PAGE_URL)
    loaded(page)
    assert "50 bước nữa" in text(page, "#home-runs .pipe p.bad") and "tối đa 300 bước" in text(page, "#home-runs .pipe p.bad")         # step 301 is running: 300/300 must not read as finished
    assert "300/300 bước" in text(page, "#home-runs .pipe") and page.locator("#home-runs .pipe .rows li").count() == 300
    context.close()


def test_a_job_with_exactly_the_steps_that_are_kept_says_nothing_is_left_out(browser):
    context, page = new_page(browser)
    steps = [{"name": f"step {i}", "status": "completed", "conclusion": "success", "number": i, "started_at": iso(100), "completed_at": iso(90)} for i in range(1, 301)]
    page.gh.routes[f"/repos/{REPO}/actions/runs/101/jobs"] = (200, {"jobs": [{"id": 1, "name": "build", "status": "in_progress", "started_at": iso(100), "steps": steps}]})
    page.goto(PAGE_URL)
    loaded(page)
    assert page.locator("#home-runs .pipe p.bad").count() == 0 and page.locator("#home-runs .pipe .rows li").count() == 300
    context.close()


def test_a_run_that_is_run_again_does_not_show_the_steps_of_its_earlier_attempt(browser):
    context, page = new_page(browser)
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    row = page.locator("details.wf", has_text="Nightly")
    row.locator("summary").click()
    wait(page, "document.querySelectorAll('details.wf[open] .rows li.st-failed').length === 1")           # attempt 1 failed in "Run e2e"
    again = run_of(90, 2, 3, "completed", "success", 600, "schedule", head=REPO)
    again["run_attempt"] = 2                                                                              # run again: the same run id, the next attempt
    page.gh.routes[f"/repos/{REPO}/actions/workflows/2/runs"] = (200, {"workflow_runs": [again]})
    page.gh.routes[f"/repos/{REPO}/actions/runs"][1]["workflow_runs"] = [r for r in page.gh.routes[f"/repos/{REPO}/actions/runs"][1]["workflow_runs"] if r["id"] != 90] + [again]
    page.gh.routes[f"/repos/{REPO}/actions/runs/90/jobs"] = (200, {"jobs": [{"id": 31, "name": "nightly", "status": "completed", "conclusion": "success", "started_at": iso(700), "completed_at": iso(600),
                                                                              "steps": [{"name": "Run e2e (second try)", "status": "completed", "conclusion": "success", "number": 1}]}]})
    open_tab(page, "settings")
    page.click("#refresh")
    page.wait_for_timeout(600)
    open_tab(page, "repos")
    wait(page, "document.querySelector('details.wf[open]') !== null && document.querySelector('details.wf[open]').textContent.includes('Run e2e (second try)')")
    assert row.locator(".rows li.st-failed").count() == 0                                                 # not the failed step of the attempt before
    context.close()


def test_a_late_answer_with_a_higher_quota_count_does_not_put_the_count_back_up(browser):
    context, page = new_page(browser)
    held = []
    context.route(f"https://api.github.com/repos/{REPO}/actions/runs/101/jobs**", lambda route, request: route.fulfill(status=204, headers=PREFLIGHT) if request.method == "OPTIONS" else held.append(route))
    page.goto(PAGE_URL)
    deadline = time.monotonic() + 8
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held                                                                             # the last request of the look is out; every other answer (4990 left) is in
    page.wait_for_timeout(300)
    body = page.gh.routes[f"/repos/{REPO}/actions/runs/101/jobs"][1]
    held[0].fulfill(status=200, headers={**CORS, "x-ratelimit-remaining": "4999", "x-ratelimit-limit": "5000", "x-ratelimit-reset": str(page.gh.reset)}, content_type="application/json", body=json.dumps(body))
    loaded(page)
    assert "4990/5000" in re.sub(r"[.,\s]", "", page.text_content("#rate"))                                 # a count only goes down inside a window: 4999 was a late answer
    context.close()


def test_a_workflow_crowded_out_of_the_repository_list_still_shows_its_own_history(ui):
    repos(ui)
    weekly = ui.locator("details.wf", has_text="Weekly scan")
    assert "Thành công" in weekly.inner_text() and "Chưa thấy lượt chạy" not in weekly.inner_text()
    assert [p for p in ui.gh.paths() if p.endswith("/actions/workflows/3/runs")]
    nightly = ui.locator("details.wf", has_text="Nightly")
    assert nightly.locator(".spark").get_attribute("aria-label") == "2 lượt gần nhất: 1 thành công, 1 lỗi"          # run 90 came from both lists and counts once
    assert nightly.locator(".spark").get_attribute("role") == "img"


def test_the_schedule_is_read_from_the_workflow_file_and_shown_in_words_and_on_the_week(ui):
    repos(ui)
    assert any("hằng ngày 18:00 UTC" in t for t in ui.eval_on_selector_all(".sched", "els => els.map(e => e.textContent)"))
    assert "0 18 * * * (UTC)" in ui.eval_on_selector_all(".sched", "els => els.map(e => e.getAttribute('title'))")
    open_tab(ui, "schedule")
    assert ui.locator("#week .day li", has_text="Nightly").count() >= 6                  # one a day for the week


def test_a_schedule_that_names_a_time_zone_is_shown_and_counted_in_that_zone(browser):
    context, page = new_page(browser)                                                       # the browser is on Ho Chi Minh time (UTC+7, no clock changes)
    page.gh.routes[f"/repos/{REPO}/contents/.github/workflows/weekly.yml"] = (200, "on:\n  schedule:\n    - cron: '0 9 * * *'\n      timezone: 'Asia/Tokyo'\n")
    page.goto(PAGE_URL)
    loaded(page)
    open_tab(page, "repos")
    wait(page, "Array.from(document.querySelectorAll('.sched')).some((e) => e.textContent.includes('Asia/Tokyo'))")
    sched = next(t for t in page.eval_on_selector_all(".sched", "els => els.map(e => e.textContent)") if "Asia/Tokyo" in t)
    assert "hằng ngày 09:00 Asia/Tokyo" in sched and "07:00" in sched                      # 09:00 in Tokyo is 07:00 in Ho Chi Minh, every day of the year
    assert "0 9 * * * (Asia/Tokyo)" in page.eval_on_selector_all(".sched", "els => els.map(e => e.getAttribute('title'))")
    open_tab(page, "schedule")
    runs = page.eval_on_selector_all("#week .day li", "els => els.filter(e => e.textContent.includes('Weekly scan')).map(e => e.textContent)")
    assert len(runs) >= 6 and all("07:00" in r for r in runs)                              # one a day for the week, all at 07:00 local
    context.close()


def test_the_week_has_a_column_for_every_day_the_next_seven_days_touch(browser):
    """Seven times 24 hours from Sunday midday end on the next Sunday: its 01:30 run needs a column of its own, not a silent drop."""
    context, page = new_page(browser, tz="UTC")
    page.clock.set_fixed_time(dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc))
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.click("#demo")
    open_tab(page, "schedule")
    wait(page, "document.querySelectorAll('#week .day').length > 0")
    assert page.locator("#week .day").count() == 8
    last = page.locator("#week .day").nth(7).inner_text()
    assert "25-10" in last and "Deploy docs" in last and "01:30" in last
    assert "Hôm nay" in page.locator("#week .day").nth(0).inner_text()
    context.close()


def test_agents_are_guessed_from_names_and_the_declared_ones_are_listed(ui):
    open_tab(ui, "agents")
    body = text(ui, "#agents-body")
    assert "architect" in body and "README" not in body and "sub" not in body.split("agents/:")[1].split("\n")[0]        # only .md files
    assert "claude" in body and "#5" in body and "suy đoán theo tên" in text(ui, "#view-agents")


def test_an_office_snapshot_is_read_in_the_browser_and_kept_nowhere(ui, tmp_path):
    open_tab(ui, "agents")
    snap = {"version": 1, "mode": "claude", "now": time.time(), "live": True, "requirement": {"id": "REQ-9", "tier": "T2", "strategy": "main_with_architect_and_reviewer"},
            "progress": {"pct": 50, "agents": 2, "total": 2, "running": 1, "done": 1, "blocked": 0}, "phase": {"steps": ["analysis", "verify"], "current": "verify"},
            "agents": {"x": {"id": "x", "state": "working", "task": EVIL, "progress": 0.4, "model": "sonnet", "tokens": 1200}}}
    good = Path(tmp_path) / "snapshot.json"
    good.write_text(json.dumps(snap), encoding="utf-8")
    seen = len(ui.seen)
    ui.set_input_files("#office-file", str(good))
    wait(ui, "document.querySelector('.oa') !== null")
    assert "REQ-9" in text(ui, "#office-body") and EVIL in text(ui, ".oa") and ui.query_selector("#office-body img") is None and ui.evaluate("window.__pwn") is None
    assert len(ui.seen) == seen                                                          # the file went nowhere
    assert "REQ-9" not in json.dumps(ui.evaluate("() => Object.entries(window.localStorage)"))
    bad = Path(tmp_path) / "other.json"
    bad.write_text(json.dumps({**snap, "version": 2}), encoding="utf-8")
    ui.set_input_files("#office-file", str(bad))
    wait(ui, "document.getElementById('office-msg').className.includes('bad')")
    assert "version 1" in text(ui, "#office-msg") and "REQ-9" in text(ui, "#office-body")            # the good one stays
    notjson = Path(tmp_path) / "x.json"
    notjson.write_text("not json", encoding="utf-8")
    ui.set_input_files("#office-file", str(notjson))
    wait(ui, "document.getElementById('office-msg').textContent.includes('không phải JSON')")


# ---- Health and the live indicator ---------------------------------------------------------------------------------------------

def test_health_shows_each_repo_the_quota_and_how_many_requests_were_made(ui):
    open_tab(ui, "health")
    wait(ui, "document.getElementById('quota').max === 5000")
    assert ui.get_attribute("#quota", "value") == "4990" and "4.990/5.000" in text(ui, "#health-rate")
    assert f"{len(ui.gh.requests):,}".replace(",", ".") in text(ui, "#health-calls") and "tắt" in text(ui, "#health-calls")
    row = text(ui, "#health-rows")
    assert "demo/app" in row and "Có lỗi" in row and "3 workflow, 1 lỗi" in row


def test_the_live_indicator_says_how_the_page_is_refreshing(browser):
    context, page = new_page(browser, interval=30)
    page.goto(PAGE_URL)
    loaded(page)
    assert text(page, "#live-text") == "thủ công" and "Cần token" in page.get_attribute("#live", "title")          # an interval but no token: it cannot
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('live-text').textContent === 'live'")
    assert "is-live" in page.get_attribute("#live", "class") and "30 giây" in page.get_attribute("#live", "title")
    page.click("#demo")
    wait(page, "document.getElementById('live-text').textContent === 'mẫu'")
    context.close()


# ---- the token -----------------------------------------------------------------------------------------------------------------

def test_the_token_goes_only_into_one_header_and_is_kept_nowhere(ui):
    open_tab(ui, "settings")
    ui.gh.requests.clear()
    ui.fill("#token", TOKEN)
    ui.press("#token", "Tab")
    wait(ui, "document.getElementById('connect-msg').className.includes('ok')")
    deadline = time.monotonic() + 6
    while len(ui.gh.requests) < 12 and time.monotonic() < deadline:
        ui.wait_for_timeout(100)
    sent = ui.gh.requests
    assert len(sent) >= 12 and all(r["method"] == "GET" and r["headers"].get("authorization") == "Bearer " + TOKEN for r in sent)
    assert all(r["path"].startswith("/repos/") for r in sent)
    stores = ui.evaluate("() => ({ local: Object.entries(window.localStorage), session: Object.keys(window.sessionStorage), cookie: document.cookie, body: document.body.innerText })")
    assert TOKEN not in json.dumps(stores) and stores["session"] == [] and stores["cookie"] == "" and ui.context.cookies() == []
    assert TOKEN not in ui.url and ui.input_value("#token") == TOKEN and "5.000" in text(ui, "#rate")
    ui.gh.requests.clear()
    ui.click("#forget")
    wait(ui, "document.getElementById('connect-msg').textContent.includes('quên')")
    deadline = time.monotonic() + 6
    while len(ui.gh.requests) < 12 and time.monotonic() < deadline:
        ui.wait_for_timeout(100)
    assert ui.input_value("#token") == "" and ui.gh.requests and all("authorization" not in r["headers"] for r in ui.gh.requests)


def test_a_private_repo_is_unreadable_without_a_token_and_forgetting_the_token_drops_what_it_loaded(browser):
    context, page = new_page(browser)
    secret = app_routes()
    page.gh.routes = {path: (404, {"message": "Not Found"}) for path in secret}
    page.goto(PAGE_URL)
    open_tab(page, "repos")
    wait(page, "document.querySelector('.repo p.bad') !== null")
    assert "private hoặc sai tên" in text(page, ".repo p.bad")
    page.gh.routes = secret                                                              # with a token the same repo answers
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    open_tab(page, "home")
    wait(page, "document.querySelector('#home-runs .pipe') !== null")
    page.gh.routes = {path: (404, {"message": "Not Found"}) for path in secret}
    open_tab(page, "settings")
    page.click("#forget")
    open_tab(page, "home")
    wait(page, "document.querySelector('#home-runs .pipe') === null")                    # what the token fetched is gone from the page
    open_tab(page, "repos")
    wait(page, "document.querySelector('.repo p.bad') !== null")
    context.close()


def test_a_page_served_from_github_io_never_sends_a_token(browser):
    context = browser.new_context()
    gh = Github()
    context.route("https://api.github.com/**", gh.handle)
    context.route("https://someone.github.io/**", lambda route: route.fulfill(status=200, content_type="text/html; charset=utf-8", body=sync_csp.PAGE.read_text(encoding="utf-8")))
    page = context.new_page()
    page.goto("https://someone.github.io/ui/board.html")
    open_tab(page, "settings")
    assert page.is_visible("#origin-warning") and page.is_disabled("#token")
    page.evaluate("document.getElementById('token').disabled = false; document.getElementById('token').value = 'github_pat_x';"
                  "document.getElementById('token').dispatchEvent(new Event('change'))")
    page.wait_for_timeout(500)
    assert gh.requests and all("authorization" not in r["headers"] for r in gh.requests)          # a token forced into the field is not used
    context.close()


def test_forgetting_the_token_also_clears_the_account_list_the_token_loaded(browser):
    context, page = new_page(browser)
    page.gh.routes = {**app_routes(), "/user/repos": (200, [{"full_name": "me/secret-private", "owner": {"login": "me"}, "private": True, "pushed_at": iso(60)}])}
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    page.click("#pick")
    page.fill("#pick-owner", "me")
    page.click("#pick-load")
    wait(page, "document.querySelectorAll('#pick-list input').length === 1")
    assert "secret-private" in text(page, "#pick-list")
    page.click("details.io summary")
    page.click("#export")
    assert page.input_value("#io") != ""
    page.click("#forget")
    wait(page, "document.getElementById('connect-msg').textContent.includes('quên')")
    assert page.locator("#pick-list li").count() == 0 and page.input_value("#io") == "" and "secret-private" not in text(page, "body")
    context.close()


def test_a_list_still_on_its_way_when_the_token_is_forgotten_is_dropped_when_it_arrives(browser):
    context, page = new_page(browser)
    held = []
    context.route("https://api.github.com/user/repos**", lambda route, request: route.fulfill(status=204, headers=PREFLIGHT) if request.method == "OPTIONS" else held.append(route))
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    page.click("#pick")
    page.fill("#pick-owner", "me")
    page.click("#pick-load")
    deadline = time.monotonic() + 8
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held and text(page, "#link-msg") == "Đang tải danh sách…"                       # the request is out and waiting, authenticated by the token
    page.click("#forget")
    wait(page, "document.getElementById('connect-msg').textContent.includes('quên')")
    assert text(page, "#link-msg") == ""                                                    # "loading…" is not left standing for an answer that will never be used
    held[0].fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps([{"full_name": "me/secret-private", "owner": {"login": "me"}, "private": True, "pushed_at": iso(60)}]))
    page.wait_for_timeout(600)                                                              # long enough for the old code to have drawn it
    assert page.locator("#pick-list li").count() == 0 and "secret-private" not in text(page, "body") and text(page, "#link-msg") == ""
    context.close()


def test_a_picker_load_overtaken_by_a_newer_one_is_dropped_when_it_arrives(browser):
    context, page = new_page(browser)
    held = []
    context.route("https://api.github.com/users/slowowner/repos**", lambda route, request: route.fulfill(status=204, headers=PREFLIGHT) if request.method == "OPTIONS" else held.append(route))
    page.gh.routes["/users/fastowner/repos"] = (200, [{"full_name": "fastowner/new", "owner": {"login": "fastowner"}, "private": False, "pushed_at": iso(60)}])
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.click("#pick")
    page.fill("#pick-owner", "slowowner")
    page.click("#pick-load")
    deadline = time.monotonic() + 8
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held                                                                             # the first load is out and waiting
    page.fill("#pick-owner", "fastowner")
    page.click("#pick-load")
    wait(page, "document.querySelectorAll('#pick-list input').length === 1")
    held[0].fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps([{"full_name": "slowowner/old", "owner": {"login": "slowowner"}, "private": False, "pushed_at": iso(60)}]))
    page.wait_for_timeout(600)                                                              # long enough for the old code to have drawn it
    assert "fastowner/new" in text(page, "#pick-list") and "slowowner/old" not in text(page, "body") and page.locator("#pick-list li").count() == 1
    context.close()


def paged_jobs(context, total, run_id=101, fail_once_page=None):
    """The jobs of a run as GitHub gives them: a hundred to a page, `total_count` for all. Returns the list of pages asked for.
    With `fail_once_page` that page answers 500 the first time it is asked for."""
    asked, failed = [], []

    def handle(route, request):
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=PREFLIGHT)
        found = re.search(r"[?&]page=(\d+)", request.url)
        n = int(found.group(1)) if found else 1
        asked.append(n)
        if n == fail_once_page and not failed:
            failed.append(n)
            return route.fulfill(status=500, headers=CORS, content_type="application/json", body='{"message":"boom"}')
        jobs = [{"id": 5000 + i, "name": f"shard-{i}", "status": "completed", "conclusion": "success", "started_at": iso(500), "completed_at": iso(400), "steps": []}
                for i in range((n - 1) * 100, min(total, n * 100))]
        route.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps({"total_count": total, "jobs": jobs}))
    context.route(f"https://api.github.com/repos/{REPO}/actions/runs/{run_id}/jobs**", handle)
    return asked


def test_a_run_with_more_than_a_hundred_jobs_is_read_page_by_page(browser):
    context, page = new_page(browser)
    asked = paged_jobs(context, 150)
    page.goto(PAGE_URL)
    loaded(page)
    assert "150 job" in text(page, "#home-runs .pipe .total") and sorted(set(asked)) == [1, 2]
    assert page.locator("#home-runs .pipe p.bad").count() == 0                              # nothing is missing, so nothing is said to be
    context.close()


def test_the_jobs_of_a_finished_run_are_read_page_by_page_when_its_row_is_opened(browser):
    context, page = new_page(browser)
    asked = paged_jobs(context, 620, run_id=90)
    page.goto(PAGE_URL)
    loaded(page)
    open_tab(page, "repos")
    wait(page, "document.querySelector('#repo-grid .repo h3 a') !== null")
    row = page.locator("details.wf", has_text="Nightly")
    assert asked == []                                                                      # nothing is read until it is opened
    row.locator("summary").click()
    wait(page, "document.querySelectorAll('details.wf[open] .total').length === 1")
    assert "500 job" in row.locator(".total").inner_text() and sorted(set(asked)) == [1, 2, 3, 4, 5]
    assert "120 job nữa chưa đọc được" in row.locator("p.bad").inner_text()                 # the same note as on Home, for the other way a run's jobs are read
    context.close()


def paged_list(context, pattern, total, item):
    """A list GitHub gives a hundred at a time, `total` long; `item(i)` makes the i-th entry. Returns the list of pages asked for."""
    asked = []

    def handle(route, request):
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=PREFLIGHT)
        found = re.search(r"[?&]page=(\d+)", request.url)
        n = int(found.group(1)) if found else 1
        asked.append(n)
        route.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps([item(i) for i in range((n - 1) * 100, min(total, n * 100))]))
    context.route(pattern, handle)
    return asked


def a_pull(i):
    return {"number": 100 + i, "title": f"PR {i}", "head": {"ref": f"feat/{i}", "repo": {"full_name": REPO}}, "user": {"login": "me", "type": "User"}, "draft": False, "updated_at": iso(60), "created_at": iso(60)}


def test_a_repository_with_more_than_a_hundred_open_pull_requests_is_read_page_by_page(browser):
    context, page = new_page(browser)
    asked = paged_list(context, f"https://api.github.com/repos/{REPO}/pulls**", 250, a_pull)
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    card = text(page, "#repo-grid .repo")
    assert "PR mở: 250" in card and "PR mở: 250+" not in card and sorted(set(asked)) == [1, 2, 3] and "Issue mở: 0" in card         # all read: exact counts
    context.close()


def test_pull_requests_past_the_limit_are_not_counted_as_if_they_were_all_there(browser):
    context, page = new_page(browser)
    asked = paged_list(context, f"https://api.github.com/repos/{REPO}/pulls**", 400, a_pull)
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    card = text(page, "#repo-grid .repo")
    assert "PR mở: 300+" in card and "Issue mở: ?" in card and "chỉ đọc 300 PR mới nhất" in card and sorted(set(asked)) == [1, 2, 3]       # a fourth page is never asked for
    context.close()


def test_a_pull_request_list_that_could_not_be_read_is_not_counted_as_an_empty_one(browser):
    context, page = new_page(browser)
    page.gh.routes[f"/repos/{REPO}/pulls"] = (500, {"message": "boom"})
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    card = text(page, "#repo-grid .repo")
    assert "PR mở: ?" in card and "Issue mở: ?" in card and "PR mở: 0" not in card and "Không đọc được PR" in card         # the repository says 3 open issues-and-PRs: no number can be taken from it
    context.close()


def test_a_pull_request_list_that_was_read_and_is_empty_gives_exact_counts(browser):
    context, page = new_page(browser)
    page.gh.routes[f"/repos/{REPO}/pulls"] = (200, [])
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    card = text(page, "#repo-grid .repo")
    assert "PR mở: 0" in card and "Issue mở: 3" in card and "PR mở: ?" not in card and "Không đọc được PR" not in card
    context.close()


def test_a_repository_whose_workflow_list_could_not_be_read_is_not_shown_as_having_no_workflows(browser):
    context, page = new_page(browser, repos=(REPO, "demo/two"))
    page.gh.routes.update(app_routes("demo/two"))
    page.gh.routes["/repos/demo/two/actions/workflows"] = (200, {"workflows": []})                      # read, and empty: the count must tell the two repositories apart
    page.gh.routes[f"/repos/{REPO}/actions/workflows"] = (500, {"message": "boom"})
    page.gh.routes[f"/repos/{REPO}/pulls"] = (200, [])                                                  # no agent pull request waiting for a look: the inbox holds the one thing
    page.gh.routes["/repos/demo/two/pulls"] = (200, [])
    page.goto(PAGE_URL)
    wait(page, "document.querySelector('#home-runs .empty') !== null && document.querySelector('#home-runs .empty').textContent.includes('chưa đọc được')")
    assert "1 repo chưa đọc được danh sách workflow" in text(page, "#home-runs")                       # not "no tasks" and nothing else
    assert text(page, "#inbox-count") == "1"
    open_tab(page, "inbox")
    assert "Không đọc được danh sách workflow" in text(page, "#inbox-list") and REPO in text(page, "#inbox-list")
    repos(page)
    card = page.locator("#repo-grid .repo", has_text=REPO).inner_text()
    assert "Chưa đọc được workflow" in card and "chưa có workflow" not in card.lower()                  # not the sentence that says it has none, in any case of letters
    assert "chưa có workflow" in page.locator("#repo-grid .repo", has_text="demo/two").inner_text().lower()       # the other one has none, and says so
    open_tab(page, "health")
    rows = page.locator("#health-rows .healthrow", has_text=REPO).inner_text()
    assert "? workflow" in rows and "0 workflow" in page.locator("#health-rows .healthrow", has_text="demo/two").inner_text()
    open_tab(page, "board")
    assert "1 repo chưa đọc được danh sách workflow" in text(page, "#board-detail")
    context.close()


def test_a_repository_whose_workflow_list_was_read_and_is_empty_says_it_has_none(browser):
    context, page = new_page(browser)
    page.gh.routes[f"/repos/{REPO}/actions/workflows"] = (200, {"workflows": []})
    page.gh.routes[f"/repos/{REPO}/pulls"] = (200, [])
    page.goto(PAGE_URL)
    wait(page, "document.querySelector('#home-runs .empty') !== null")
    assert text(page, "#home-runs .empty") == "Không có task nào đang chạy hoặc đang chờ." and page.locator("#inbox-count").is_hidden()
    repos(page)
    assert "Chưa có workflow" in text(page, "#repo-grid .repo") and "Chưa đọc được workflow" not in text(page, "#repo-grid .repo")
    context.close()


def test_loads_under_way_or_queued_when_the_demo_starts_ask_nothing_more(browser):
    names = ("demo/a", "demo/b", "demo/c", "demo/d")                                                    # three are loaded at a time: the fourth waits its turn
    context, page = new_page(browser, repos=names)
    for name in names:
        page.gh.routes.update(app_routes(name))
    held = []
    for name in names[:3]:
        context.route(f"https://api.github.com/repos/{name}", lambda route, request: route.fulfill(status=204, headers=PREFLIGHT) if request.method == "OPTIONS" else held.append((route, request.url)))
    page.goto(PAGE_URL)
    deadline = time.monotonic() + 8
    while len(held) < 3 and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert len(held) == 3                                                                               # the first look is under way: the repository itself of three is awaited
    quiet(page)
    open_tab(page, "settings")
    page.click("#demo")
    wait(page, "!document.getElementById('banner').hidden")
    mark = len(page.gh.requests)
    for route, url in held:
        name = url.split("/repos/")[1]
        route.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps(page.gh.routes[f"/repos/{name}"][1]))
    page.wait_for_timeout(300)
    quiet(page)
    later = [r["clean"] for r in page.gh.requests[mark:]]
    assert later == [] and not [r for r in page.gh.requests if "demo/d" in r["clean"]]                 # the answers that came late started nothing, and the fourth was never started
    context.close()


def a_repo(i):
    return {"full_name": f"bigowner/r{i}", "owner": {"login": "bigowner"}, "private": False, "pushed_at": iso(60)}


def test_the_repository_picker_reads_further_pages_up_to_five(browser):
    for total, shown, pages, said in ((230, 230, [1, 2, 3], False), (600, 500, [1, 2, 3, 4, 5], True)):
        context, page = new_page(browser)
        asked = paged_list(context, "https://api.github.com/users/bigowner/repos**", total, a_repo)
        page.goto(PAGE_URL)
        open_tab(page, "settings")
        page.click("#pick")
        page.fill("#pick-owner", "bigowner")
        page.click("#pick-load")
        wait(page, f"document.querySelectorAll('#pick-list li').length === {shown}")
        assert sorted(set(asked)) == pages and ("500 repo đẩy gần nhất" in text(page, "#link-msg")) is said, (total, asked)
        context.close()


def test_a_workflow_file_with_a_space_in_its_name_is_read_and_linked_with_the_name_encoded(browser):
    context, page = new_page(browser)
    base = f"/repos/{REPO}"
    page.gh.routes[f"{base}/actions/workflows"] = (200, {"workflows": [{"id": 301, "name": "release", "path": ".github/workflows/release candidate #1.yml", "state": "active"}]})
    page.gh.routes[f"{base}/actions/runs"] = (200, {"workflow_runs": []})
    page.gh.routes[f"{base}/actions/workflows/301/runs"] = (200, {"workflow_runs": []})
    page.gh.routes[f"{base}/contents/.github/workflows/release%20candidate%20%231.yml"] = (200, "on:\n  workflow_dispatch:\n  schedule:\n    - cron: '0 18 * * *'\n")
    page.goto(PAGE_URL)
    repos(page)
    wait(page, "document.querySelector('.sched') !== null")
    assert "hằng ngày 18:00 UTC" in text(page, ".sched")
    assert page.get_attribute("details.wf a >> text=Xem file workflow", "href") == "https://github.com/demo/app/blob/HEAD/.github/workflows/release%20candidate%20%231.yml"
    open_tab(page, "home")
    assert page.get_attribute("#ready-now a", "href") == "https://github.com/demo/app/actions/workflows/release%20candidate%20%231.yml"          # the other link built from a path
    context.close()


def test_the_word_workflow_dispatch_in_a_script_does_not_make_a_workflow_startable_by_hand(browser):
    for yaml, ready in (("on:\n  workflow_dispatch:\n", "1"), ("on:\n  push:\njobs:\n  a:\n    steps:\n      - run: echo workflow_dispatch\n", "0")):
        context, page = new_page(browser)
        page.gh.routes[f"/repos/{REPO}/contents/.github/workflows/weekly.yml"] = (200, yaml)
        page.goto(PAGE_URL)
        loaded(page)
        assert text(page, "#ready-count") == ready and ("Weekly scan" in text(page, "#ready-now")) is (ready == "1"), yaml
        context.close()


def test_the_next_run_of_a_cron_that_fires_often_is_in_the_list_of_what_is_coming(browser):
    context, page = new_page(browser)
    page.gh.routes[f"/repos/{REPO}/contents/.github/workflows/weekly.yml"] = (200, "on:\n  schedule:\n    - cron: '*/30 * * * *'\n")
    page.goto(PAGE_URL)
    loaded(page)
    coming = text(page, "#ready-now")
    assert "Weekly scan" in coming and "(lặp lại)" in coming                                      # not listed run by run, but its next run is there
    context.close()


def test_a_workflow_file_that_could_not_be_read_is_not_taken_for_one_that_declares_nothing(browser):
    context, page = new_page(browser)
    page.gh.routes[f"/repos/{REPO}/contents/.github/workflows/nightly.yml"] = (500, {"message": "boom"})
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    assert "1 workflow chưa đọc được file (GitHub không trả lời hoặc token thiếu quyền Contents: Read)" in text(page, "#repo-grid .repo")
    context.close()


def test_workflow_files_are_read_up_to_two_dozen_and_the_rest_are_owned_up_to(browser):
    context, page = new_page(browser)
    base = f"/repos/{REPO}"
    page.gh.routes[f"{base}/actions/workflows"] = (200, {"workflows": [{"id": 200 + i, "name": f"wf-{i}", "path": f".github/workflows/wf-{i}.yml", "state": "active"} for i in range(26)]})
    page.gh.routes[f"{base}/actions/runs"] = (200, {"workflow_runs": []})
    for i in range(26):
        page.gh.routes[f"{base}/actions/workflows/{200 + i}/runs"] = (200, {"workflow_runs": []})
        page.gh.routes[f"{base}/contents/.github/workflows/wf-{i}.yml"] = (200, "on:\n  push:\n")
    page.goto(PAGE_URL)
    repos(page)
    wait(page, "document.querySelector('#repo-grid .repo .small.bad') !== null")
    assert len([p for p in page.gh.paths() if re.fullmatch(rf"{base}/contents/\.github/workflows/wf-\d+\.yml", p)]) == 24
    assert "2 workflow chưa đọc được file (quá giới hạn 24 file)" in text(page, "#repo-grid .repo")
    context.close()


def test_a_workflow_with_a_very_long_file_name_still_has_its_schedule_read(browser):
    context, page = new_page(browser)
    base = f"/repos/{REPO}"
    path = ".github/workflows/" + "a" * 110 + ".yml"
    page.gh.routes[f"{base}/actions/workflows"] = (200, {"workflows": [{"id": 300, "name": "long-path", "path": path, "state": "active"}]})
    page.gh.routes[f"{base}/actions/runs"] = (200, {"workflow_runs": []})
    page.gh.routes[f"{base}/actions/workflows/300/runs"] = (200, {"workflow_runs": []})
    page.gh.routes[f"{base}/contents/{path}"] = (200, "on:\n  schedule:\n    - cron: '0 18 * * *'\n")
    page.goto(PAGE_URL)
    repos(page)
    wait(page, "document.querySelector('.sched') !== null")
    assert "hằng ngày 18:00 UTC" in text(page, ".sched") and f"{base}/contents/{path}" in page.gh.paths()
    context.close()


def test_the_workflow_list_is_read_page_by_page_up_to_five_pages_and_says_when_it_stops(browser):
    for total, pages, shown, said, fail_page in ((250, [1, 2, 3], 250, None, None), (600, [1, 2, 3, 4, 5], 500, "chỉ đọc 500 workflow", None), (250, [1, 2, 3], 200, "chỉ đọc 200 workflow", 3)):
        context, page = new_page(browser)
        asked = []

        def handle(route, request, total=total, asked=asked, fail_page=fail_page):
            if request.method == "OPTIONS":
                return route.fulfill(status=204, headers=PREFLIGHT)
            found = re.search(r"[?&]page=(\d+)", request.url)
            n = int(found.group(1)) if found else 1
            asked.append(n)
            if n == fail_page:
                return route.fulfill(status=500, headers=CORS, content_type="application/json", body='{"message":"boom"}')
            items = [{"id": 1000 + i, "name": f"wf-{i}", "path": f".github/workflows/w{i}.yml", "state": "active"} for i in range((n - 1) * 100, min(total, n * 100))]
            route.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps({"total_count": total, "workflows": items}))
        context.route(re.compile(rf"https://api\.github\.com/repos/{REPO}/actions/workflows\?.*"), handle)
        page.gh.routes[f"/repos/{REPO}/actions/runs"] = (200, {"workflow_runs": []})
        page.goto(PAGE_URL)
        repos(page)
        wait(page, "document.querySelector('#repo-grid .repo .small.bad') !== null")
        card = text(page, "#repo-grid .repo")
        assert sorted(set(asked)) == pages and f"và {shown - 6} workflow khác" in card, (total, asked)
        assert (said in card) if said else ("Repo có thể còn workflow nữa" not in card), (total, said)       # a list read to its end says nothing; one that was cut, or whose page failed, says so
        context.close()


def test_a_workflow_history_that_could_not_be_read_is_not_passed_off_as_never_having_run(browser):
    context, page = new_page(browser)
    page.gh.routes[f"/repos/{REPO}/actions/workflows/3/runs"] = (500, {"message": "boom"})            # the weekly scan lives only in its own history: GitHub fails it
    page.goto(PAGE_URL)
    loaded(page)
    repos(page)
    assert "1 workflow chưa đọc được lịch sử (GitHub không trả lời)" in text(page, "#repo-grid .repo")
    context.close()


def test_workflows_crowded_out_of_the_repository_list_are_read_up_to_two_dozen_and_the_rest_are_owned_up_to(browser):
    context, page = new_page(browser)
    base = f"/repos/{REPO}"
    page.gh.routes[f"{base}/actions/workflows"] = (200, {"workflows": [{"id": 200 + i, "name": f"wf-{i}", "path": f".github/workflows/wf-{i}.yml", "state": "active"} for i in range(26)]})
    page.gh.routes[f"{base}/actions/runs"] = (200, {"workflow_runs": []})                      # the repository-wide list shows none of them: every workflow is crowded out
    for i in range(26):
        page.gh.routes[f"{base}/actions/workflows/{200 + i}/runs"] = (200, {"workflow_runs": [run_of(9000 + i, 200 + i, 1, "completed", "failure" if i == 19 else "success", 3600, head=REPO)]})
    page.goto(PAGE_URL)
    wait(page, "!document.getElementById('inbox-count').hidden")
    open_tab(page, "inbox")
    assert "wf-19 #1" in text(page, "#inbox-list")                                              # the nineteenth: past the old limit of eight, its failure is not hidden
    assert len([p for p in page.gh.paths() if re.fullmatch(rf"{base}/actions/workflows/\d+/runs", p)]) == 24
    repos(page)
    assert "2 workflow chưa đọc được lịch sử" in text(page, "#repo-grid .repo")                 # the two beyond the limit are said to be unread, not shown as never having run
    context.close()


def test_a_page_of_jobs_that_failed_is_asked_for_again_after_half_a_minute(browser):
    context, page = new_page(browser)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    asked = paged_jobs(context, 250, run_id=90, fail_once_page=3)
    page.goto(PAGE_URL)
    loaded(page)
    open_tab(page, "repos")
    wait(page, "document.querySelector('#repo-grid .repo h3 a') !== null")
    row = page.locator("details.wf", has_text="Nightly")
    open_row = "document.querySelector('details.wf[open]')"
    row.locator(":scope > summary").click()
    wait(page, f"{open_row} !== null && {open_row}.querySelector('.total') !== null")
    assert "200 job" in row.locator(".total").inner_text() and "50 job nữa chưa đọc được" in row.locator("p.bad").inner_text()           # the third page failed
    row.locator(":scope > summary").click()
    wait(page, f"{open_row} === null")
    row.locator(":scope > summary").click()                                                                                                          # folded and opened again within the half minute: what was read is shown
    wait(page, f"{open_row} !== null")
    assert "200 job" in row.locator(".total").inner_text() and asked.count(3) == 1
    page.clock.run_for(31_000)
    row.locator(":scope > summary").click()
    wait(page, f"{open_row} === null")
    row.locator(":scope > summary").click()                                                                                                          # after it the missing page is asked for again
    wait(page, f"{open_row} !== null && {open_row}.querySelector('.total') !== null && {open_row}.querySelector('.total').textContent.includes('250 job')")
    assert asked.count(3) == 2 and row.locator("p.bad").count() == 0
    context.close()


def test_a_run_with_far_more_jobs_is_read_up_to_five_pages_and_says_how_many_are_left_out(browser):
    context, page = new_page(browser)
    asked = paged_jobs(context, 700)
    page.goto(PAGE_URL)
    loaded(page)
    assert "500 job" in text(page, "#home-runs .pipe .total") and sorted(set(asked)) == [1, 2, 3, 4, 5]             # a sixth page is never asked for
    assert "200 job nữa chưa đọc được" in text(page, "#home-runs .pipe p.bad")
    context.close()


# ---- repositories ---------------------------------------------------------------------------------------------------------------

def test_a_wrong_name_sends_nothing_and_a_right_one_is_linked_saved_and_removable(ui):
    open_tab(ui, "settings")
    before = len(ui.gh.requests)
    ui.fill("#add-repo", "not a repo")
    ui.click("#add")
    assert "chủ/tên" in text(ui, "#link-msg") and len(ui.gh.requests) == before
    ui.fill("#add-repo", "https://github.com/demo/other.git")
    ui.press("#add-repo", "Enter")
    wait(ui, "document.querySelectorAll('.chip').length === 2")
    deadline = time.monotonic() + 6
    while "/repos/demo/other" not in ui.gh.paths() and time.monotonic() < deadline:
        ui.wait_for_timeout(50)
    assert "/repos/demo/other" in ui.gh.paths()
    open_tab(ui, "repos")
    wait(ui, "document.querySelectorAll('#repo-grid .repo').length === 2 && document.querySelector('#repo-grid .repo p.bad') !== null")
    assert "Không thấy repo" in text(ui, "#repo-grid .repo p.bad")
    open_tab(ui, "settings")
    ui.fill("#add-repo", "DEMO/app")
    ui.click("#add")
    assert "đã được liên kết" in text(ui, "#link-msg") and ui.locator(".chip").count() == 2            # the same repo in other letters is the same repo
    assert ui.evaluate(f"() => JSON.parse(window.localStorage.getItem('{STORE_KEY}')).repos") == ["demo/app", "demo/other"]
    ui.reload()
    open_tab(ui, "settings")
    wait(ui, "document.querySelectorAll('.chip').length === 2")                                          # it was saved
    ui.click("button[aria-label='Bỏ liên kết demo/other']")
    wait(ui, "document.querySelectorAll('.chip').length === 1")
    assert ui.evaluate(f"() => JSON.parse(window.localStorage.getItem('{STORE_KEY}')).repos") == ["demo/app"]
    ui.reload()
    open_tab(ui, "settings")
    wait(ui, "document.querySelectorAll('.chip').length === 1")
    assert text(ui, ".chip").startswith("demo/app")


@pytest.mark.parametrize("status,body,words", [
    (403, {"message": "You have exceeded a secondary rate limit"}, "giới hạn tốc độ"), (429, {"message": "Too many requests: rate limit"}, "giới hạn tốc độ"),
    (403, {"message": "Resource not accessible by personal access token"}, "token thiếu quyền"), (401, {"message": "Bad credentials"}, "Token sai"), (500, None, "mã 500")])
def test_each_refusal_is_told_apart(browser, status, body, words):
    context, page = new_page(browser)
    page.gh.routes = {f"/repos/{REPO}": (status, body)}
    page.goto(PAGE_URL)
    open_tab(page, "repos")
    wait(page, "document.querySelector('.repo p.bad') !== null")
    assert words in text(page, ".repo p.bad") and page.locator(".repo button", has_text="Thử lại").count() == 1
    context.close()


def test_a_refused_read_with_no_quota_left_says_when_it_comes_back(browser):
    context, page = new_page(browser)
    page.gh.remaining = 0
    page.gh.routes = {f"/repos/{REPO}": (403, {"message": "API rate limit exceeded"})}
    page.goto(PAGE_URL)
    open_tab(page, "repos")
    wait(page, "document.querySelector('.repo p.bad') !== null")
    assert "Hết hạn mức GitHub" in text(page, ".repo p.bad") and "token chỉ-đọc" in text(page, ".repo p.bad")
    context.close()


def test_repos_can_be_picked_from_an_account(browser):
    context, page = new_page(browser, repos=())
    page.gh.routes = {"/users/demo/repos": (200, [{"full_name": "demo/app", "owner": {"login": "demo"}, "private": False, "pushed_at": iso(60)},
                                                  {"full_name": "demo/tool", "owner": {"login": "demo"}, "private": False, "pushed_at": iso(600)},
                                                  {"full_name": "bad name/x y", "owner": {"login": "demo"}}]), **{k: v for k, v in app_routes("demo/tool").items()}}
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.click("#pick")
    page.fill("#pick-owner", "demo")
    page.click("#pick-load")
    wait(page, "document.querySelectorAll('#pick-list input').length === 2")                              # the malformed name is not offered
    page.check("#pick-list label >> nth=1 >> input")
    page.click("#pick-add")
    wait(page, "document.querySelectorAll('.chip').length === 1")
    assert text(page, ".chip").startswith("demo/tool")
    context.close()


def test_the_list_can_be_exported_and_added_from_text(ui):
    open_tab(ui, "settings")
    ui.click("details.io summary")
    ui.click("#export")
    assert json.loads(ui.input_value("#io")) == {"repos": ["demo/app"]}
    ui.fill("#io", "demo/a\nhttps://github.com/demo/b, nonsense")
    ui.click("#import")
    wait(ui, "document.querySelectorAll('.chip').length === 3")
    assert "bỏ qua 1 mục không hợp lệ" in text(ui, "#link-msg")


# ---- cost: the page asks GitHub as little as it can --------------------------------------------------------------------------------

def test_a_second_look_asks_only_if_changed_and_a_not_modified_answer_changes_nothing(ui):
    before = ui.inner_text("#home-runs")
    open_tab(ui, "settings")
    count, answered = len(ui.gh.requests), ui.gh.not_modified
    ui.click("#refresh")
    deadline = time.monotonic() + 8
    while len(ui.gh.requests) < count + 12 and time.monotonic() < deadline:               # repo, runs, workflows, pulls, jobs, three workflow files, agents, three histories
        ui.wait_for_timeout(100)
    ui.wait_for_timeout(300)
    asked = ui.gh.conditional(count)
    assert len(asked) == 12 and all(asked)                                                 # every one of them was conditional ...
    assert ui.gh.not_modified - answered == len(asked)                                     # ... and every one was answered "not modified"
    open_tab(ui, "home")
    assert re.sub(r"\d+ (giây|phút)", "", ui.inner_text("#home-runs")) == re.sub(r"\d+ (giây|phút)", "", before) and ui.query_selector("#home-runs .fbox") is not None


def test_out_of_quota_the_page_stops_sending_until_the_reset(browser):
    repos_ = [f"demo/r{i}" for i in range(6)]
    context, page = new_page(browser, repos=repos_)
    page.gh.remaining = 0
    page.gh.routes = {f"/repos/{r}": (403, {"message": "API rate limit exceeded"}) for r in repos_}
    page.goto(PAGE_URL)
    open_tab(page, "repos")
    wait(page, "document.querySelectorAll('.repo p.bad').length === 6")
    assert len(page.gh.requests) == 12                                            # three repos at a time, four calls each; the other three were never sent
    assert all("Hết hạn mức GitHub" in t for t in page.eval_on_selector_all(".repo p.bad", "els => els.map(e => e.textContent)"))
    open_tab(page, "settings")
    page.fill("#token", TOKEN)                                                   # a new token is a new quota: the page asks again
    page.press("#token", "Tab")
    wait(page, "document.getElementById('connect-msg').className.includes('ok')")
    page.wait_for_timeout(300)
    assert len(page.gh.requests) > 12
    context.close()


SECONDARY = {"message": "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."}


def test_after_a_secondary_rate_limit_the_page_waits_as_long_as_github_asks_and_then_goes_on(browser):
    context, page = new_page(browser, interval=30)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('live-text').textContent === 'live'")
    quiet(page)
    good = page.gh.routes
    page.gh.routes = {**good, f"/repos/{REPO}/actions/runs": (403, SECONDARY)}              # a secondary limit on one call: 403, the hourly count is far from empty, Retry-After says how long
    page.gh.extra = {"retry-after": "300"}                                                  # five minutes: more than the minute the page would take for itself
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    assert "chờ đến" in text(page, "#rate") and "thứ cấp" in text(page, "#rate")
    seen = quiet(page)

    def card():                                                                             # a repository that was read shows its counts; one that could not be read does not
        repos(page)
        return text(page, "#repo-grid .repo")

    assert "PR mở" in card() and "Không đọc được các lượt chạy" not in card()               # the repository is on the page as it was read in full, not as the partial look the wait cut short
    open_tab(page, "settings")
    page.click("#refresh")                                                                  # the person asks again during the wait: nothing is sent, and what is on the page stays
    page.wait_for_timeout(300)
    assert len(page.gh.requests) == seen and "PR mở" in card()
    page.gh.routes, page.gh.extra = good, {}                                                # GitHub is fine again, but the wait is not over
    page.clock.run_for(100_000)                                                             # three timer ticks, past a minute and inside the five
    page.wait_for_timeout(400)
    assert len(page.gh.requests) == seen and text(page, "#live-text") == "tạm dừng" and "PR mở" in card()
    page.clock.run_for(250_000)                                                             # past it (a tick lands every 30 seconds): the timer works again by itself, and the indicator says so
    deadline = time.monotonic() + 6
    while len(page.gh.requests) == seen and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert len(page.gh.requests) > seen
    wait(page, "document.getElementById('live-text').textContent === 'live'")
    open_tab(page, "settings")
    assert "chờ đến" not in text(page, "#rate")
    context.close()


def test_a_look_at_many_repositories_stops_starting_new_ones_once_github_asks_to_wait(browser):
    names = [f"demo/r{i}" for i in range(6)]
    context, page = new_page(browser, repos=names)
    page.gh.routes = {f"/repos/{r}": (403, SECONDARY) for r in names}
    page.gh.extra = {"retry-after": "60"}
    page.goto(PAGE_URL)
    open_tab(page, "repos")
    wait(page, "document.querySelectorAll('.repo p.bad').length === 6")
    assert len(page.gh.requests) == 12                                                      # three repositories at a time, four calls each; the other three were never sent
    notes = page.eval_on_selector_all(".repo p.bad", "els => els.map(e => e.textContent)")
    assert sum("chờ đến" in t and "chưa được đọc" in t for t in notes) == 3                 # the three that were never started say why, with the time asking is allowed again
    context.close()


def test_repositories_that_were_read_keep_their_data_while_github_asks_to_wait(browser):
    names = [f"demo/r{i}" for i in range(6)]                                                 # three are loaded at a time: three are still queued when the first answers come back
    context, page = new_page(browser, repos=names)
    for name in names:
        page.gh.routes.update(app_routes(name))
    page.goto(PAGE_URL)
    repos(page)
    wait(page, "[...document.querySelectorAll('#repo-grid .repo')].filter((e) => e.textContent.includes('PR mở')).length === 6")
    quiet(page)
    page.gh.routes = {path: (403, SECONDARY) for path in page.gh.routes}
    page.gh.extra = {"retry-after": "300"}
    open_tab(page, "settings")
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    quiet(page)
    repos(page)
    cards = page.locator("#repo-grid .repo")
    assert cards.count() == 6 and all("PR mở" in cards.nth(i).inner_text() for i in range(6))     # every card still shows what was read before, the queued ones included
    assert page.locator("#repo-grid .repo p.bad").count() == 0
    context.close()


def limit_with_one_request(page):
    """A second secondary limit that comes from a single request (the steps of a finished run, asked for by opening its row), so that no other answer races it."""
    page.gh.routes[f"/repos/{REPO}/actions/runs/90/jobs"] = (403, SECONDARY)
    repos(page)
    page.locator("details.wf", has_text="Nightly").locator(":scope > summary").click()
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")


def test_a_success_that_was_in_flight_when_the_wait_began_does_not_restart_the_backoff(browser):
    context, page = new_page(browser)                                                       # manual refresh only: a timer tick would add answers of its own
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('connect-msg').className.includes('ok')")
    quiet(page)
    held = []
    context.route(lambda url: url.endswith(f"/repos/{REPO}/actions/runs?per_page=40"),
                  lambda route, request: route.fulfill(status=204, headers=PREFLIGHT) if request.method == "OPTIONS" else held.append(route))
    good_runs = page.gh.routes[f"/repos/{REPO}/actions/runs"][1]
    page.gh.routes[f"/repos/{REPO}/pulls"] = (403, SECONDARY)                                 # no Retry-After: a minute, then twice that
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    deadline = time.monotonic() + 6
    while not held and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert held                                                                               # a question asked before the wait is still unanswered
    held[0].fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps(good_runs))       # and now it is answered well
    page.wait_for_timeout(300)
    page.clock.run_for(61_000)                                                                # the minute is over
    wait(page, "document.getElementById('live-text').textContent !== 'tạm dừng'")
    held.clear()
    limit_with_one_request(page)                                                              # asked again after the wait, the limit comes back: now two minutes, not one
    page.clock.run_for(100_000)                                                               # past the minute, inside the two
    page.wait_for_timeout(300)
    assert text(page, "#live-text") == "tạm dừng"
    context.close()


def test_a_not_modified_answer_after_the_wait_restarts_the_backoff_like_any_good_answer(browser):
    context, page = new_page(browser)                                                       # manual refresh only: a timer tick would add answers of its own
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('connect-msg').className.includes('ok')")
    quiet(page)
    good = page.gh.routes[f"/repos/{REPO}/pulls"]
    page.gh.routes[f"/repos/{REPO}/pulls"] = (403, SECONDARY)                                # no Retry-After: a minute
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    quiet(page)
    page.clock.run_for(61_000)                                                               # the minute is over
    wait(page, "document.getElementById('live-text').textContent !== 'tạm dừng'")
    page.gh.routes[f"/repos/{REPO}/pulls"] = good
    count = len(page.gh.requests)
    page.click("#refresh")                                                                   # nothing changed: every question is asked "only if changed" and answered 304
    quiet(page)
    asked = page.gh.conditional(count)
    assert page.gh.not_modified > 0 and asked and sum(asked) >= 6                            # (and the 304s are the good answers that count)
    limit_with_one_request(page)                                                             # a new limit: a minute again, not two
    page.clock.run_for(70_000)
    wait(page, "document.getElementById('live-text').textContent !== 'tạm dừng'")
    context.close()


def test_repositories_that_were_read_keep_their_data_when_the_hourly_count_runs_out_in_the_middle_of_a_look(browser):
    names = [f"demo/r{i}" for i in range(6)]                                                 # three are loaded at a time: three are still queued when the count runs out
    context, page = new_page(browser, repos=names)
    for name in names:
        page.gh.routes.update(app_routes(name))
    page.goto(PAGE_URL)
    repos(page)
    wait(page, "[...document.querySelectorAll('#repo-grid .repo')].filter((e) => e.textContent.includes('PR mở')).length === 6")
    quiet(page)
    page.gh.remaining = 0                                                                    # the answers of the next look say the count is spent (the reset is ahead)
    open_tab(page, "settings")
    page.click("#refresh")
    quiet(page)
    repos(page)
    cards = page.locator("#repo-grid .repo")
    assert cards.count() == 6 and all("PR mở" in cards.nth(i).inner_text() for i in range(6))     # every card still shows what was read, the queued ones included: no card was turned into an error
    assert page.locator("#repo-grid .repo p.bad").count() == 0                                   # and none was replaced by the partial look the count cut short (it would warn of files and histories not read)
    context.close()


def test_a_look_that_lost_requests_to_the_wait_does_not_replace_what_was_read_in_full(browser):
    context, page = new_page(browser)
    page.goto(PAGE_URL)
    loaded(page)
    quiet(page)
    assert "build" in text(page, "#home-runs .pipe")                                          # the first look read everything, the steps of the run in progress included
    page.gh.routes[f"/repos/{REPO}/pulls"] = (403, SECONDARY)                                 # one answer of the next look starts a wait; the calls that come after it are refused
    page.gh.extra = {"retry-after": "300"}
    open_tab(page, "settings")
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    quiet(page)
    open_tab(page, "home")
    assert "build" in text(page, "#home-runs .pipe") and "Chưa đọc được các bước" not in text(page, "#home-runs .pipe")
    repos(page)
    card = text(page, "#repo-grid .repo")
    assert "PR mở: 1" in card and "Không đọc được PR" not in card and "chưa đọc được lịch sử" not in card         # the model read in full is still the one on the page, with no warning from the partial look
    context.close()


def test_the_answer_that_begins_a_wait_counts_as_a_lost_request_even_when_it_is_the_last_of_the_look(browser):
    context, page = new_page(browser)
    page.goto(PAGE_URL)
    loaded(page)
    quiet(page)
    assert "build" in text(page, "#home-runs .pipe")
    page.gh.routes[f"/repos/{REPO}/actions/runs/101/jobs"] = (403, SECONDARY)               # the steps of the one run in progress are the last thing the look asks for
    page.gh.extra = {"retry-after": "300"}
    open_tab(page, "settings")
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    quiet(page)
    open_tab(page, "home")
    assert "build" in text(page, "#home-runs .pipe") and "Chưa đọc được các bước" not in text(page, "#home-runs .pipe")       # the steps read before are still there
    context.close()


def test_the_answer_that_spends_the_hourly_count_counts_as_a_lost_request_even_when_it_is_the_last_of_the_look(browser):
    context, page = new_page(browser)
    page.goto(PAGE_URL)
    loaded(page)
    quiet(page)
    assert "build" in text(page, "#home-runs .pipe")
    jobs = f"/repos/{REPO}/actions/runs/101/jobs"
    page.gh.routes[jobs] = (403, {"message": "API rate limit exceeded"})                    # the last question of the look is the one that is refused for want of count
    page.gh.remaining_at[jobs] = 0
    open_tab(page, "settings")
    page.click("#refresh")
    quiet(page)
    page.wait_for_timeout(300)
    open_tab(page, "home")
    assert "build" in text(page, "#home-runs .pipe") and "Chưa đọc được các bước" not in text(page, "#home-runs .pipe")
    context.close()


def test_a_workflow_that_is_switched_off_and_never_ran_still_shows_its_triggers_as_blocked(browser):
    context, page = new_page(browser)
    workflows = page.gh.routes[f"/repos/{REPO}/actions/workflows"][1]["workflows"]
    page.gh.routes[f"/repos/{REPO}/actions/workflows"] = (200, {"workflows": workflows + [
        {"id": 4, "name": "Old job", "path": ".github/workflows/old.yml", "state": "disabled_manually"},
        {"id": 5, "name": "Gone job", "path": ".github/workflows/gone.yml", "state": "deleted"}]})
    page.gh.routes[f"/repos/{REPO}/contents/.github/workflows/old.yml"] = (200, "on:\n  schedule:\n    - cron: '0 5 * * *'\n  workflow_dispatch:\n")
    page.goto(PAGE_URL)
    loaded(page)
    assert [p for p in page.gh.paths() if p.endswith("/old.yml")]                             # its file was read
    assert not [p for p in page.gh.paths() if p.endswith("/gone.yml")]                        # the file of a deleted workflow is not asked for: it is not there
    board(page)
    nodes = page.eval_on_selector_all("#wavegrid button.node", "els => els.map(e => e.textContent)")
    assert any("CRON" in t and "Tắt" in t and "05:00" in t for t in nodes)                  # the cron it declares, as a blocked trigger (the manual trigger is one node of the repository that all its workflows share)
    open_tab(page, "home")
    assert "Old job" not in text(page, "#ready-now")                                          # it declares workflow_dispatch but cannot be started: not offered
    open_tab(page, "schedule")
    assert "Old job" not in text(page, "#view-schedule")                                       # and it runs nothing: it is not in the plan
    repos(page)
    assert "kế tiếp" not in page.locator("details.wf", has_text="Old job").inner_text()
    context.close()


def test_adding_many_repositories_at_once_reads_three_at_a_time(browser):
    names = [f"demo/b{i}" for i in range(8)]
    context, page = new_page(browser, repos=())
    for name in names:
        page.gh.routes.update(app_routes(name))
    held = []
    for name in names:
        context.route(f"https://api.github.com/repos/{name}", lambda route, request: route.fulfill(status=204, headers=PREFLIGHT) if request.method == "OPTIONS" else held.append((route, request.url)))
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.click("details.io summary")
    page.fill("#io", "\n".join(names))
    page.click("#import")
    deadline = time.monotonic() + 6
    while len(held) < 3 and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    page.wait_for_timeout(500)
    assert len(held) == 3                                                                    # three repositories asked for at once; the other five wait their turn, not eight at once
    for _ in range(8):                                                                       # let them through one wave at a time: no more than three are ever waiting together
        batch = list(held)
        held.clear()
        for route, url in batch:
            route.fulfill(status=200, headers=CORS, content_type="application/json", body=json.dumps(page.gh.routes[f"/repos/{url.split('/repos/')[1]}"][1]))
        page.wait_for_timeout(400)
        assert len(held) <= 3
        if not held:
            break
    open_tab(page, "repos")
    wait(page, "[...document.querySelectorAll('#repo-grid .repo')].filter((e) => e.textContent.includes('PR mở')).length === 8")       # and all eight were read in the end
    context.close()


def test_a_repository_filter_whose_repository_is_gone_stops_filtering(browser):
    context, page = new_page(browser, repos=(REPO, "demo/two"))
    page.gh.routes.update(app_routes("demo/two"))
    page.goto(PAGE_URL)
    wait(page, "document.querySelectorAll('#home-runs .pipe').length === 2")
    board(page)
    wait(page, "document.querySelectorAll('#f-repo option').length === 3")
    page.select_option("#f-repo", "demo/two")
    wait(page, "document.getElementById('f-count').textContent.split(' / ')[0] !== document.getElementById('f-count').textContent.split(' / ')[1]")
    open_tab(page, "settings")
    page.click('[data-remove="demo/two"]')                                                   # the repository of the filter is unlinked
    board(page)
    wait(page, "document.querySelectorAll('#f-repo option').length === 2")
    shown, total = (int(n) for n in text(page, "#f-count").split(" / "))
    assert page.input_value("#f-repo") == "" and shown == total and total > 0                # the selector says "all" and the board shows everything that is left, not nothing
    context.close()


def test_a_new_token_ends_the_wait_after_a_secondary_rate_limit(browser):
    context, page = new_page(browser, interval=30)
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('live-text').textContent === 'live'")
    quiet(page)
    good = page.gh.routes
    page.gh.routes = {path: (403, SECONDARY) for path in good}
    page.gh.extra = {"retry-after": "300"}
    page.click("#refresh")
    wait(page, "document.getElementById('live-text').textContent === 'tạm dừng'")
    seen = quiet(page)
    page.gh.routes, page.gh.extra = good, {}
    page.fill("#token", TOKEN + "2")                                                        # another reader: the wait was for the old one
    page.press("#token", "Tab")
    wait(page, "document.getElementById('live-text').textContent === 'live'")
    quiet(page)
    assert len(page.gh.requests) > seen and "chờ đến" not in text(page, "#rate")
    context.close()


def test_a_refusal_that_is_not_a_rate_limit_does_not_make_the_page_wait(browser):
    context, page = new_page(browser, interval=30)
    page.goto(PAGE_URL)
    loaded(page)
    open_tab(page, "settings")
    page.gh.routes = {path: (403, {"message": "Resource not accessible by personal access token"}) for path in page.gh.routes}
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('connect-msg').className.includes('ok')")
    quiet(page)
    assert text(page, "#live-text") == "live" and "chờ đến" not in text(page, "#rate")             # a permission problem is not a wait
    seen = len(page.gh.requests)
    page.click("#refresh")
    page.wait_for_timeout(500)
    assert len(page.gh.requests) > seen                                                      # and the next refresh is sent
    context.close()


def test_auto_refresh_pauses_when_the_quota_runs_low_and_goes_on_when_it_recovers(browser):
    context, page = new_page(browser, interval=30)
    page.clock.install(time=dt.datetime.now(dt.timezone.utc))
    page.gh.remaining = 100                                                      # of 5000: below the 5 % the page keeps back
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.fill("#token", TOKEN)
    page.press("#token", "Tab")
    wait(page, "document.getElementById('rate').textContent.includes('tạm dừng')")
    assert text(page, "#live-text") == "tạm dừng"
    seen = quiet(page)
    page.clock.run_for(65_000)
    page.wait_for_timeout(400)
    assert len(page.gh.requests) == seen                                         # two timer ticks, nothing sent
    page.gh.remaining, page.gh.reset = 4000, page.gh.reset + 3600                # the window ended: a new count under a later reset time (inside a window the count only goes down)
    page.click("#refresh")                                                       # the person asks: this goes through and reads the new quota
    wait(page, "document.getElementById('rate').textContent.includes('4.000')")
    assert "tạm dừng" not in text(page, "#rate") and text(page, "#live-text") == "live"
    seen = quiet(page)
    page.clock.run_for(31_000)
    deadline = time.monotonic() + 6
    while len(page.gh.requests) == seen and time.monotonic() < deadline:
        page.wait_for_timeout(100)
    assert len(page.gh.requests) > seen                                          # and the timer works again by itself
    context.close()


# ---- the shell: tabs, looks, demo, small screens, frames ------------------------------------------------------------------------------

def test_tabs_show_one_screen_at_a_time_and_work_from_the_keyboard(ui):
    assert [ui.get_attribute(f"#tab-{v}", "aria-selected") for v in VIEWS] == ["true"] + ["false"] * 7
    assert [ui.is_visible(f"#view-{v}") for v in VIEWS] == [True] + [False] * 7
    ui.focus("#tab-home")
    ui.keyboard.press("ArrowRight")
    assert ui.get_attribute("#tab-board", "aria-selected") == "true" and ui.is_visible("#view-board") and not ui.is_visible("#view-home")
    ui.keyboard.press("End")
    assert ui.get_attribute("#tab-settings", "aria-selected") == "true" and ui.is_visible("#view-settings")
    ui.keyboard.press("ArrowRight")                                                  # wraps round
    assert ui.get_attribute("#tab-home", "aria-selected") == "true"
    ui.keyboard.press("ArrowLeft")
    assert ui.get_attribute("#tab-settings", "aria-selected") == "true"
    ui.keyboard.press("Home")
    assert ui.get_attribute("#tab-home", "aria-selected") == "true" and ui.is_visible("#view-home")
    ui.keyboard.press("End")
    assert [ui.get_attribute(f"#tab-{v}", "tabindex") for v in VIEWS] == ["-1"] * 7 + ["0"]                  # only the current tab is a stop for Tab


def test_four_looks_and_three_modes_are_chosen_from_a_panel_and_remembered(browser):
    context, page = new_page(browser, scheme="dark")
    page.goto(PAGE_URL)
    root = lambda: page.evaluate("() => [document.documentElement.dataset.theme, document.documentElement.dataset.eff]")      # noqa: E731
    assert root() == ["neon", "dark"] and page.is_hidden("#theme-pop")
    page.click("#theme-btn")
    assert page.is_visible("#theme-pop") and page.get_attribute("#theme-btn", "aria-expanded") == "true"
    assert page.locator("#theme-pop [data-pick-theme]").count() == 4 and page.get_attribute("[data-pick-theme=neon]", "aria-pressed") == "true"
    page.click("[data-pick-theme=candy]")
    assert root() == ["candy", "dark"] and page.get_attribute("[data-pick-theme=candy]", "aria-pressed") == "true" and page.get_attribute("[data-pick-theme=neon]", "aria-pressed") == "false"
    page.click("[data-pick-mode=light]")
    assert root() == ["candy", "light"]
    page.click("[data-pick-mode=auto]")
    assert root() == ["candy", "dark"]                                                # auto follows the system
    page.keyboard.press("Escape")
    assert page.is_hidden("#theme-pop") and page.evaluate("document.activeElement.id") == "theme-btn"
    saved = page.evaluate(f"() => JSON.parse(window.localStorage.getItem('{STORE_KEY}'))")
    assert saved["theme"] == "candy" and saved["mode"] == "auto" and "token" not in json.dumps(saved)
    page.reload()
    assert root() == ["candy", "dark"]                                                # remembered
    page.click("#theme-btn")
    page.mouse.click(10, 500)                                                         # a click elsewhere closes it
    assert page.is_hidden("#theme-pop")
    context.close()


def test_auto_follows_a_light_system_and_an_explicit_mode_ignores_it(browser):
    context, page = new_page(browser, scheme="light")
    page.goto(PAGE_URL)
    assert page.evaluate("document.documentElement.dataset.eff") == "light"
    page.click("#theme-btn")
    page.click("[data-pick-mode=dark]")
    assert page.evaluate("document.documentElement.dataset.eff") == "dark"
    page.emulate_media(color_scheme="light")
    assert page.evaluate("document.documentElement.dataset.eff") == "dark"
    context.close()
    context, page = new_page(browser, scheme="dark", theme="aurora", mode="light")                    # a saved choice wins over the system at load
    page.goto(PAGE_URL)
    assert page.evaluate("() => [document.documentElement.dataset.theme, document.documentElement.dataset.eff]") == ["aurora", "light"]
    context.close()


def test_every_look_keeps_the_text_readable(browser):
    """The colour of the text against its background, in all eight combinations, on the two surfaces that carry most text (page and card)."""
    def lum(rgb):
        c = [x / 255 for x in rgb]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    for theme in ("neon", "classic", "aurora", "candy"):
        for mode in ("dark", "light"):
            context, page = new_page(browser, theme=theme, mode=mode)
            page.goto(PAGE_URL)
            open_tab(page, "settings")
            got = page.evaluate("""() => {
              const px = (el, prop) => { const c = document.createElement('canvas').getContext('2d'); c.fillStyle = getComputedStyle(el)[prop]; c.fillRect(0, 0, 1, 1); return Array.from(c.getImageData(0, 0, 1, 1).data.slice(0, 3)); };
              const card = document.querySelector('#connect'), muted = document.querySelector('#connect p.muted'), button = document.getElementById('refresh');
              const probe = document.createElement('span'); probe.textContent = 'x'; probe.style.color = 'var(--accent)'; card.append(probe);
              const out = { ink: px(card, 'color'), card: px(card, 'backgroundColor'), muted: px(muted, 'color'), accent: px(probe, 'color'), onButton: px(button, 'color'), button: px(button, 'backgroundColor') };
              probe.remove();
              return out;
            }""")
            assert ratio(got["ink"], got["card"]) >= 7, (theme, mode, got)                      # body text on a card
            assert ratio(got["muted"], got["card"]) >= 4.5, (theme, mode, got)                  # the quieter text still passes AA
            assert ratio(got["accent"], got["card"]) >= 4.5, (theme, mode, got)                 # links and the current tab
            assert ratio(got["onButton"], got["button"]) >= 4.5, (theme, mode, got)             # the label on the main button
            context.close()


def test_the_status_words_and_glyphs_are_readable_in_every_look_and_on_a_folded_card(browser):
    """A status word is drawn in its colour mixed toward the text colour: the raw amber of "needs you" on a white card is about 2:1."""
    def lum(rgb):
        c = [x / 255 for x in rgb[:3]]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def ratio(a, b):
        la, lb = sorted((lum(a), lum(b)), reverse=True)
        return (la + 0.05) / (lb + 0.05)

    def over(top, base, alpha):
        return [top[i] * alpha + base[i] * (1 - alpha) for i in range(3)]

    probe = """() => {
      const px = (el, prop) => { const c = document.createElement('canvas').getContext('2d'); c.fillStyle = getComputedStyle(el)[prop]; c.clearRect(0, 0, 1, 1); c.fillRect(0, 0, 1, 1); return Array.from(c.getImageData(0, 0, 1, 1).data); };
      const out = { cards: [], boxes: [], rows: [] }, grid = document.getElementById('wavegrid'), home = document.getElementById('view-home');
      for (const folded of [false, true]) for (const s of ['needs', 'running', 'review', 'under', 'ready', 'blocked', 'done']) {
        const b = document.createElement('button'); b.className = 'node s-' + s + (folded ? ' compact' : '');
        const c = document.createElement('span'); c.className = 'chipst'; c.textContent = 'x'; b.append(c); grid.append(b);
        out.cards.push({ s, folded, fg: px(c, 'color'), bg: px(b, 'backgroundColor'), opacity: Number(getComputedStyle(b).opacity) }); b.remove();
      }
      const panel = document.createElement('div'); panel.className = 'panel'; home.append(panel);
      out.panel = px(panel, 'backgroundColor');
      const rows = document.createElement('div'); rows.className = 'rows'; panel.append(rows);
      for (const s of ['done', 'running', 'waiting', 'failed', 'queued', 'skipped', 'cancelled']) {
        const f = document.createElement('div'); f.className = 'fbox st-' + s; const t = document.createElement('div'); t.className = 't'; const g = document.createElement('span'); g.className = 'g'; g.textContent = 'x'; t.append(g); f.append(t); panel.append(f);
        out.boxes.push({ s, fg: px(g, 'color'), bg: px(f, 'backgroundColor') });
        const li = document.createElement('li'); li.className = 'st-' + s; const w = document.createElement('span'); w.className = 'glyph'; w.textContent = 'x'; li.append(w); rows.append(li);
        out.rows.push({ s, fg: px(w, 'color'), bg: px(li, 'backgroundColor') });
      }
      panel.remove();
      return out;
    }"""
    for theme in ("neon", "classic", "aurora", "candy"):
        for mode in ("dark", "light"):
            context, page = new_page(browser, theme=theme, mode=mode)
            page.goto(PAGE_URL)
            open_tab(page, "board")
            got = page.evaluate(probe)
            card = got["panel"][:3]
            for c in got["cards"]:
                text_on_card = over(c["fg"], card, c["opacity"])                       # a folded card is drawn at reduced opacity over the board's panel
                assert ratio(text_on_card, over(c["bg"], card, c["opacity"])) >= 4.5, (theme, mode, c)
            for kind in ("boxes", "rows"):
                for g in got[kind]:
                    behind = over(g["bg"], card, g["bg"][3] / 255)                     # an unstarted box has no fill: the panel shows through
                    assert ratio(g["fg"], behind) >= 4.5, (theme, mode, kind, g)
            context.close()


def test_the_demo_contacts_nobody_shows_every_screen_and_leaving_it_brings_the_list_back(ui):
    open_tab(ui, "settings")
    seen = len(ui.seen)
    ui.click("#demo")
    open_tab(ui, "home")
    wait(ui, "document.querySelectorAll('#home-runs .pipe').length === 4")
    assert ui.is_visible("#banner") and len(ui.seen) == seen and text(ui, "#live-text") == "mẫu"
    assert "Deploy production" in text(ui, "#home-runs") and "chờ duyệt" in text(ui, "#home-runs") and "lần thử thứ 2" in text(ui, "#home-runs")
    board(ui)
    assert text(ui, "#f-count") == "38 / 38"
    open_tab(ui, "repos")
    assert ui.locator("#repo-grid .repo").count() == 4
    assert "PR mở: ?" not in text(ui, "#repo-grid") and "Issue mở: ?" not in text(ui, "#repo-grid")          # the demo's lists are all read: exact counts
    open_tab(ui, "agents")
    assert "REQ-031" in text(ui, "#office-body")
    open_tab(ui, "settings")
    assert ui.is_disabled("#add")
    ui.click("#demo")
    wait(ui, "document.getElementById('banner').hidden")
    assert ui.locator(".chip").count() == 1 and text(ui, ".chip").startswith("demo/app") and ui.is_enabled("#add")
    assert ui.evaluate(f"() => JSON.parse(window.localStorage.getItem('{STORE_KEY}')).repos") == ["demo/app"]      # the demo never overwrote the saved list


def test_a_look_chosen_while_the_demo_is_on_is_remembered_and_the_demo_names_are_not(ui):
    open_tab(ui, "settings")
    ui.click("#demo")
    open_tab(ui, "home")
    ui.click("#theme-btn")
    ui.click("[data-pick-theme=aurora]")
    ui.click("[data-pick-mode=light]")
    ui.keyboard.press("Escape")
    saved = ui.evaluate(f"() => JSON.parse(window.localStorage.getItem('{STORE_KEY}'))")
    assert saved["theme"] == "aurora" and saved["mode"] == "light" and saved["repos"] == ["demo/app"]          # the real list, not demo/web-app and the others
    ui.reload()
    assert ui.evaluate("() => [document.documentElement.dataset.theme, document.documentElement.dataset.eff]") == ["aurora", "light"]


@pytest.mark.parametrize("width", [320, 390, 768])
def test_nothing_scrolls_sideways_on_a_phone_or_tablet_in_any_screen(browser, width):
    context, page = new_page(browser, width=width)
    page.goto(PAGE_URL)
    open_tab(page, "settings")
    page.click("#demo")
    for view in VIEWS:
        open_tab(page, view)
        if view == "home":
            wait(page, "document.querySelectorAll('#home-runs .pipe').length === 4")
        if view == "board":
            wait(page, "document.querySelectorAll('#edges path').length > 0")
        if view == "repos":
            for summary in page.query_selector_all("details.wf > summary"):
                summary.click()
        page.wait_for_timeout(100)
        sizes = page.evaluate("() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth })")
        assert sizes["scroll"] <= sizes["client"], (view, sizes)
    context.close()


def test_the_page_refuses_to_run_inside_a_frame(browser, tmp_path):
    outer = Path(tmp_path) / "outer.html"
    outer.write_text(f'<!doctype html><iframe id="f" src="{PAGE_URL}" width="600" height="400"></iframe>', encoding="utf-8")
    context = browser.new_context()
    serve(context)
    page = context.new_page()
    page.goto(outer.as_uri())
    page.wait_for_timeout(300)
    assert "không chạy trong khung nhúng" in page.frame_locator("#f").locator("html").inner_text()
    assert page.frame_locator("#f").locator("#refresh").count() == 0
    context.close()


def test_the_content_security_policy_really_blocks_injected_script(ui):
    ui.evaluate("""() => { const s = document.createElement('script'); s.textContent = 'window.__ran = 1'; document.body.append(s); }""")
    assert ui.evaluate("window.__ran") is None                                           # the hash allows our script and nothing else
    ui.evaluate("() => { fetch('https://example.com/x').catch(() => { window.__blocked = true; }); }")
    wait(ui, "window.__blocked === true")
    assert not [u for u in ui.seen if "example.com" in u]


def test_a_refresh_does_not_take_the_focus_or_fold_what_the_person_opened(ui):
    repos(ui)
    summary = ui.locator("details.wf summary >> nth=0")
    summary.focus()
    ui.keyboard.press("Enter")
    assert ui.evaluate("document.querySelector('details.wf').open") is True
    assert ui.evaluate("document.activeElement.tagName") == "SUMMARY"
    job = ui.locator("details.wf[open] .rows details >> nth=0")
    job.locator("summary").click()                                                   # fold a job the page had opened
    folded = job.evaluate("e => e.open")
    open_tab(ui, "settings")
    ui.click("#refresh")
    ui.wait_for_timeout(600)
    open_tab(ui, "repos")
    assert ui.evaluate("document.querySelector('details.wf').open") is True
    assert ui.locator("details.wf[open] .rows details >> nth=0").evaluate("e => e.open") == folded      # a folded row stays folded after the refresh
