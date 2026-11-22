import type { CalcResult, Sku } from '../../../engine';
import { Field, LinesIcon, NumberInput } from '../../../components/ui';
import { useStore } from '../../../state/store';
import { boppView, hoursPlaceholder, lineCountOf, lineDec, lineInc } from './model';
import { CountField, FieldGrid, NotApplicable, PlanningSection } from './PlanningSection';
import { InfoTile, TileStrip } from './Tile';

/** BOPP film printing line planning for bags with BOPP film. */
export function BoppPlanning({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state, actions } = useStore();
  const v = boppView(state, sku, result);
  const id = sku.id;
  return (
    <PlanningSection
      title="BOPP printing planning"
      summary={v.summary}
      icon={LinesIcon}
      open={!!sku.boppPlanOpen}
      onToggle={() => actions.updateSku(id, { boppPlanOpen: !sku.boppPlanOpen })}
    >
      {v.applic ? (
        <>
          <FieldGrid>
            <Field label="Film speed m/min" help="ARC ~140–150 m/min, manual ~120 m/min">
              {(fid) => <NumberInput id={fid} value={sku.boppSpeed ?? ''} placeholder={v.speedPh} onChange={actions.bindSku(id, 'boppSpeed')} />}
            </Field>
            <Field label="Target efficiency %">
              {(fid) => (
                <NumberInput id={fid} max={100} tone={v.effMissing ? 'missing' : 'default'} value={sku.boppEff ?? ''} placeholder="e.g. 85" onChange={actions.bindSku(id, 'boppEff')} />
              )}
            </Field>
            <Field label="Working hours/day">
              {(fid) => <NumberInput id={fid} max={24} value={sku.boppHours ?? ''} placeholder={hoursPlaceholder(state)} onChange={actions.bindSku(id, 'boppHours')} />}
            </Field>
            <CountField
              label="Lines running"
              value={v.lines}
              onChange={(e) => actions.updateSku(id, { numBoppLine: lineCountOf(e.target.value) })}
              onInc={() => actions.updateSku(id, { numBoppLine: lineInc(sku.numBoppLine) })}
              onDec={() => actions.updateSku(id, { numBoppLine: lineDec(sku.numBoppLine) })}
              incLabel="Add line"
              decLabel="Remove line"
            />
          </FieldGrid>
          <TileStrip>
            <InfoTile label="Run speed" value={v.speedS} />
            <InfoTile label="BOPP meters (order)" value={v.metersS} />
            <InfoTile label="Effective speed" value={v.effSpeedS} />
            <InfoTile label="Effective runtime/day" value={v.runtimeS} title="Working hours/day × efficiency" />
            <InfoTile label="Meters printed/day" value={v.mPerDayS} title="Effective speed × 60 × working hours/day" />
            <InfoTile tone="accent" label="Number of days" value={v.daysS} />
          </TileStrip>
        </>
      ) : (
        <NotApplicable>BOPP planning applies to bags with BOPP film. Set a BOPP variant (SS or BS) to plan the BOPP line.</NotApplicable>
      )}
    </PlanningSection>
  );
}
