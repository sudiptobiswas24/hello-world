import type { CalcResult, Sku } from '../../../engine';
import { Field, NumberInput, PulseIcon, Select } from '../../../components/ui';
import { useStore } from '../../../state/store';
import { hoursPlaceholder, lineCountOf, lineDec, lineInc, tapeView } from './model';
import { CountField, FieldGrid, PlanningSection, planningClasses } from './PlanningSection';
import { InfoTile, TileStrip, TileSub } from './Tile';

/** Tape extrusion line: spacer, tape count, output and days for the order's tape. */
export function TapePlanning({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state, actions } = useStore();
  const v = tapeView(state, sku, result);
  const id = sku.id;
  return (
    <PlanningSection
      title="Tape plant planning"
      summary={v.summary}
      icon={PulseIcon}
      open={!!sku.tapeOpen}
      onToggle={() => actions.updateSku(id, { tapeOpen: !sku.tapeOpen })}
    >
      <FieldGrid>
        <Field label="Film width mm">
          {(fid) => <NumberInput id={fid} value={sku.tapeOutW ?? ''} placeholder="e.g. 1450" onChange={actions.bindSku(id, 'tapeOutW')} />}
        </Field>
        <Field label="Tape width mm">
          {(fid) => (
            <Select id={fid} className={planningClasses.plainSelect} value={v.tapeWidth} onChange={actions.bindSku(id, 'tapeWidth')}>
              <option value="2.45">2.45 mm</option>
              <option value="2.48">2.48 mm</option>
              <option value="2.5">2.50 mm</option>
            </Select>
          )}
        </Field>
        <Field label="Stretching ratio" help="Spacer = √(stretching ratio) × tape width, rounded up">
          {(fid) => <NumberInput id={fid} step={0.1} value={sku.tapeDraw ?? ''} placeholder="e.g. 6" onChange={actions.bindSku(id, 'tapeDraw')} />}
        </Field>
        <Field label="Final line speed m/min">
          {(fid) => <NumberInput id={fid} value={sku.tapeSpeed ?? ''} placeholder="e.g. 350" onChange={actions.bindSku(id, 'tapeSpeed')} />}
        </Field>
        <Field label="Target efficiency %">
          {(fid) => (
            <NumberInput id={fid} max={100} tone={v.effMissing ? 'missing' : 'default'} value={sku.tapeEff ?? ''} placeholder="e.g. 85" onChange={actions.bindSku(id, 'tapeEff')} />
          )}
        </Field>
        <Field label="Working hours/day">
          {(fid) => <NumberInput id={fid} max={24} value={sku.tapeHours ?? ''} placeholder={hoursPlaceholder(state)} onChange={actions.bindSku(id, 'tapeHours')} />}
        </Field>
        <Field label="Winders in plant" help="Winders available on the line">
          {(fid) => <NumberInput id={fid} value={sku.winders ?? ''} onChange={actions.bindSku(id, 'winders')} />}
        </Field>
        <CountField
          label="Lines running"
          value={v.lines}
          onChange={(e) => actions.updateSku(id, { numTapeLine: lineCountOf(e.target.value) })}
          onInc={() => actions.updateSku(id, { numTapeLine: lineInc(sku.numTapeLine) })}
          onDec={() => actions.updateSku(id, { numTapeLine: lineDec(sku.numTapeLine) })}
          incLabel="Add line"
          decLabel="Remove line"
        />
      </FieldGrid>
      <TileStrip>
        <InfoTile label="Tape denier" value={v.tapeDenierS} title="From the bag's machine denier" />
        <InfoTile label="Spacer size" value={v.spacerS} title="Spacer = √(stretching ratio) × tape width" />
        <InfoTile label="Useful film width" value={v.usefulWS} title="Useful width = film width − 2 × spacer" />
        <InfoTile
          tone={v.overWinders ? 'alert' : 'plain'}
          label="Number of tapes"
          title="No. of tapes = useful width ÷ spacer size"
          value={
            <>
              {v.tapeCountS} <TileSub>{v.tapeCountSub}</TileSub>
            </>
          }
          note={v.winderNote}
          noteAlert={v.overWinders}
        />
        <InfoTile label="Production" value={v.prodS} title="Production = denier × final line speed × no. of tapes ÷ 150000" />
        <InfoTile label="Output @ efficiency" value={v.effOutS} />
        <InfoTile label="Effective runtime/day" value={v.runtimeS} title="Working hours/day × efficiency" />
        <InfoTile label="Order tape (warp + weft)" value={v.tapeKgS} />
        <InfoTile tone="accent" label="Number of days" value={v.daysS} />
      </TileStrip>
    </PlanningSection>
  );
}
