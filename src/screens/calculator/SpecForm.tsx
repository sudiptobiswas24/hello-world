import { useMemo } from 'react';
import { num, qcModel, todayISO } from '../../engine';
import type { CalcMode, CalcResult, FoldType, Sku, Unit } from '../../engine';
import { useStore } from '../../state/store';
import { Field, NumberInput, Segmented, Select, TextInput, cx } from '../../components/ui';
import { Eq, toneClass } from './FormBits';
import { TYPE_OPTIONS_LONG, fillStrength, foldOptions, reqTone, unitEq } from './model';
import f from './SpecForm.module.css';

export interface SpecProps {
  sku: Sku;
  result: CalcResult;
}

const UNIT_OPTIONS: { value: Unit; label: string }[] = [
  { value: 'in', label: 'inches' },
  { value: 'cm', label: 'cm' },
  { value: 'mm', label: 'mm' },
];

const MODE_OPTIONS: { value: CalcMode; label: string }[] = [
  { value: 'wt', label: 'Weight → GSM' },
  { value: 'dn', label: 'Denier → weight' },
];

/** Job ID, date, party, bag name, type, unit, calculation basis. */
export function JobRow({ sku }: { sku: Sku }) {
  const { actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  return (
    <div className={f.jobRow}>
      <Field label="Job ID" className={f.jobIdField}>
        {(id) => <TextInput id={id} mono value={sku.jobId} onChange={bind('jobId')} placeholder="JOB-001" />}
      </Field>
      <Field label="Date" className={f.dateField}>
        {(id) => <TextInput id={id} type="date" value={sku.date || todayISO()} onChange={bind('date')} />}
      </Field>
      <Field label="Party name" className={f.grow}>
        {(id) => <TextInput id={id} value={sku.party} onChange={bind('party')} placeholder="Customer" />}
      </Field>
      <Field label="Bag name" className={f.grow}>
        {(id) => <TextInput id={id} value={sku.name} onChange={bind('name')} placeholder="e.g. 25 kg rice sack" />}
      </Field>
      <Field label="Bag type" className={f.typeField}>
        {(id) => <Select id={id} value={sku.type} onChange={(e) => actions.setSkuType(sku.id, e.target.value)} options={TYPE_OPTIONS_LONG} />}
      </Field>
      <Field label="Unit" className={f.unitField}>
        {(id) => <Select id={id} value={sku.unit} onChange={(e) => actions.setUnit(sku.id, e.target.value as Unit)} options={UNIT_OPTIONS} />}
      </Field>
      <Segmented label="Calculation basis" className={f.modeSeg} options={MODE_OPTIONS} value={sku.mode} onChange={(mode) => actions.updateSku(sku.id, { mode })} />
    </div>
  );
}

/** Fabric type, window, pattern, colour, master batch. */
export function FabricRow({ sku }: { sku: Sku }) {
  const { actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  return (
    <div className={cx(f.grid, f.g130)}>
      <Field label="Fabric type">
        {(id) => (
          <Select
            id={id}
            value={sku.fabricType}
            onChange={(e) => {
              const v = e.target.value as Sku['fabricType'];
              actions.updateSku(sku.id, { fabricType: v, window: v === 'milky' ? sku.window : 'none' });
            }}
          >
            <option value="milky">Milky white</option>
            <option value="natural">Natural</option>
          </Select>
        )}
      </Field>
      {sku.fabricType === 'milky' && (
        <Field label="Window">
          {(id) => (
            <Select id={id} value={sku.window} onChange={bind('window')}>
              <option value="none">No window</option>
              <option value="offcentre">Off-centre window</option>
              <option value="middle">Middle window</option>
            </Select>
          )}
        </Field>
      )}
      <Field label="Pattern">
        {(id) => (
          <Select id={id} value={sku.pattern} onChange={bind('pattern')} title="Job-card instruction only">
            <option value="none">None</option>
            <option value="checker">Checker</option>
            <option value="sideBorder">Side border</option>
            <option value="rangoli">Rangoli</option>
          </Select>
        )}
      </Field>
      <Field label="Fabric colour">{(id) => <TextInput id={id} value={sku.fabricColor} onChange={bind('fabricColor')} placeholder="e.g. White" />}</Field>
      <Field label="Master batch code">{(id) => <TextInput id={id} mono value={sku.mbCode} onChange={bind('mbCode')} placeholder="e.g. MB-104" />}</Field>
    </div>
  );
}

/** Width, length, fill, gusset, fold, hem, mesh, target weight or denier, ash, shrink. */
export function DimensionsRow({ sku, result: r }: SpecProps) {
  const { state, actions } = useStore();
  const bind = (k: keyof Sku) => actions.bindSku(sku.id, k);
  const qcLog = state.qcLog;
  const M = useMemo(() => qcModel({ qcLog }), [qcLog]);
  const fill = fillStrength(sku, r, M);
  const gussetOn = !!sku.gussetSide && sku.gussetSide !== 'none';
  return (
    <div className={cx(f.grid, f.g96)}>
      <Field label="Width">
        {(id) => (
          <>
            <NumberInput id={id} value={sku.width} placeholder="e.g. 18" onChange={bind('width')} tone={reqTone(num(sku.width) > 0)} />
            <Eq>{unitEq(sku.width, sku.unit)}</Eq>
          </>
        )}
      </Field>
      <Field label="Length">
        {(id) => (
          <>
            <NumberInput id={id} value={sku.length} placeholder="e.g. 30" onChange={bind('length')} tone={reqTone(num(sku.length) > 0)} />
            <Eq>{unitEq(sku.length, sku.unit)}</Eq>
          </>
        )}
      </Field>
      <Field label="Fill capacity/bag kg" help="How many kg of material the bag will be filled with — checked against predicted tape strength" className={f.minw130}>
        {(id) => (
          <>
            <NumberInput id={id} value={sku.fillKg} placeholder="e.g. 25" onChange={bind('fillKg')} />
            <div
              title="Predicted from the QC model: gpd from the tape-mix CC%; tape strength kg = gpd × denier ÷ 1000. Standard fills of 25 kg and above need ≥ 5 gpd"
              className={cx(f.pred, f.predNowrap, toneClass(fill.tone))}
            >
              {fill.text}
            </div>
          </>
        )}
      </Field>
      <Field label="Gusset" help="Job-card info only — not used in weight math">
        {(id) => (
          <div className={f.gussetRow}>
            <Select id={id} value={sku.gussetSide || 'none'} onChange={bind('gussetSide')}>
              <option value="none">None</option>
              <option value="single">Single</option>
              <option value="both">Both</option>
            </Select>
            {gussetOn && <NumberInput aria-label="Gusset size" value={sku.gusset} placeholder="e.g. 3" onChange={bind('gusset')} />}
          </div>
        )}
      </Field>
      <Field label="Bottom fold" className={f.foldCell}>
        {(id) => (
          <Select
            id={id}
            value={sku.foldType}
            onChange={(e) => actions.setFoldType(sku.id, e.target.value as FoldType)}
            title="Fold allowance is added to cut length automatically"
            options={foldOptions(sku.unit)}
          />
        )}
      </Field>
      <Field label="Hemming" className={f.minw110}>
        {(id) => (
          <Select id={id} value={sku.hemOn ? 'yes' : 'no'} onChange={(e) => actions.updateSku(sku.id, { hemOn: e.target.value === 'yes' })} title="Adds 1″ (25.4 mm) to cut length">
            <option value="no">No hem</option>
            <option value="yes">Hem (+1″)</option>
          </Select>
        )}
      </Field>
      <Field label="Warp mesh">{(id) => <NumberInput id={id} value={sku.warpMesh} placeholder="e.g. 10" onChange={bind('warpMesh')} />}</Field>
      <Field label="Weft mesh">{(id) => <NumberInput id={id} value={sku.weftMesh} placeholder="e.g. 10" onChange={bind('weftMesh')} />}</Field>
      {sku.mode === 'wt' ? (
        <Field label="Target wt g" brand>
          {(id) => <NumberInput id={id} value={sku.targetWt} placeholder="e.g. 68" onChange={bind('targetWt')} tone={reqTone(num(sku.targetWt) > 0, true)} />}
        </Field>
      ) : (
        <>
          <Field label="Warp denier" brand>
            {(id) => <NumberInput id={id} value={sku.warpD} placeholder="e.g. 900" onChange={bind('warpD')} tone="brand" />}
          </Field>
          <Field label="Weft denier" brand>
            {(id) => <NumberInput id={id} value={sku.weftD} placeholder="e.g. 900" onChange={bind('weftD')} tone="brand" />}
          </Field>
        </>
      )}
      <Field label="Ash %" help="Used to determine CC (CaCO₃ filler) %">
        {(id) => <NumberInput id={id} value={sku.ashPct} placeholder="e.g. 2" onChange={bind('ashPct')} />}
      </Field>
      <Field label="Shrink %">{(id) => <NumberInput id={id} value={sku.shrink} onChange={bind('shrink')} />}</Field>
    </div>
  );
}
