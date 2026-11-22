import { matGet, num } from '../../engine';
import type { Sku } from '../../engine';
import { useStore } from '../../state/store';
import { Field, NumberInput, SectionTitle, Select, TextInput, cx } from '../../components/ui';
import { Eq, Group, SubField } from './FormBits';
import type { SpecProps } from './SpecForm';
import {
  boppWidthEq,
  filmNote,
  filmRec,
  handleForced,
  inkCodeRows,
  inkHeading,
  inkOptions,
  joinYarn,
  linerEq,
  linerFactor,
  linerGrams,
  linerShown,
  metRec,
  reqTone,
  toggleInk,
  yarnColors,
} from './model';
import f from './SpecForm.module.css';

/** Lamination & films — shown for types that allow lamination, BOPP or metallic film. */
export function FilmsSection({ sku, result: r }: SpecProps) {
  const { actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  const cfg = r.cfg;
  if (!(cfg.aLam || cfg.aBopp || cfg.aMet)) return null;
  const hasLamSel = sku.lamSide !== 'none';
  const showBopp = sku.boppVariant !== 'none';
  const showMet = sku.metallic !== 'none';
  const fr = filmRec(sku, r);
  const mr = metRec(sku);
  const note = filmNote(sku, r);
  return (
    <div>
      <SectionTitle>Lamination &amp; films</SectionTitle>
      <div className={cx(f.grid, f.g110)}>
        {cfg.aLam && (
          <>
            <Field label="Lamination">
              {(id) => (
                <Select id={id} value={sku.lamSide} onChange={bind('lamSide')}>
                  <option value="none">None (UL)</option>
                  <option value="single">Single</option>
                  <option value="double">Double</option>
                </Select>
              )}
            </Field>
            {hasLamSel && (
              <Field label="Lam GPM g/m">
                {(id) => <NumberInput id={id} value={sku.lamVal} onChange={bind('lamVal')} placeholder="e.g. 12" tone={reqTone(num(sku.lamVal) > 0)} />}
              </Field>
            )}
          </>
        )}
        {cfg.aBopp && (
          <Field label="BOPP film">
            {(id) => (
              <Select id={id} value={sku.boppVariant} onChange={bind('boppVariant')}>
                <option value="none">None</option>
                <option value="SS">SS BOPP (single side)</option>
                <option value="BS">BS BOPP (both side)</option>
              </Select>
            )}
          </Field>
        )}
        {showBopp && (
          <>
            <Field label="Film width mm">
              {(id) => (
                <>
                  <NumberInput id={id} value={sku.boppW} onChange={bind('boppW')} tone={reqTone(num(sku.boppW) > 0)} />
                  <Eq>{boppWidthEq(sku.boppW)}</Eq>
                  {fr.show && (
                    <button type="button" className={f.recBtn} title="Recommended film width = fabric width + 10 mm" onClick={() => actions.updateSku(sku.id, { boppW: fr.value })}>
                      Use {fr.label} mm (fabric + 10)
                    </button>
                  )}
                </>
              )}
            </Field>
            <Field label="Micron">
              {(id) => <NumberInput id={id} value={sku.boppMic} onChange={bind('boppMic')} tone={reqTone(num(sku.boppMic) > 0)} />}
            </Field>
            <Field label="Finish">
              {(id) => (
                <Select id={id} value={sku.finish} onChange={bind('finish')}>
                  <option value="glossy">Glossy</option>
                  <option value="matt">Matt</option>
                  <option value="metallic">Metallic</option>
                </Select>
              )}
            </Field>
          </>
        )}
        {cfg.aMet && (
          <Field label="Metallic / window">
            {(id) => (
              <Select id={id} value={sku.metallic} onChange={bind('metallic')}>
                <option value="none">None</option>
                <option value="window">Window (half)</option>
                <option value="full">Full metallic</option>
              </Select>
            )}
          </Field>
        )}
        {showMet && (
          <>
            <Field label="Met. size mm">
              {(id) => (
                <>
                  <NumberInput id={id} value={sku.metW} onChange={bind('metW')} />
                  {mr.show && (
                    <button type="button" className={f.recBtn} title={mr.title} onClick={() => actions.updateSku(sku.id, { metW: mr.value })}>
                      Use {mr.label} mm {mr.why}
                    </button>
                  )}
                </>
              )}
            </Field>
            <Field label="Met. micron">{(id) => <NumberInput id={id} value={sku.metMic} onChange={bind('metMic')} />}</Field>
          </>
        )}
      </div>
      {note && <div className={f.filmNote}>{note}</div>}
    </div>
  );
}

/** Print, liner & extras — print sides/colours, ink choice + codes, leno stitch, box style. */
export function ExtrasSection({ sku, result: r }: SpecProps) {
  const { state, actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  const showColors = sku.printed === 'yes';
  const showBothColors = showColors && sku.printSides === 'both';
  const inks = inkOptions(state, sku);
  const hasInkSel = (sku.inkSel || []).some((id) => matGet(state, id));
  const codes = inkCodeRows(state, sku);
  return (
    <div>
      <SectionTitle>Print, liner &amp; extras</SectionTitle>
      <div className={cx(f.grid, f.g110)}>
        {r.cfg.aPrint && (
          <Field label={sku.type === 'bopp' ? 'Printed' : 'Flexo print'}>
            {(id) => (
              <Select id={id} value={sku.printed} onChange={bind('printed')}>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </Select>
            )}
          </Field>
        )}
        {showColors && (
          <Field label="Flexo sides">
            {(id) => (
              <Select id={id} value={sku.printSides} onChange={bind('printSides')}>
                <option value="single">Single side</option>
                <option value="both">Both sides</option>
              </Select>
            )}
          </Field>
        )}
        {showColors && <Field label="Colors (front)">{(id) => <NumberInput id={id} value={sku.colors} onChange={bind('colors')} />}</Field>}
        {showBothColors && <Field label="Colors (back)">{(id) => <NumberInput id={id} value={sku.colorsBack} onChange={bind('colorsBack')} />}</Field>}
        {showColors && (
          <Group label={inkHeading(sku.type)} className={f.full}>
            <div className={f.inkPills}>
              {inks.map((ik) => (
                <label key={ik.id} className={f.inkPill}>
                  <input type="checkbox" checked={ik.checked} onChange={() => actions.updateSku(sku.id, (k) => ({ inkSel: toggleInk(k.inkSel, ik.id) }))} />
                  {ik.name}
                </label>
              ))}
            </div>
            {hasInkSel && (
              <div className={f.inkCodes}>
                {codes.map((ic) => (
                  <label key={ic.id} className={f.inkCode}>
                    <span className={f.inkCodeName}>{ic.name}</span>
                    <input
                      className={f.codeInput}
                      value={ic.code}
                      placeholder="code no."
                      onChange={(e) => {
                        const v = e.target.value;
                        actions.updateSku(sku.id, (k) => ({ inkCodes: Object.assign({}, k.inkCodes, { [ic.id]: v }) }));
                      }}
                    />
                  </label>
                ))}
              </div>
            )}
          </Group>
        )}
        {sku.type === 'leno' && (
          <Field label="Leno stitch">
            {(id) => (
              <Select id={id} value={sku.lenoStitch} onChange={bind('lenoStitch')}>
                <option value="corner">Corner stitch</option>
                <option value="noCorner">Without corner stitch</option>
              </Select>
            )}
          </Field>
        )}
        {sku.type === 'box' && (
          <Field label="Box style">
            {(id) => (
              <Select id={id} value={sku.boxStyle} onChange={(e) => actions.updateSku(sku.id, { boxStyle: e.target.value })}>
                <option value="dcut">D-cut</option>
                <option value="handleLoop">Handle loop</option>
                <option value="flap">Flap / box bottom</option>
              </Select>
            )}
          </Field>
        )}
      </div>
    </div>
  );
}

/** Liner & handle add-ons, plus stitching yarn colours and denier. */
export function AddonsSection({ sku, result: r }: SpecProps) {
  const { actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  const factor = linerFactor(sku.unit);
  const forced = handleForced(sku);
  const handleOnEff = !!sku.handleOn || forced;
  const yarns = yarnColors(sku);
  const writeYarn = (a: string[]) => actions.updateSku(sku.id, { yarnColor: joinYarn(a) });
  return (
    <div>
      <SectionTitle>Liner &amp; handle add-ons</SectionTitle>
      <div className={f.stack12}>
        {r.cfg.aLiner && (
          <div>
            <label className={f.checkRow}>
              <input type="checkbox" checked={!!sku.linerOn} onChange={() => actions.updateSku(sku.id, { linerOn: !sku.linerOn })} /> Liner (LDPE inside)
            </label>
            {sku.linerOn && (
              <div className={cx(f.grid, f.g100, f.indent)}>
                <SubField label={'Width ' + sku.unit}>
                  {(id) => (
                    <>
                      <NumberInput id={id} value={linerShown(sku.linerWmm, sku.unit)} onChange={(e) => actions.updateSku(sku.id, { linerWmm: num(e.target.value) * factor })} />
                      <Eq>{linerEq(sku.linerWmm, sku.unit)}</Eq>
                    </>
                  )}
                </SubField>
                <SubField label={'Length ' + sku.unit}>
                  {(id) => (
                    <>
                      <NumberInput id={id} value={linerShown(sku.linerLmm, sku.unit)} onChange={(e) => actions.updateSku(sku.id, { linerLmm: num(e.target.value) * factor })} />
                      <Eq>{linerEq(sku.linerLmm, sku.unit)}</Eq>
                    </>
                  )}
                </SubField>
                <SubField label="Micron">{(id) => <NumberInput id={id} value={sku.linerMic} onChange={bind('linerMic')} />}</SubField>
                <SubField label="Or fixed g">
                  {(id) => <NumberInput id={id} value={sku.linerG} onChange={bind('linerG')} title="Used if width/length/micron left blank" />}
                </SubField>
                <div className={f.linerCalc}>
                  ≈ <b>{linerGrams(sku)} g</b>
                </div>
              </div>
            )}
          </div>
        )}
        {r.cfg.aHandle && (
          <div>
            <label className={f.checkRow}>
              <input type="checkbox" checked={!!sku.handleOn} onChange={() => actions.updateSku(sku.id, { handleOn: !sku.handleOn })} disabled={forced} /> Handle
            </label>
            {handleOnEff && (
              <div className={cx(f.grid, f.g120, f.indent)}>
                <SubField label="Handle weight">
                  {(id) => (
                    <Select id={id} value={String(sku.handleWt)} onChange={bind('handleWt')}>
                      <option value="1.5">1.5 g</option>
                      <option value="2">2 g</option>
                      <option value="2.5">2.5 g</option>
                      <option value="3">3 g</option>
                    </Select>
                  )}
                </SubField>
                <SubField label="Handle colour">{(id) => <TextInput id={id} value={sku.handleColor} onChange={bind('handleColor')} placeholder="e.g. Red" />}</SubField>
              </div>
            )}
          </div>
        )}
        <div>
          <div className={f.subHead}>Stitching yarn</div>
          <div className={cx(f.grid, f.g120, f.indent)}>
            <Group small label={'Yarn colour' + (yarns.length > 1 ? 's' : '')}>
              <div className={f.yarnList}>
                {yarns.map((v, i) => (
                  <div key={i} className={f.yarnItem}>
                    <TextInput
                      aria-label={'Yarn colour ' + (i + 1)}
                      value={v}
                      placeholder="e.g. White"
                      onChange={(e) => {
                        const a = yarns.slice();
                        a[i] = e.target.value;
                        writeYarn(a);
                      }}
                    />
                    {yarns.length > 1 && (
                      <button type="button" className={f.removeX} title="Remove colour" aria-label="Remove colour" onClick={() => writeYarn(yarns.filter((_, j) => j !== i))}>
                        ×
                      </button>
                    )}
                  </div>
                ))}
                {yarns.length < 4 && (
                  <button type="button" className={f.dashedBtn} onClick={() => writeYarn(yarns.concat(['']))}>
                    + Colour
                  </button>
                )}
              </div>
            </Group>
            <SubField label="Yarn denier">
              {(id) => <NumberInput id={id} value={sku.yarnDenier} onChange={bind('yarnDenier')} placeholder="1000 (standard)" />}
            </SubField>
          </div>
        </div>
      </div>
    </div>
  );
}
