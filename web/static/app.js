// SEGPILOT live site — vanilla JS, no build step, no dependencies.

// ---------------------------------------------------------------- theme ----
(function themeInit() {
  const KEY = "segpilot-theme";
  const btn = document.getElementById("themeToggle");
  const order = ["system", "light", "dark"];

  function apply(mode) {
    if (mode === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", mode);
    }
    btn.textContent = "theme: " + mode;
  }

  let current = localStorage.getItem(KEY) || "system";
  apply(current);

  btn.addEventListener("click", () => {
    const idx = (order.indexOf(current) + 1) % order.length;
    current = order[idx];
    localStorage.setItem(KEY, current);
    apply(current);
  });
})();

// -------------------------------------------------------------- helpers ----
function pct(x) { return (x * 100).toFixed(0) + "%"; }
function fmt3(x) { return Number(x).toFixed(3); }
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

const HIGHLIGHT_ARMS = new Set(["intent_only", "segpilot"]);

// -------------------------------------------------------------- results ----
async function loadResults() {
  let doc;
  try {
    const r = await fetch("/api/results");
    if (!r.ok) throw new Error("HTTP " + r.status);
    doc = await r.json();
  } catch (err) {
    document.getElementById("verdictHeadline").textContent =
      "Results not generated yet — run `python -m segpilot.replay.report --json`.";
    return;
  }
  renderStatStrip(doc);
  renderVerdict(doc);
  renderPopulationTabs(doc);
  renderCorrections(doc.corrections || []);
  renderFindings(doc.findings || []);
  if (doc.solve_rate) renderSolveRate(doc.solve_rate);
}

function renderStatStrip(doc) {
  const strip = document.getElementById("statStrip");
  const nums = strip.querySelectorAll(".stat .n");
  nums[0].textContent = (doc.findings || []).length;
  nums[1].textContent = doc.generated_from_sessions ?? "—";
  nums[2].textContent = (doc.arms || []).length;
  nums[3].textContent = (doc.corrections || []).length;
}

function renderVerdict(doc) {
  document.getElementById("verdictHeadline").textContent =
    doc.verdict || "No verdict recorded yet.";
}

function armsTableBody(arms, rows) {
  const table = document.createElement("table");
  table.className = "data";
  const thead = el("thead", {}, el("tr", {}, [
    el("th", {}, "arm"), el("th", {}, "ratio"), el("th", {}, "saved"),
    el("th", {}, "must-keep ret."), el("th", {}, "task ret."),
    el("th", {}, "rel/1k"), el("th", {}, "guard trips"),
  ]));
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr", { class: HIGHLIGHT_ARMS.has(row.arm) ? "me" : "" }, [
      el("td", {}, row.arm),
      el("td", {}, fmt3(row.ratio)),
      el("td", {}, pct(row.saving_pct)),
      el("td", {}, [
        el("span", { class: "meter" }, [
          document.createTextNode(pct(row.must_keep_retention) + " "),
          el("span", { class: "bar" }, el("i", { style: `width:${row.must_keep_retention * 100}%` })),
        ]),
      ]),
      el("td", {}, pct(row.task_retention)),
      el("td", {}, fmt3(row.rel_per_1k)),
      el("td", {}, String(row.guard_trips)),
    ]);
    tbody.appendChild(tr);
  }
  table.appendChild(thead);
  table.appendChild(tbody);
  return table;
}

function renderPopulationTabs(doc) {
  const tabsEl = document.getElementById("popTabs");
  const tableWrap = document.getElementById("armsTable").parentElement;
  const popNote = document.getElementById("popNote");

  const tabs = [];
  if (doc.populations && doc.populations.campaign) {
    tabs.push({ key: "campaign", label: `Drift campaigns (${doc.populations.campaign.session_count})`, rows: doc.populations.campaign.arms });
  }
  if (doc.populations && doc.populations["single-bug"]) {
    tabs.push({ key: "single-bug", label: `Single-bug control (${doc.populations["single-bug"].session_count})`, rows: doc.populations["single-bug"].arms });
  }
  tabs.push({ key: "all", label: `All sessions (${doc.generated_from_sessions ?? "?"})`, rows: doc.aggregate_all || [] });

  function show(key) {
    const t = tabs.find((x) => x.key === key) || tabs[0];
    tableWrap.innerHTML = "";
    tableWrap.appendChild(armsTableBody(doc.arms, t.rows));
    tabsEl.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.key === t.key));
  }

  tabsEl.innerHTML = "";
  for (const t of tabs) {
    const b = el("button", { class: "tab-btn", type: "button", "data-key": t.key }, t.label);
    b.addEventListener("click", () => show(t.key));
    tabsEl.appendChild(b);
  }
  popNote.textContent = "Campaign sessions are the drift test; single-bug sessions are the no-drift control.";
  show(tabs[0].key);
}

function renderCorrections(corrections) {
  const log = document.getElementById("correctionsLog");
  log.innerHTML = "";
  for (const c of corrections) {
    log.appendChild(el("div", { class: "correction" }, [
      el("div", { class: "was" }, `"${c.was}"`),
      el("div", { class: "why" }, c.why),
    ]));
  }
}

function renderFindings(findings) {
  const list = document.getElementById("findingsList");
  list.innerHTML = "";
  for (const f of findings) {
    list.appendChild(el("li", {}, el("span", { class: "t" }, f)));
  }
}

function renderSolveRate(solveRate) {
  const section = document.getElementById("solve-rate-section");
  section.style.display = "";
  const table = document.getElementById("solveRateTable");
  table.innerHTML = "";
  const thead = el("thead", {}, el("tr", {}, [el("th", {}, "arm"), el("th", {}, "solved"), el("th", {}, "tasks"), el("th", {}, "rate")]));
  const tbody = el("tbody");
  for (const [arm, s] of Object.entries(solveRate)) {
    tbody.appendChild(el("tr", { class: HIGHLIGHT_ARMS.has(arm) ? "me" : "" }, [
      el("td", {}, arm), el("td", {}, String(s.solved)), el("td", {}, String(s.total)),
      el("td", {}, s.rate == null ? "—" : pct(s.rate)),
    ]));
  }
  table.appendChild(thead);
  table.appendChild(tbody);
}

loadResults();

// ------------------------------------------------------------------ demo ----
const PRESETS = [
  {
    label: "3 unrelated functions (textbook case)",
    intent: "why does authenticate_user reject valid passwords",
    kind: "file_read",
    note: "This is finding #4's example: intent picks out exactly the relevant function and drops the other two entirely. The lever works dramatically here — try changing the intent to “debug the KMS key rotation logic” and watch which function survives instead.",
    code:
`import logging
logger = logging.getLogger(__name__)

def authenticate_user(username, password, session_store):
    """Authenticate and open a session."""
    logger.debug("auth attempt for %s", username)
    user = UserRepository.find_by_username(username)
    if user is None:
        raise AuthenticationError("no such user")
    if not verify_password_hash(password, user.password_hash):
        raise AuthenticationError("bad password")
    token = session_store.create_session(user.id, ttl_seconds=3600)
    logger.info("session opened for %s", username)
    return token

def rotate_encryption_keys(keyring, kms_client):
    """Rotate all data-encryption keys against the KMS."""
    logger.debug("starting key rotation")
    for key_id in keyring.list_active_key_ids():
        new_material = kms_client.generate_data_key(key_id)
        keyring.stage_rotation(key_id, new_material)
    keyring.commit_rotation()
    logger.info("key rotation complete")

def export_metrics_snapshot(registry, destination_path):
    """Write a Prometheus snapshot to disk."""
    logger.debug("exporting metrics")
    payload = registry.render_prometheus_text()
    with open(destination_path, "w") as handle:
        handle.write(payload)
    logger.info("metrics written to %s", destination_path)`,
  },
  {
    label: "realistic interrelated code (what we actually found)",
    intent: "why do sessions expire early when clock skew compensation is enabled",
    kind: "file_read",
    note: "Real code rarely separates as cleanly as the example above. On interrelated functions like these, intent routing often changes almost nothing — this is closer to what docs/results.md found across real agent sessions: a weak, inconsistent effect, not a reliable win.",
    code:
`import time
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class RateLimitBucket:
    """Token bucket used by the API rate limiter."""
    capacity: int
    refill_per_second: float
    tokens: float
    last_refill: float

def consume_rate_limit_token(bucket, now=None):
    """Consume one token from the rate-limit bucket."""
    now = now or time.time()
    elapsed = now - bucket.last_refill
    bucket.tokens = min(bucket.capacity, bucket.tokens + elapsed * bucket.refill_per_second)
    bucket.last_refill = now
    if bucket.tokens < 1:
        return False
    bucket.tokens -= 1
    return True

def compute_session_expiry(issued_at, ttl_seconds, clock_skew_seconds=0):
    """Return the absolute expiry timestamp for a session.

    BUG: clock_skew_seconds is subtracted instead of added, so sessions
    expire earlier than intended whenever skew compensation is configured.
    """
    logger.debug("computing expiry issued_at=%s ttl=%s", issued_at, ttl_seconds)
    return issued_at + ttl_seconds - clock_skew_seconds

def is_session_expired(session, now=None):
    """True when the session's expiry timestamp has passed."""
    now = now or time.time()
    expiry = compute_session_expiry(session.issued_at, session.ttl_seconds, session.clock_skew_seconds)
    logger.debug("session %s expiry=%s now=%s", session.id, expiry, now)
    return now >= expiry`,
  },
  {
    label: "failing test output",
    intent: "why does test_parse_amount_from_float_is_exact_to_cents fail",
    kind: "log_output",
    note: "",
    code:
`F...........FF.FFFF.F                                                    [100%]
================================== FAILURES ===================================
_______________ test_parse_amount_from_float_is_exact_to_cents ________________

    def test_parse_amount_from_float_is_exact_to_cents():
        """Parsing must not reintroduce binary float error.

        2.675 is the classic case: as a binary float it is actually
        2.67499999999999982..., so rounding the float to cents gives 2.67,
        while the intended decimal value rounds half-up to 2.68. Going
        through str preserves the intended value.
        """
        parsed = parse_amount(2.675)
>       assert parsed.quantize(CENTS, rounding=ROUND_HALF_UP) == Decimal("2.68")
E       AssertionError: assert Decimal('2.67') == Decimal('2.68')

tests/test_entries.py:23: AssertionError
============================ 1 failed, 41 passed in 0.31s ============================`,
  },
];

function setupPresets() {
  const wrap = document.getElementById("presetChips");
  const noteEl = document.getElementById("presetNote");
  wrap.innerHTML = "";
  function selectPreset(p) {
    document.getElementById("codeInput").value = p.code;
    document.getElementById("intentInput").value = p.intent;
    document.getElementById("kindSelect").value = p.kind;
    noteEl.textContent = p.note || "";
  }
  for (const p of PRESETS) {
    const chip = el("button", { class: "chip", type: "button" }, "↳ " + p.label);
    chip.addEventListener("click", () => selectPreset(p));
    wrap.appendChild(chip);
  }
  selectPreset(PRESETS[0]);   // pre-fill so the demo isn't empty on load
}
setupPresets();

function setStatus(msg, isErr) {
  const line = document.getElementById("statusLine");
  line.textContent = msg;
  line.className = "status-line" + (isErr ? " err" : "");
}

function renderCompressResult(result) {
  document.getElementById("resultGrid").style.display = "";
  const b = result.baseline, s = result.segpilot;

  document.getElementById("baselineStats").innerHTML = "";
  document.getElementById("baselineStats").append(
    el("span", {}, [document.createTextNode("tokens "), el("b", {}, `${result.original_tokens} → ${b.tokens}`)]),
    el("span", {}, [document.createTextNode("ratio "), el("b", {}, fmt3(b.ratio))]),
    el("span", {}, [document.createTextNode("must-keep "), el("b", {}, pct(b.must_keep_retention))]),
  );
  document.getElementById("baselineText").textContent = b.text;

  document.getElementById("routedStats").innerHTML = "";
  document.getElementById("routedStats").append(
    el("span", {}, [document.createTextNode("tokens "), el("b", {}, `${result.original_tokens} → ${s.tokens}`)]),
    el("span", {}, [document.createTextNode("ratio "), el("b", {}, fmt3(s.ratio))]),
    el("span", {}, [document.createTextNode("must-keep "), el("b", {}, pct(s.must_keep_retention))]),
  );
  document.getElementById("routedText").textContent = s.text;
}

async function pollJob(jobId) {
  const started = Date.now();
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    const elapsedS = Math.round((Date.now() - started) / 1000);
    let r;
    try {
      r = await fetch(`/api/compress/${jobId}`);
    } catch {
      setStatus("network error while polling — retrying…", true);
      continue;
    }
    if (!r.ok) {
      setStatus(`job lookup failed (HTTP ${r.status})`, true);
      return;
    }
    const body = await r.json();
    if (body.status === "done") {
      setStatus(`done in ~${elapsedS}s`);
      renderCompressResult(body.result);
      return;
    }
    if (body.status === "error") {
      setStatus("compression failed: " + body.error, true);
      return;
    }
    const hint = elapsedS > 20
      ? " — the hosted GPU may be cold-starting, this can take up to a minute"
      : "";
    setStatus(`${body.status}… (${elapsedS}s)${hint}`);
  }
}

document.getElementById("compressBtn").addEventListener("click", async () => {
  const code = document.getElementById("codeInput").value.trim();
  const intent = document.getElementById("intentInput").value.trim();
  const kind = document.getElementById("kindSelect").value;
  if (!code) {
    setStatus("paste some code or text first", true);
    return;
  }

  const btn = document.getElementById("compressBtn");
  btn.disabled = true;
  document.getElementById("resultGrid").style.display = "none";
  setStatus("submitting to Paritok's hosted GPU…");

  try {
    const r = await fetch("/api/compress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code, intent, kind }),
    });
    if (r.status === 429) {
      const body = await r.json();
      setStatus(`${body.error} — try again in ~${body.retry_after_s}s`, true);
      return;
    }
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      setStatus("request failed: " + (body.detail || r.status), true);
      return;
    }
    const { job_id } = await r.json();
    await pollJob(job_id);
  } catch (err) {
    setStatus("network error: " + err.message, true);
  } finally {
    btn.disabled = false;
  }
});
