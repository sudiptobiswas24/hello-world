import { describe, expect, it } from 'vitest';
import { calc } from './calc';
import { defaultState, makeSku } from './defaults';
import { fmt, inr } from './format';
import { mixAnalysis } from './mix';
import { mixBlend, mixPct } from './materials';
import { plan, tapePlan } from './plan';
import { qcModel } from './qc';
import { migrateState } from './persist';
import type { AppState, Sku } from './types';

/**
 * Expected values are worked by hand from the documented formula chain
 * (cut length → meter weight → GSM → denier → consumption → cost), not
 * copied from the implementation.
 */
const IN_PER_M = 39.37;

const st = (patch: Partial<AppState> = {}): AppState => Object.assign(defaultState(), patch);
const sample = (id: string): Sku => defaultState().skus.find((s) => s.id === id)!;

describe('weight → GSM chain (s1: 25 kg rice sack, 18×30 in, 68 g, mesh 10×10, SFDS fold)', () => {
  const state = st();
  const s = sample('s1');
  const r = calc(state, s);

  // fold SFDS = 1.25″, no hem
  const cutIn = 30 + 1.25;
  // thread per seam row: 18″ = 0.4572 m × 1 × 4.5 × 1000 D / 9000 = 0.2286 g; double-stitched fold = 2 rows
  const thread = 2 * ((18 * 0.0254 * 4.5 * 1000) / 9000);
  const net = 68 - thread;
  const gpm = (net / cutIn) * IN_PER_M;
  const gsm = (gpm * IN_PER_M) / (2 * 18);
  const denier = (gsm * 9000) / (20 * IN_PER_M);

  it('derives cut length, meter weight, GSM and denier', () => {
    expect(r.cutIn).toBeCloseTo(31.25, 10);
    expect(r.net).toBeCloseTo(net, 10);
    expect(r.gpmTotal).toBeCloseTo(gpm, 10);
    expect(r.gpmUnlam).toBeCloseTo(gpm, 10); // no films on a plain bag
    expect(r.gsm).toBeCloseTo(gsm, 10);
    expect(r.gsm).toBeCloseTo(93.06, 1);
    expect(r.warpD).toBeCloseTo(denier, 10);
    expect(r.machD).toBeCloseTo(denier * 0.96, 10); // 4% shrink
    expect(r.totalG).toBe(68);
  });

  it('rounds order metres up to the next 100 m after the 3% fabric allowance', () => {
    const base = (50000 * cutIn) / IN_PER_M; // 39,687.6 m
    expect(r.fabricM).toBe(Math.ceil((base * 1.03) / 100) * 100);
    expect(r.fabricM).toBe(40900);
    expect(r.lamM).toBe(0);
    expect(r.boppM).toBe(0);
    expect(r.fabricKg).toBeCloseTo((40900 * gpm) / 1000, 8);
  });

  it('costs the tape blend at the weighted mix rate and builds the quote', () => {
    const blend = (96 * 102 + 2 * 165 + 2 * 28) / 100; // ₹101.78/kg
    expect(mixBlend(state, s.tapeMix)).toBeCloseTo(blend, 10);
    const tape = r.bom[0];
    expect(tape.name).toBe('Tape blend (PP granules + Colour master batch + CC (CaCO₃ filler))');
    expect(tape.rate).toBeCloseTo(blend, 10);
    expect(tape.cost).toBeCloseTo((r.fabricKg * blend) / 50000, 10);
    const threadLine = r.bom.find((b) => b.name.startsWith('Stitching thread'))!;
    expect(threadLine.g).toBeCloseTo(thread, 10);
    expect(threadLine.rate).toBe(160);

    // conversion: tape 7 + weaving 6 + cutting 4 ₹/kg on 68 g, then packing ₹0.15/bag
    expect(r.conv.map((c) => c.name)).toEqual(['Tape extrusion', 'Weaving', 'Cutting + stitching', 'Bale packing']);
    expect(r.convCost).toBeCloseTo((7 + 6 + 4) * 0.068 + 0.15, 10);

    const mat = r.bom.reduce((a, b) => a + b.cost, 0);
    const cost = (mat + r.convCost) * 1.08; // 8% overhead
    expect(r.costBag).toBeCloseTo(cost, 10);
    expect(r.quote).toBeCloseTo(cost * 1.12, 10); // 12% margin
    expect(r.gstAmt).toBeCloseTo(r.quote * 50000 * 0.18, 6);
    expect(r.bales).toBe(100); // 50,000 ÷ 500 per bale
    expect(r.errCount).toBe(0);
  });
});

describe('films and lamination (s2: BOPP flour bag)', () => {
  const r = calc(st(), sample('s2'));
  it('subtracts BOPP film (single side) and lam coating before GSM', () => {
    expect(r.boppWpm).toBeCloseTo((515 * 10) / 1000, 10); // SS = not doubled
    expect(r.lamWpm).toBe(12);
    expect(r.gpmLam).toBeCloseTo(r.gpmTotal - 5.15, 10);
    expect(r.gpmUnlam).toBeCloseTo(r.gpmTotal - 5.15 - 12, 10);
    expect(r.conv.map((c) => c.name)).toContain('Lamination');
    expect(r.conv.map((c) => c.name)).toContain('Printing');
  });
  it('doubles film weight only for both-side BOPP', () => {
    const bs = calc(st(), { ...sample('s2'), boppVariant: 'BS' });
    expect(bs.boppWpm).toBeCloseTo(2 * 5.15, 10);
  });
  it('halves window film', () => {
    const w = calc(st(), { ...sample('s2'), metallic: 'window', metW: 400, metMic: 15 });
    expect(w.metWpm).toBeCloseTo((400 * 15) / 1000 / 2, 10);
  });
});

describe('denier → weight mode is the inverse of weight → GSM', () => {
  it('round-trips a weight-mode result back to the same bag weight', () => {
    const state = st();
    for (const id of ['s1', 's2', 's3']) {
      const wt = calc(state, sample(id));
      const dn = calc(state, { ...sample(id), mode: 'dn', warpD: wt.warpD, weftD: wt.weftD });
      expect(dn.gsm).toBeCloseTo(wt.gsm, 8);
      expect(dn.totalG).toBeCloseTo(wt.totalG, 8);
    }
  });
  it('converts cm and mm to inches', () => {
    const cm = calc(st(), sample('s4'));
    expect(cm.W).toBeCloseTo(55 / 2.54, 10);
    const mm = calc(st(), { ...sample('s1'), unit: 'mm', width: 457.2, length: 762 });
    expect(mm.W).toBeCloseTo(18, 10);
    expect(mm.L).toBeCloseTo(30, 10);
  });
});

describe('add-ons', () => {
  it('weighs a liner from W × L × micron (two plies, density 0.92)', () => {
    const s = { ...sample('s1'), linerOn: true, linerWmm: 450, linerLmm: 760, linerMic: 40 };
    const r = calc(st(), s);
    const liner = 2 * 0.45 * 0.76 * 40 * 0.92;
    expect(r.bom.find((b) => b.name === 'LDPE liner')!.g).toBeCloseTo(liner, 10);
    expect(r.conv.map((c) => c.name)).toContain('Liner insertion');
  });
  it('gives box bags in handle-loop style a 2 g handle by default, but never leno bags', () => {
    const box = calc(st(), { ...sample('s5'), boxStyle: 'handleLoop', handleWt: '' });
    expect(box.bom.find((b) => b.name === 'Handle strip')!.g).toBeCloseTo(2, 10);
    expect(box.conv.map((c) => c.name)).toContain('Handle attachment');
    expect(box.conv.map((c) => c.name)).not.toContain('D-cut punching');
    const leno = calc(st(), { ...sample('s4'), handleOn: true });
    expect(leno.bom.find((b) => b.name === 'Handle strip')).toBeUndefined();
  });
  it('adds a 1″ hem to cut length', () => {
    const r = calc(st(), { ...sample('s1'), hemOn: true });
    expect(r.cutIn).toBeCloseTo(32.25, 10);
  });
});

describe('validation', () => {
  it('flags missing required inputs as errors', () => {
    const s = makeSku({ id: 'x', name: 'blank', width: '', length: '', qty: '', targetWt: '' });
    const msgs = calc(st({ skus: [s] }), s).issues.filter((i) => i.level === 'error').map((i) => i.msg);
    expect(msgs).toContain('Width is required. Enter a value greater than 0.');
    expect(msgs).toContain('Length is required. Enter a value greater than 0.');
    expect(msgs).toContain('Order qty is required. Enter at least 1 bag.');
    expect(msgs).toContain('Target wt g is required. Enter the finished bag weight.');
  });
  it('warns on a duplicated job ID', () => {
    const state = st();
    const dup = { ...sample('s2'), id: 'dup', jobId: 'JOB-101' };
    state.skus = state.skus.concat([dup]);
    const msgs = calc(state, dup).issues.map((i) => i.msg);
    expect(msgs).toContain('Job ID “JOB-101” is used by another bag. Make it unique before dispatch.');
  });
  it('requires lamination GPM when laminated', () => {
    const r = calc(st(), { ...sample('s2'), lamVal: '' });
    expect(r.issues.map((i) => i.msg)).toContain('Lamination GPM is required. Enter the coating weight in g/m.');
  });
});

describe('rates', () => {
  it('uses the latest effective history entry over the list rate, ignoring future-dated ones', () => {
    const state = st({
      rateHistory: [
        { m: 'pp', d: '2026-01-05', eff: '2026-01-05', r: 100 },
        { m: 'pp', d: '2026-02-04', eff: '2026-02-04', r: 110 },
        { m: 'pp', d: '2026-02-04', eff: '2999-01-01', r: 999 },
      ],
    });
    expect(mixBlend(state, [{ m: 'pp', wt: 1 }])).toBe(110);
  });
});

describe('QC model and mix analysis', () => {
  it('starts from the 38-bag baseline', () => {
    expect(qcModel({ qcLog: [] })).toEqual({ s0: 5.74, s1: -0.033, e0: 24.8, e1: -0.144, nLog: 0 });
  });
  it('refits once 5 tests are logged and blends by sample count', () => {
    // perfect line gpd = 6 − 0.05·cc
    const qcLog = [0, 5, 10, 15, 20].map((cc) => ({ cc, gpd: 6 - 0.05 * cc }));
    const m = qcModel({ qcLog });
    const w = 5 / (5 + 38);
    expect(m.s0).toBeCloseTo(5.74 * (1 - w) + 6 * w, 10);
    expect(m.s1).toBeCloseTo(-0.033 * (1 - w) - 0.05 * w, 10);
    expect(m.e0).toBe(24.8); // no elongation logged
  });
  it('flags master batch in a white bag and offers to remove it', () => {
    const state = st();
    const s = sample('s1');
    const a = mixAnalysis(state, s, calc(state, s));
    expect(mixPct(s.tapeMix).cc).toBeCloseTo(2, 10);
    expect(a.gpd).toBeCloseTo(5.74 - 0.033 * 2, 10);
    expect(a.mixGrade).toBe('Standard · ≥ 5 gpd');
    const mb = a.findings[0];
    expect(mb.tone).toBe('error');
    expect(mb.msg).toBe('White / milky fabric — colour master batch is not needed (mix has 2.0% MB).');
    expect(mb.fixMix).toEqual([{ m: 'pp', wt: 96 }, { m: 'cc', wt: 2 }]);
  });
  it('caps CC at the strength limit', () => {
    const state = st();
    const s = { ...sample('s1'), fabricColor: 'Blue', tapeMix: [{ m: 'pp', wt: 60 }, { m: 'cc', wt: 38 }, { m: 'colmb', wt: 2 }] };
    const a = mixAnalysis(state, s, calc(state, s));
    const maxCC = (5.74 - 5) / 0.033; // 22.4%
    const f = a.findings.find((x) => x.fixLabel?.startsWith('Cap CC'))!;
    expect(f.fixLabel).toBe('Cap CC at 22%');
    const cc = f.fixMix!.find((x) => x.m === 'cc')!.wt as number;
    expect((cc / (62 + cc)) * 100).toBeCloseTo(maxCC, 1);
    expect(a.grade).toBe('local');
  });
});

describe('planning', () => {
  it('sizes tapes from the stretch ratio and finds the bottleneck', () => {
    const state = st();
    const s = sample('s1');
    const r = calc(state, s);
    const tp = tapePlan(state, s, r);
    const spacer = Math.sqrt(6) * 2.45;
    expect(tp.spacer).toBeCloseTo(spacer, 10);
    expect(tp.nt).toBe(Math.floor((1450 - 2 * spacer) / spacer));
    expect(tp.prod).toBeCloseTo((r.machD * 350 * tp.nt) / 150000, 10);

    const p = plan(state, s, r);
    const cut = p.rows.find((x) => x.stage === 'Cutting + stitching')!;
    expect(cut.capN).toBeCloseTo(1800 * 3 * 0.85, 10);
    expect(cut.days).toBeCloseTo(50000 / 4590 / 20, 10);
    // looms have no PPM set on the sample → 0 days, so tape or cutting is slowest
    expect(p.rows.find((x) => x.stage === 'Weaving')!.cap).toBe('set loom PPM');
    expect(p.maxDays).toBe(Math.max(...p.rows.map((x) => x.days).filter(Number.isFinite)));
  });
});

describe('formatting', () => {
  it('uses Indian digit grouping', () => {
    expect(fmt(1234567.891)).toBe('12,34,567.89');
    expect(fmt(0.5, 0)).toMatch(/^[01]$/);
    expect(fmt(NaN)).toBe('—');
    expect(inr(98765.4321)).toBe('₹ 98,765.43');
    expect(inr(Infinity)).toBe('—');
  });
});

describe('persistence migration', () => {
  it('returns sample data for empty or corrupt input', () => {
    expect(migrateState(null).skus).toHaveLength(5);
    expect(migrateState('{not json').skus).toHaveLength(5);
  });
  it('maps legacy bag types and fills new fields', () => {
    const raw = JSON.stringify({ skus: [{ id: 'a', name: 'old', type: 'handle', width: 10, length: 12 }], matSections: [] });
    const out = migrateState(raw);
    expect(out.skus[0].type).toBe('box');
    expect(out.skus[0].boxStyle).toBe('handleLoop');
    expect(out.skus[0].handleOn).toBe(true);
    expect(out.skus[0].tapeMix.length).toBeGreaterThan(0);
    expect(out.matSections.map((x) => x.id)).toEqual(['tape', 'lam', 'print', 'boppSec', 'finish']);
  });
  it('moves pre-section flat rates into material sections', () => {
    const out = migrateState(JSON.stringify({ mats: { pp: 99, filler: 31 } }));
    const tape = out.matSections.find((x) => x.id === 'tape')!;
    expect(tape.items.find((x) => x.id === 'pp')!.rate).toBe(99);
    expect(tape.items.find((x) => x.id === 'cc')!.rate).toBe(31);
  });
});
