// 11-05 gap qa_start:PR-1718: slot wait logs, > 8 min alert (simulated clock), priority order merge > rerun (scripts/qa-slot.sh)
import test from 'node:test'
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, readFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SLOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'scripts', 'qa-slot.sh')
const sleepMs = (ms) => new Promise((r) => setTimeout(r, ms))
const until = async (fn, ms = 8000) => { const t = Date.now(); while (Date.now() - t < ms) { if (fn()) return true; await sleepMs(50) } return false }
const holder = (root) => spawn('bash', ['-c', `exec 9>${root}/nightly.lock; flock 9; sleep 60`], { stdio: 'ignore' })
// background waiter; ctl appends to $ROOT/ctl.log like run-nightly.sh's control.log
const waiter = (root, fn, env = {}) => {
  const p = spawn('bash', ['-c', `set -u; ctl() { printf '%s|%s|%s\\n' "$3" "$1" "$2" >>$ARGUS_ROOT/ctl.log; }; source ${SLOT}; exec 9>$ARGUS_ROOT/nightly.lock; ${fn} && echo "GOT $SLOT_MODE" || echo GAVEUP; sleep 30`],
    { env: { ...process.env, ARGUS_ROOT: root, PANOPTES_SLOT_POLL_S: '0.1', PANOPTES_TEST: '1', PI_TEST_MEM_AVAIL_MB: '9000', PANOPTES_VMSTAT: join(root, 'vmstat'), ...env } })
  p.out = ''; p.err = ''; p.stdout.on('data', (d) => { p.out += d }); p.stderr.on('data', (d) => { p.err += d }); return p
}
const setup = () => { const root = mkdtempSync(join(tmpdir(), 'argus-slotwait-')); mkdirSync(join(root, 'priority')); writeFileSync(join(root, 'vmstat'), ''); return root }
const ctlLog = (root) => existsSync(join(root, 'ctl.log')) ? readFileSync(join(root, 'ctl.log'), 'utf8') : ''

test('on-merge wait logs wait start and acquire with PR id and kind (control.log + stderr)', async () => {
  const root = setup(); const h = holder(root); await sleepMs(300)
  const w = waiter(root, 'slot_acquire_onmerge 9 1718 20', { SLOT_ID: 'PR-1718', SLOT_KIND: 'merge', PANOPTES_ONMERGE_MAX_CONC: '0', PANOPTES_ONMERGE_MAX_CONC_HARD: '0' })
  try {
    assert.ok(await until(() => ctlLog(root).includes('slot wait start')), 'wait start logged')
    assert.match(ctlLog(root), /info\|run-nightly slot wait start\|"id":"PR-1718","kind":"merge"/)
    h.kill(); assert.ok(await until(() => w.out.includes('GOT')))
    assert.match(ctlLog(root), /info\|run-nightly slot acquired\|"id":"PR-1718","kind":"merge","mode":"lock","waited_s":\d+/)
    assert.match(w.err, /slot wait start/); assert.match(w.err, /slot acquired/)
  } finally { h.kill(); w.kill() }
})

test('catch-up (normal) wait also logs start and acquire', async () => {
  const root = setup(); const h = holder(root); await sleepMs(300)
  const w = waiter(root, 'slot_acquire_normal 9 20', { SLOT_ID: 'PR-1600', SLOT_KIND: 'catch-up' })
  try {
    assert.ok(await until(() => ctlLog(root).includes('"id":"PR-1600","kind":"catch-up"')))
    h.kill(); assert.ok(await until(() => w.out.includes('GOT lock') || ctlLog(root).includes('slot acquired')))
    assert.match(ctlLog(root), /run-nightly slot acquired\|"id":"PR-1600","kind":"catch-up","mode":"lock"/)
  } finally { h.kill(); w.kill() }
})

test('wait > 8 min raises one alert (warn line + slot-wait-alerts state file), not at 8 min; acquire closes it (simulated clock)', async () => {
  const root = setup(); const clock = join(root, 'clock'); const t0 = 1_800_000_000; writeFileSync(clock, String(t0))
  const h = holder(root); await sleepMs(300)
  const w = waiter(root, 'slot_acquire_onmerge 9 1718 30', { SLOT_ID: 'PR-1718', SLOT_KIND: 'merge', PANOPTES_ONMERGE_MAX_CONC: '0', PANOPTES_ONMERGE_MAX_CONC_HARD: '0', PANOPTES_TEST_CLOCK: clock })
  const alert = () => join(root, 'slot-wait-alerts', `${w.pid}.json`)
  try {
    assert.ok(await until(() => ctlLog(root).includes('slot wait start')))
    writeFileSync(clock, String(t0 + 480)); await sleepMs(500)
    assert.ok(!existsSync(alert()), 'no alert at exactly 8 min'); assert.doesNotMatch(ctlLog(root), /slot wait alert/)
    writeFileSync(clock, String(t0 + 481))
    assert.ok(await until(() => existsSync(alert())), 'alert file after > 8 min')
    const a = JSON.parse(readFileSync(alert(), 'utf8'))
    assert.equal(a.id, 'PR-1718'); assert.equal(a.kind, 'merge'); assert.equal(a.state, 'waiting'); assert.equal(a.waited_s, 481)
    writeFileSync(clock, String(t0 + 600)); await sleepMs(400)
    assert.equal(ctlLog(root).match(/warn\|run-nightly slot wait alert/g).length, 1, 'alert raised once')
    h.kill(); assert.ok(await until(() => w.out.includes('GOT')))
    assert.ok(await until(() => JSON.parse(readFileSync(alert(), 'utf8')).state === 'acquired'))
  } finally { h.kill(); w.kill() }
})

test('a fresh merge overtakes a queued rerun for the next free slot (no cap change); catch-up stays behind both', async () => {
  const root = setup(); const h = holder(root)
  const conc = spawn('sleep', ['60']); writeFileSync(join(root, 'priority', `conc-${conc.pid}`), '')   // the one concurrent slot is taken
  await sleepMs(300)
  const env = { PANOPTES_ONMERGE_MAX_CONC: '1', PANOPTES_ONMERGE_MAX_CONC_HARD: '1' }
  const cu = waiter(root, 'slot_acquire_normal 9 30', { ...env, SLOT_ID: 'PR-1600', SLOT_KIND: 'catch-up' })
  const rr = waiter(root, 'slot_acquire_onmerge 9 1641 30', { ...env, SLOT_ID: 'PR-1641', SLOT_KIND: 'rerun' })   // queued first, polls fast
  await sleepMs(600)
  const mg = waiter(root, 'slot_acquire_onmerge 9 1718 30', { ...env, SLOT_ID: 'PR-1718', SLOT_KIND: 'merge', PANOPTES_SLOT_POLL_S: '1' })
  try {
    await sleepMs(1500)
    assert.equal(rr.out + mg.out + cu.out, '', 'nobody runs while both slots are taken')
    conc.kill(); rmSync(join(root, 'priority', `conc-${conc.pid}`), { force: true })   // one concurrent slot frees up
    assert.ok(await until(() => mg.out.includes('GOT concurrent')), 'merge got the freed slot')
    await sleepMs(500); assert.equal(rr.out, '', 'rerun still waiting'); assert.equal(cu.out, '')
    h.kill()                                                                                  // the lock frees: rerun is next, then catch-up
    assert.ok(await until(() => rr.out.includes('GOT lock')), 'rerun gets the next slot'); assert.equal(cu.out, '')
  } finally { h.kill(); conc.kill(); rr.kill(); mg.kill(); cu.kill() }
})

test('starvation-safe: a rerun waiting >= PANOPTES_SLOT_STARVE_S counts as a fresh merge (oldest first)', async () => {
  const root = setup(); const clock = join(root, 'clock'); const t0 = 1_800_000_000; writeFileSync(clock, String(t0))
  const h = holder(root); await sleepMs(300)
  const env = { PANOPTES_ONMERGE_MAX_CONC: '0', PANOPTES_ONMERGE_MAX_CONC_HARD: '0', PANOPTES_TEST_CLOCK: clock, PANOPTES_SLOT_STARVE_S: '1800' }
  const rr = waiter(root, 'slot_acquire_onmerge 9 1641 30', { ...env, SLOT_ID: 'PR-1641', SLOT_KIND: 'rerun' })
  await sleepMs(400); writeFileSync(clock, String(t0 + 1800))
  const mg = waiter(root, 'slot_acquire_onmerge 9 1718 30', { ...env, SLOT_ID: 'PR-1718', SLOT_KIND: 'merge' })
  try {
    await sleepMs(600); h.kill()
    assert.ok(await until(() => rr.out.includes('GOT lock')), 'aged rerun goes first'); assert.equal(mg.out, '')
  } finally { h.kill(); rr.kill(); mg.kill() }
})
