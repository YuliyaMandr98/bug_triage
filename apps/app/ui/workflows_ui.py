"""Workflows UI (triage_bugs only)."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from apps.app.database import WorkflowRunModel
from apps.app.database import get_session_factory
from apps.app.config import get_settings
from apps.app.workflows import enqueue_workflow
from packages.common import TriageBugTicketsRunRequest
from packages.common import WorkflowType

router = APIRouter()


def get_db(request: Request) -> Session:
    """Get database session."""
    settings = get_settings()
    SessionLocal = get_session_factory(settings.database_url)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _bool_from_form(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "on", "yes"}


def _render_triage_bugs_page(
    *,
    form_values: dict[str, str] | None = None,
    validation_error: str | None = None,
    run_id: str | None = None,
) -> str:
    default_jql = 'issuetype in ("BE BUG", "Mobile bug", Bug, "FE bug") AND status = Backlog'
    values = {
        "jql": default_jql,
        "max_results": "50",
        "apply": "",
        "add_comment": "",
        "severity_field_id": "customfield_10865",
        "impact_field_id": "customfield_10004",
        "target_status": "Triage",
        "batch_delay_seconds": "10",
    }
    if form_values:
        values.update(form_values)

    error_block = (
        f'<div class="error">Validation error: {validation_error}</div>'
        if validation_error
        else ""
    )

    run_panel = ""
    if run_id:
        run_panel = f"""
        <div class="result-card">
            <h3>Run Monitor</h3>
            <p>Run ID: <code>{run_id}</code></p>
            <div id="dryRunBadge" class="badge">Pending...</div>
            <div id="runProgress" class="progress-line">Preparing monitor...</div>
            <div id="currentStep" class="step-line">Current step: waiting...</div>
            <div class="counters">
                <div>Fetched: <strong id="countFetched">0</strong></div>
                <div>Triaged: <strong id="countTriaged">0</strong></div>
                <div>Not Real Bug: <strong id="countNotReal">0</strong></div>
                <div>Errors: <strong id="countErrors">0</strong></div>
            </div>
            <h4>Status</h4>
            <pre id="runStatus">Loading...</pre>
            <h4>Terminal-Like Stream</h4>
            <div class="logs-toolbar">
                <span id="logMeta">Logs: 0</span>
                <label><input type="checkbox" id="autoScrollLogs" checked /> Auto-scroll</label>
            </div>
            <pre id="liveLogs">Loading logs...</pre>
            <h4>Per-Issue Results</h4>
            <div id="bulkApplyBar" style="display:none; margin-bottom:8px;">
                <button id="btnApplyAll" class="btn-apply-all" onclick="applyAll()">✅ Apply All Real Bugs to Jira</button>
                <label style="margin-left:12px; font-size:13px;"><input type="checkbox" id="addCommentApply" /> Add Jira triage comments</label>
                <span id="bulkApplyStatus" style="margin-left:12px; font-size:13px;"></span>
            </div>
            <table>
                <thead>
                    <tr><th>Key</th><th>Summary</th><th>Real Bug</th><th>Severity</th><th>Impact</th><th>Priority</th><th>Outcome</th><th>Reasoning</th><th id="applyColHeader"></th></tr>
                </thead>
                <tbody id="resultsTableBody"><tr><td colspan="9">Waiting for results...</td></tr></tbody>
            </table>
            <h4>Artifacts</h4>
            <ul id="artifactLinks"><li>Waiting for artifacts...</li></ul>
        </div>
        <script>
            const runId = "{run_id}";
            let done = false;
            let isDryRun = true;
            let runFinished = false;
            let lastRows = [];
            const monitorStartedAt = Date.now();
            let lastLogTimestamp = null;
            const POLL_INTERVAL_MS = 1000;
            const ARTIFACT_POLL_EVERY_TICKS = 5;
            let tickCount = 0;

            function outcomeClass(outcome) {{
                if (outcome === 'triaged') return 'outcome-ok';
                if (outcome === 'dry_run') return 'outcome-dry';
                if (outcome === 'error' || outcome === 'partial_update') return 'outcome-err';
                return 'outcome-skip';
            }}

            function fmtElapsed(ms) {{
                const sec = Math.floor(ms / 1000);
                const min = Math.floor(sec / 60);
                const rem = sec % 60;
                return `${{min}}m ${{rem}}s`;
            }}

            async function refreshRun() {{
                const runResp = await fetch(`/api/runs/${{runId}}`);
                if (!runResp.ok) {{
                    const msg = `Failed to load run status (HTTP ${{runResp.status}})`;
                    document.getElementById("runStatus").textContent = msg;
                    document.getElementById("runProgress").textContent = msg;
                    return;
                }}
                const run = await runResp.json();
                document.getElementById("runStatus").textContent = JSON.stringify(run, null, 2);

                const elapsed = fmtElapsed(Date.now() - monitorStartedAt);
                const statusUpper = String(run.status || "unknown").toUpperCase();
                document.getElementById("runProgress").textContent = `Status: ${{statusUpper}} | Elapsed: ${{elapsed}}`;

                const dryRunBadge = document.getElementById("dryRunBadge");
                if (run.dry_run) {{
                    isDryRun = true;
                    dryRunBadge.textContent = "DRY-RUN (preview only — no Jira changes)";
                    dryRunBadge.className = "badge dry";
                }} else {{
                    isDryRun = false;
                    dryRunBadge.textContent = "APPLY MODE (writes enabled — Jira will be updated)";
                    dryRunBadge.className = "badge apply";
                }}

                const logsResp = await fetch(`/api/runs/${{runId}}/logs`);
                if (logsResp.ok) {{
                    const logsData = await logsResp.json();
                    const logs = logsData.logs || [];
                    const stripLogPrefix = (msg) => msg.replace(/^\[[\dT:.+\-Z]+\]\s*\[cid=[^\]]+\]\s*/, "");
                    const lines = logs.map((l) => `${{l.timestamp}} [${{l.level}}] ${{stripLogPrefix(l.message)}}`);
                    const logsEl = document.getElementById("liveLogs");
                    logsEl.textContent = lines.join("\\n") || "No logs yet";

                    if (logs.length > 0) {{
                        lastLogTimestamp = logs[logs.length - 1].timestamp;
                    }}
                    document.getElementById("logMeta").textContent =
                        `Logs: ${{logs.length}} | Last update: ${{lastLogTimestamp || "n/a"}}`;

                    const currentStepEl = document.getElementById("currentStep");
                    if (logs.length > 0) {{
                        const last = logs[logs.length - 1];
                        currentStepEl.textContent = `Current step: [${{last.level}}] ${{stripLogPrefix(last.message)}}`;
                    }} else {{
                        currentStepEl.textContent = "Current step: waiting for first log line...";
                    }}

                    if (document.getElementById("autoScrollLogs").checked) {{
                        logsEl.scrollTop = logsEl.scrollHeight;
                    }}

                    if (run.status === "running" || run.status === "queued") {{
                        const waitHint = logs.length === 0
                            ? "Waiting for worker logs..."
                            : "Workflow is running. Gemini calls may take time; logs update as each issue is processed.";
                        document.getElementById("runProgress").textContent =
                            `Status: ${{statusUpper}} | Elapsed: ${{elapsed}} | ${{waitHint}}`;
                    }}
                }}

                const isTerminal = ["succeeded", "failed", "canceled"].includes(run.status);
                if (isTerminal) {{
                    runFinished = true;
                }}

                const shouldPollArtifacts =
                    (tickCount % ARTIFACT_POLL_EVERY_TICKS === 0) ||
                    isTerminal;

                if (shouldPollArtifacts) {{
                    const artifactResp = await fetch(`/api/artifacts/run/${{runId}}`);
                    if (artifactResp.ok) {{
                        const artifacts = await artifactResp.json();
                        const linksEl = document.getElementById("artifactLinks");
                        linksEl.innerHTML = "";
                        for (const item of artifacts) {{
                            const li = document.createElement("li");
                            const a = document.createElement("a");
                            a.href = item.download_url;
                            a.textContent = item.filename;
                            li.appendChild(a);
                            linksEl.appendChild(li);
                        }}

                        const summaryArtifact = artifacts.find((a) => a.filename === "triage_summary.json");
                        if (summaryArtifact) {{
                            const summaryResp = await fetch(summaryArtifact.download_url);
                            if (summaryResp.ok) {{
                                const summary = await summaryResp.json();
                                document.getElementById("countFetched").textContent = summary.bugs_fetched ?? 0;
                                document.getElementById("countTriaged").textContent = summary.bugs_triaged ?? 0;
                                document.getElementById("countNotReal").textContent = summary.skipped_not_real_bug ?? 0;
                                document.getElementById("countErrors").textContent = summary.errors ?? 0;
                            }}
                        }}

                        const perIssueArtifact = artifacts.find((a) => a.filename === "per_issue_results.json");
                        if (perIssueArtifact) {{
                            const resultResp = await fetch(perIssueArtifact.download_url);
                            if (resultResp.ok) {{
                                const rows = await resultResp.json();
                                lastRows = rows;
                                const body = document.getElementById("resultsTableBody");
                                body.innerHTML = "";
                                const showApplyCol = isDryRun && runFinished;
                                document.getElementById("applyColHeader").textContent = showApplyCol ? "Apply" : "";
                                for (const row of rows) {{
                                    const tr = document.createElement("tr");
                                    tr.id = `row-${{row.key}}`;
                                    tr.className = outcomeClass(row.outcome);
                                    const isReal = row.is_real_bug === null ? "-" : row.is_real_bug ? "✅ Yes" : "❌ No";
                                    const sev = row.severity || "-";
                                    const imp = row.impact || "-";
                                    const pri = row.priority || "-";
                                    const applyCell = (showApplyCol && row.is_real_bug)
                                        ? `<td><button class="btn-apply-row" onclick="applyOne('${{row.key}}', this)">Apply</button></td>`
                                        : `<td></td>`;
                                    tr.innerHTML = `
                                        <td><a href="/ui/runs" target="_blank">${{row.key || ""}}</a></td>
                                        <td>${{row.summary || ""}}</td>
                                        <td>${{isReal}}</td>
                                        <td>${{sev}}</td>
                                        <td>${{imp}}</td>
                                        <td>${{pri}}</td>
                                        <td><span class="outcome-badge ${{row.outcome || ""}}">${{row.outcome || ""}}</span></td>
                                        <td>${{row.reasoning || row.reason || ""}}</td>
                                        ${{applyCell}}
                                    `;
                                    body.appendChild(tr);
                                }}
                                if (showApplyCol && rows.some(r => r.is_real_bug)) {{
                                    document.getElementById("bulkApplyBar").style.display = "";
                                }}
                            }}
                        }}
                    }}
                }}

                if (isTerminal) {{
                    done = true;
                }}
            }}

            async function _callApply(issueKeys) {{
                const statusEl = document.getElementById("bulkApplyStatus");
                const addComment = !!document.getElementById("addCommentApply")?.checked;
                const resp = await fetch("/api/workflows/triage-bugs/apply-issues", {{
                    method: "POST",
                    headers: {{ "Content-Type": "application/json" }},
                    body: JSON.stringify({{ run_id: runId, issue_keys: issueKeys, add_comment: addComment }}),
                }});
                if (!resp.ok) {{
                    const err = await resp.text();
                    statusEl.textContent = `Error: ${{err}}`;
                    return null;
                }}
                return await resp.json();
            }}

            async function applyOne(issueKey, btn) {{
                const addComment = !!document.getElementById("addCommentApply")?.checked;
                const commentSuffix = addComment ? " and post a triage comment" : "";
                if (!confirm(`Apply triage to ${{issueKey}} in Jira${{commentSuffix}}?`)) return;
                btn.disabled = true;
                btn.textContent = "...";
                const result = await _callApply([issueKey]);
                if (!result) {{ btn.textContent = "Error"; return; }}
                if (result.applied && result.applied.includes(issueKey)) {{
                    btn.textContent = "✅ Applied";
                    btn.style.background = "#198754";
                }} else if (result.errors && result.errors.length) {{
                    btn.textContent = "❌ Failed";
                    btn.title = result.errors[0]?.error || "";
                    btn.style.background = "#dc3545";
                }}
            }}

            async function applyAll() {{
                const realBugKeys = lastRows.filter(r => r.is_real_bug).map(r => r.key);
                if (!realBugKeys.length) return;
                const addComment = !!document.getElementById("addCommentApply")?.checked;
                const commentSuffix = addComment ? " and add triage comments" : "";
                if (!confirm(`Apply triage to ${{realBugKeys.length}} real bug(s) in Jira? This will update severity, impact, priority and transition status${{commentSuffix}}.`)) return;
                const statusEl = document.getElementById("bulkApplyStatus");
                const allBtn = document.getElementById("btnApplyAll");
                allBtn.disabled = true;
                statusEl.textContent = "Applying...";
                const result = await _callApply(null);
                if (!result) {{ statusEl.textContent = "Request failed."; allBtn.disabled = false; return; }}
                statusEl.textContent = `✅ Applied ${{result.applied_count}} | ❌ Errors: ${{result.error_count}}`;
                document.querySelectorAll(".btn-apply-row").forEach(b => {{
                    const key = b.closest("tr")?.id?.replace("row-", "");
                    if (result.applied && result.applied.includes(key)) {{
                        b.textContent = "✅ Applied"; b.disabled = true; b.style.background = "#198754";
                    }} else if (result.errors && result.errors.some(e => e.key === key)) {{
                        b.textContent = "❌ Failed"; b.disabled = true; b.style.background = "#dc3545";
                    }}
                }});
            }}

            async function tick() {{
                tickCount += 1;
                try {{ await refreshRun(); }} catch (e) {{}}
                if (!done) setTimeout(tick, POLL_INTERVAL_MS);
            }}
            tick();
        </script>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Run Triage Bugs - Triage Bugs Tool</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; }}
            nav {{ background: #0066cc; color: white; padding: 15px 30px; }}
            nav h1 {{ margin: 0; font-size: 24px; }}
            nav ul {{ list-style: none; display: flex; gap: 20px; margin-top: 10px; }}
            nav a {{ color: white; text-decoration: none; }}
            .container {{ max-width: 1100px; margin: 0 auto; padding: 20px; }}
            h2 {{ color: #333; margin: 20px 0 10px 0; }}
            form, .result-card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 16px; }}
            .form-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
            .form-group {{ margin-bottom: 14px; }}
            label {{ display: block; font-weight: bold; margin-bottom: 6px; color: #333; }}
            input, select, textarea {{ width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; }}
            textarea {{ font-family: monospace; font-size: 13px; resize: vertical; }}
            .checkbox {{ display: flex; gap: 8px; align-items: center; margin-bottom: 8px; }}
            .checkbox input {{ width: auto; }}
            .checkbox label {{ font-weight: normal; margin: 0; }}
            button {{ background: #0066cc; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }}
            button:hover {{ background: #0052a3; }}
            .error {{ background: #ffe7e7; color: #8b0000; border: 1px solid #f5baba; padding: 10px; margin-bottom: 12px; border-radius: 6px; }}
            .badge {{ display: inline-block; padding: 4px 8px; border-radius: 12px; font-weight: 600; margin: 8px 0; }}
            .badge.dry {{ background: #fff3cd; color: #664d03; }}
            .badge.apply {{ background: #d1e7dd; color: #0f5132; }}
            .progress-line {{ margin: 6px 0 12px 0; font-size: 14px; color: #333; }}
            .step-line {{ margin: 0 0 10px 0; font-size: 13px; color: #555; }}
            .logs-toolbar {{ display: flex; justify-content: space-between; align-items: center; margin: 6px 0; font-size: 13px; color: #444; }}
            .logs-toolbar label {{ font-weight: normal; margin: 0; }}
            table {{ width: 100%; border-collapse: collapse; }}
            th, td {{ padding: 8px; border-bottom: 1px solid #eee; text-align: left; font-size: 13px; }}
            th {{ background: #f9f9f9; font-weight: bold; }}
            .counters {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }}
            .counters div {{ background: #f5f5f5; border-radius: 6px; padding: 10px; text-align: center; font-size: 13px; }}
            pre {{ background: #1e1e1e; color: #d4d4d4; border-radius: 4px; padding: 10px; overflow: auto; max-height: 260px; font-size: 12px; }}
            .outcome-badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; font-weight: 600; }}
            .triaged td, tr.outcome-ok td {{ background: #f0fff4; }}
            tr.outcome-dry td {{ background: #fffbea; }}
            tr.outcome-err td {{ background: #fff5f5; }}
            tr.outcome-skip td {{ background: #fafafa; }}
            .outcome-badge.triaged {{ background: #d1e7dd; color: #0f5132; }}
            .outcome-badge.dry_run {{ background: #fff3cd; color: #664d03; }}
            .outcome-badge.error, .outcome-badge.partial_update {{ background: #f8d7da; color: #842029; }}
            .outcome-badge.skipped, .outcome-badge.not_real_bug {{ background: #e2e3e5; color: #41464b; }}
            h4 {{ margin: 16px 0 6px 0; color: #333; }}
            .hint {{ font-size: 12px; color: #666; margin-top: 4px; }}
            .btn-apply-row {{ background: #0066cc; color: white; border: none; border-radius: 4px; padding: 3px 10px; font-size: 12px; cursor: pointer; white-space: nowrap; }}
            .btn-apply-row:hover {{ background: #0052a3; }}
            .btn-apply-row:disabled {{ opacity: 0.7; cursor: default; }}
            .btn-apply-all {{ background: #198754; color: white; border: none; border-radius: 4px; padding: 8px 16px; font-size: 13px; cursor: pointer; font-weight: 600; }}
            .btn-apply-all:hover {{ background: #157347; }}
            .btn-apply-all:disabled {{ opacity: 0.7; cursor: default; }}
        </style>
    </head>
    <body>
        <nav>
            <h1>🐛 Triage Bugs Tool</h1>
            <ul>
                <li><a href="/ui">Dashboard</a></li>
                <li><a href="/ui/workflows">Workflows</a></li>
                <li><a href="/ui/runs">Runs</a></li>
                <li><a href="/ui/integrations">Integrations</a></li>
            </ul>
        </nav>

        <div class="container">
            <h2>Triage Bug Tickets</h2>
            <p style="margin-bottom: 16px; color: #555;">Fetches bug issues from Jira, looks up their User Story spec in Confluence, and uses Gemini to classify severity and impact. In dry-run mode no changes are made to Jira.</p>
            {error_block}
            <form method="post" action="/ui/workflows/triage_bugs/run" id="triageBugsForm">
                <div class="form-group">
                    <label for="jql">JQL Query</label>
                    <textarea id="jql" name="jql" rows="3">{values.get('jql', '')}</textarea>
                    <p class="hint">Jira Query Language filter — only issues matching this query will be triaged.</p>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label for="max_results">Max Results</label>
                        <input id="max_results" type="number" min="1" max="500" name="max_results" value="{values.get('max_results', '50')}" />
                    </div>
                    <div class="form-group">
                        <label for="batch_delay_seconds">Delay Between Gemini Calls (seconds)</label>
                        <input id="batch_delay_seconds" type="number" min="0" max="60" name="batch_delay_seconds" value="{values.get('batch_delay_seconds', '10')}" />
                    </div>
                </div>

                <div class="form-row">
                    <div class="form-group">
                        <label for="severity_field_id">Jira Severity Field ID</label>
                        <input id="severity_field_id" name="severity_field_id" value="{values.get('severity_field_id', 'customfield_10865')}" />
                    </div>
                    <div class="form-group">
                        <label for="impact_field_id">Jira Impact Field ID</label>
                        <input id="impact_field_id" name="impact_field_id" value="{values.get('impact_field_id', 'customfield_10004')}" />
                    </div>
                </div>

                <div class="form-group">
                    <label for="target_status">Target Jira Status (after triage)</label>
                    <input id="target_status" name="target_status" value="{values.get('target_status', 'Triage')}" />
                </div>

                <div class="checkbox">
                    <input type="checkbox" id="apply" name="apply" {"checked" if _bool_from_form(values.get('apply')) else ""} />
                    <label for="apply">Apply (update Jira fields and transition status)</label>
                </div>

                <div class="checkbox">
                    <input type="checkbox" id="add_comment" name="add_comment" {"checked" if _bool_from_form(values.get('add_comment')) else ""} />
                    <label for="add_comment">Add Jira triage comment (optional, off by default)</label>
                </div>

                <button type="submit">Run Triage</button>
            </form>

            {run_panel}
        </div>
        <script>
            document.getElementById("triageBugsForm").addEventListener("submit", (e) => {{
                const applyEl = document.getElementById("apply");
                if (applyEl.checked) {{
                    const ok = window.confirm("You are about to APPLY triage changes. Severity, impact, and status will be written to Jira. Continue?");
                    if (!ok) e.preventDefault();
                }}
            }});
        </script>
    </body></html>
    """


@router.get("", response_class=HTMLResponse)
async def workflows_page(request: Request) -> str:
    """Workflows page"""
    workflow_rows = ""

    for workflow in WorkflowType:
        workflow_rows += f"""
        <tr>
            <td>{workflow.value}</td>
            <td>{workflow.value.replace('_', ' ').title()}</td>
            <td><a href="/ui/workflows/{workflow.value}/run">Run Workflow</a></td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Workflows - Triage Bugs Tool</title>
        <style>
            * {{ margin: 0; padding: 0; box-sizing: border-box; }}
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f5f5f5; }}
            nav {{ background: #0066cc; color: white; padding: 15px 30px; }}
            nav h1 {{ margin: 0; font-size: 24px; }}
            nav ul {{ list-style: none; display: flex; gap: 20px; margin-top: 10px; }}
            nav a {{ color: white; text-decoration: none; }}
            .container {{ max-width: 1000px; margin: 0 auto; padding: 20px; }}
            h2 {{ color: #333; margin: 20px 0 10px 0; }}
            table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #eee; }}
            th {{ background: #f9f9f9; font-weight: bold; }}
            a {{ color: #0066cc; text-decoration: none; }}
        </style>
    </head>
    <body>
        <nav>
            <h1>🐛 Triage Bugs Tool</h1>
            <ul>
                <li><a href="/ui">Dashboard</a></li>
                <li><a href="/ui/workflows">Workflows</a></li>
                <li><a href="/ui/runs">Runs</a></li>
                <li><a href="/ui/integrations">Integrations</a></li>
            </ul>
        </nav>

        <div class="container">
            <h2>Available Workflows</h2>
            <p>Select a workflow to run</p>

            <table>
                <thead>
                    <tr>
                        <th>Key</th>
                        <th>Name</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {workflow_rows}
                </tbody>
            </table>
        </div>
    </body>
    </html>
    """


@router.get("/{workflow_key}/run", response_class=HTMLResponse)
async def run_workflow_page(request: Request, workflow_key: str) -> str:
    """Workflow run form"""
    try:
        WorkflowType(workflow_key)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown workflow: {workflow_key}")

    run_id = request.query_params.get("run_id")
    return _render_triage_bugs_page(run_id=run_id)


@router.post("/triage_bugs/run", response_class=HTMLResponse)
async def run_triage_bugs_submit(request: Request, db: Session = Depends(get_db)):
    """Form submit endpoint for triage_bugs workflow UI."""
    form = await request.form()
    form_values = {k: str(v) for k, v in form.items()}

    payload = {
        "jql": str(form.get("jql") or "").strip() or 'issuetype in ("BE BUG", "Mobile bug", Bug, "FE bug") AND status = Backlog',
        "max_results": int(str(form.get("max_results") or "50").strip() or "50"),
        "apply": _bool_from_form(form.get("apply")),
        "add_comment": _bool_from_form(form.get("add_comment")),
        "severity_field_id": str(form.get("severity_field_id") or "customfield_10865").strip(),
        "impact_field_id": str(form.get("impact_field_id") or "customfield_10004").strip(),
        "target_status": str(form.get("target_status") or "Triage").strip(),
        "batch_delay_seconds": int(str(form.get("batch_delay_seconds") or "10").strip() or "10"),
    }

    try:
        validated = TriageBugTicketsRunRequest(**payload)
    except (ValidationError, ValueError) as exc:
        return _render_triage_bugs_page(
            form_values=form_values,
            validation_error=str(exc),
        )

    run_id = str(uuid4())
    run = WorkflowRunModel(
        id=run_id,
        workflow_key="triage_bugs",
        parameters=validated.model_dump(),
        dry_run="0" if validated.apply else "1",
        status="queued",
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()

    enqueue_workflow(run_id, "triage_bugs")
    return RedirectResponse(url=f"/ui/workflows/triage_bugs/run?run_id={run_id}", status_code=303)
