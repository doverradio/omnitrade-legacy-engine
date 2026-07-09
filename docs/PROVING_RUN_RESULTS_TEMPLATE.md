# Proving Run Results Template

## Purpose

Standardized report template for documenting outcomes of each autonomous proving run.

Use this template after every run so results are comparable across time.

## Report Metadata

- Run ID:
- Environment (local/staging/prod-paper):
- Report Author:
- Report Date (UTC):
- Source of Truth (dashboards, queries, logs):

## 1) Run Summary

- Run start (UTC):
- Run end (UTC):
- Run duration:
- Objective of this run:
- Overall result (pass / conditional pass / fail):
- Executive summary (3-5 lines):

## 2) Platform Reliability

### Service Uptime

- API uptime (%):
- Orchestrator uptime (%):
- Database uptime (%):
- Mission Control availability (%):

### Operational Health Notes

- Any restart events:
- Any degraded windows:
- Time-to-recovery summary:

## 3) Core Pipeline Throughput

- Candle count:
- Signal count:
- Paper trades:
- Decision records created:
- Replay count:

## 4) Paper Trading Outcome

- Paper PnL (USD):
- Starting equity (USD):
- Ending equity (USD):
- Net return (%):

### Equity Curve Summary

- Trend shape (upward / flat / volatile / downward):
- Max drawdown (%):
- Largest single-day move (%):
- Stability commentary:

## 5) Strategy and Arena Outcomes

### Strategy Comparison

| Strategy | Signals | Trades | Win Rate | PnL (USD) | Quality Score | Notes |
|---|---:|---:|---:|---:|---:|---|
|  |  |  |  |  |  |  |
|  |  |  |  |  |  |  |

### Tournament Outcome

- Tournament champion:
- Runner-up:
- Champion rationale:

### Capital Allocation Recommendation

- Recommended allocation set:
- Recommendation rationale:
- Human review decision (accepted / rejected / deferred):

## 6) Research Campaign Outcomes

- Research campaigns completed:
- Candidates generated:
- Candidates evaluated:
- Evolution generations:
- Best evolved candidate:
- Best evolved candidate quality score:
- Research memory growth:

### Research Progress Notes

- Campaign completion quality:
- Candidate quality trend:
- Evolution effectiveness commentary:

## 7) Mission Control Alerts and Incidents

### Mission Control Alerts Observed

| Timestamp (UTC) | Alert Code | Severity | Duration | Resolved | Notes |
|---|---|---|---|---|---|
|  |  |  |  |  |  |
|  |  |  |  |  |  |

### Operational Incidents

| Incident ID | Start (UTC) | End (UTC) | Impact | Resolution |
|---|---|---|---|---|
|  |  |  |  |  |
|  |  |  |  |  |

## 8) Root-Cause Analysis

For each major alert/incident, complete:

- Incident/Alert:
- Primary root cause:
- Contributing factors:
- Detection path:
- Why existing guardrails did/did not catch it:
- Corrective action taken during run:
- Preventive action proposed:

## 9) Lessons Learned

- What worked well:
- What did not work well:
- Unexpected behavior observed:
- Monitoring blind spots discovered:

## 10) Recommended Changes Before Next Proving Run

List only concrete, bounded recommendations.

| Priority | Change | Why | Owner | Target Date |
|---|---|---|---|---|
| P0/P1/P2 |  |  |  |  |
| P0/P1/P2 |  |  |  |  |

## 11) Final Readiness Assessment

- Ready for longer proving run (yes / no / conditional):
- Conditions required before next run:
- Sign-off approver(s):
- Sign-off date (UTC):

## Appendix A: Data Extract References

- Mission Control snapshot link or export:
- Query set used for counts:
- Log bundle reference:
- Dashboard screenshots reference:

## Appendix B: Verification Checklist

- [ ] Run duration recorded
- [ ] Service uptime recorded
- [ ] Candle count recorded
- [ ] Signal count recorded
- [ ] Paper trades recorded
- [ ] Paper PnL recorded
- [ ] Equity curve summary completed
- [ ] Strategy comparison completed
- [ ] Tournament champion recorded
- [ ] Capital allocation recommendation recorded
- [ ] Research campaigns completed recorded
- [ ] Candidates generated recorded
- [ ] Candidates evaluated recorded
- [ ] Evolution generations recorded
- [ ] Best evolved candidate recorded
- [ ] Research memory growth recorded
- [ ] Mission Control alerts observed recorded
- [ ] Operational incidents recorded
- [ ] Root-cause analysis completed
- [ ] Lessons learned completed
- [ ] Recommended changes before next proving run completed
