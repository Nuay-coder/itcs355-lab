// ITCS355 Lab 3 Task 3, Step 3 — breaking point.
//
//   python scripts/run_breaking_point.py --endpoint itcs355-predict
//
// A single ramping-vus scenario that steps concurrency up through STAGE_TARGETS,
// STAGE_SECONDS at each step. Each request is tagged into a per-stage Trend/Rate pair
// (via k6/execution's currentTestRunDuration, not wall-clock, so it stays correct
// regardless of when the process actually started), so the summary reports p95/error
// rate PER STAGE, not just one number for the whole ramp — that per-stage breakdown is
// what "the concurrency at which p95 crosses the target" actually means.
//
// dropped_iterations > 0 in the summary means k6 itself could not sustain the requested
// VU count on this machine — a signal to check before blaming the service for a
// "breaking point" that was actually the client.

import http from 'k6/http';
import { check } from 'k6';
import { Trend, Rate } from 'k6/metrics';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';

const holdoutRows = new SharedArray('holdout', function () {
  return JSON.parse(open('./holdout_rows.json'));
});

const TARGET = __ENV.TARGET;
const TOKEN = __ENV.TOKEN;
const STAGE_SECONDS = Number(__ENV.STAGE_SECONDS || 20);
const STAGE_TARGETS = (__ENV.STAGE_TARGETS || '10,20,30,40,60,80,100')
  .split(',')
  .map(Number);

if (!TARGET) throw new Error('set -e TARGET=https://<endpoint>/predict');
if (!TOKEN) throw new Error('set -e TOKEN=<identity token>');

const stageLatency = {};
const stageFailures = {};
for (const t of STAGE_TARGETS) {
  stageLatency[t] = new Trend(`latency_stage_${t}`);
  stageFailures[t] = new Rate(`failures_stage_${t}`);
}

export const options = {
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    ramp: {
      executor: 'ramping-vus',
      startVUs: 1,
      stages: STAGE_TARGETS.map((t) => ({ duration: `${STAGE_SECONDS}s`, target: t })),
      gracefulRampDown: '5s',
    },
  },
};

function currentStage() {
  const elapsedS = exec.instance.currentTestRunDuration / 1000;
  let acc = 0;
  for (const t of STAGE_TARGETS) {
    acc += STAGE_SECONDS;
    if (elapsedS < acc) return t;
  }
  return null;
}

function randomPayload() {
  return holdoutRows[Math.floor(Math.random() * holdoutRows.length)];
}

export default function () {
  const stage = currentStage();
  const res = http.post(TARGET, JSON.stringify(randomPayload()), {
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${TOKEN}` },
  });
  if (stage !== null) {
    stageLatency[stage].add(res.timings.duration);
    stageFailures[stage].add(res.status !== 200);
  }
  check(res, { 'status is 200': (r) => r.status === 200 });
}

export function handleSummary(data) {
  const m = data.metrics;
  const lines = [`=== breaking point (stages: ${STAGE_TARGETS.join(', ')}; ${STAGE_SECONDS}s each) ===`];
  for (const t of STAGE_TARGETS) {
    const lat = m[`latency_stage_${t}`];
    const fail = m[`failures_stage_${t}`];
    const p95 = lat && lat.values['p(95)'] !== undefined ? lat.values['p(95)'].toFixed(2) : 'n/a';
    // Trend metrics don't expose a count field even with summaryTrendStats; the
    // paired Rate metric's passes+fails is the actual per-stage request count.
    const count = fail ? fail.values.passes + fail.values.fails : 'n/a';
    const rate = fail ? fail.values.rate : 'n/a';
    lines.push(`  VUs=${t}: n=${count} p95=${p95}ms error_rate=${rate}`);
  }
  lines.push(`dropped_iterations: ${m.dropped_iterations ? m.dropped_iterations.values.count : 0}`);
  lines.push('wrote reports/raw/lab3-breaking-point.json');
  return {
    'reports/raw/lab3-breaking-point.json': JSON.stringify(data, null, 2),
    stdout: lines.join('\n') + '\n',
  };
}
