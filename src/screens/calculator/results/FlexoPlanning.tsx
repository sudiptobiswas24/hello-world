import type { CalcResult, Sku } from '../../../engine';
import { Field, NumberInput, TapeLineIcon } from '../../../components/ui';
import { useStore } from '../../../state/store';
import { flexoView, hoursPlaceholder, lineCountOf, lineDec, lineInc } from './model';
import { CountField, FieldGrid, NotApplicable, PlanningSection } from './PlanningSection';
import { InfoTile, TileStrip } from './Tile';

/** Flexo press planning for printed non-BOPP bags. */
export function FlexoPlanning({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state, actions } = useStore();
  const v = flexoView(state, sku, result);
  const id = sku.id;
  return (
    <PlanningSection
      title="Flexo printing planning"
      summary={v.summary}
      icon={TapeLineIcon}
      open={!!sku.flexoOpen}
      onToggle={() => actions.updateSku(id, { flexoOpen: !sku.flexoOpen })}
    >
      {v.applic ? (
        <>
          <FieldGrid>
            <Field label="Web speed m/min" help="≤2 col 70, 3–4 col 62, >4 col 55 m/min" hint={v.recS}>
              {(fid) => <NumberInput id={fid} value={sku.flexoSpeed ?? ''} placeholder={v.speedPh} onChange={actions.bindSku(id, 'flexoSpeed')} />}
            </Field>
            <Field label="Target efficiency %">
              {(fid) => (
                <NumberInput id={fid} max={100} tone={v.effMissing ? 'missing' : 'default'} value={sku.flexoEff ?? ''} placeholder="e.g. 85" onChange={actions.bindSku(id, 'flexoEff')} />
              )}
            </Field>
            <Field label="Working hours/day">
              {(fid) => <NumberInput id={fid} max={24} value={sku.flexoHours ?? ''} placeholder={hoursPlaceholder(state)} onChange={actions.bindSku(id, 'flexoHours')} />}
            </Field>
            <CountField
              label="Lines running"
              value={v.lines}
              onChange={(e) => actions.updateSku(id, { numFlexoLine: lineCountOf(e.target.value) })}
              onInc={() => actions.updateSku(id, { numFlexoLine: lineInc(sku.numFlexoLine) })}
              onDec={() => actions.updateSku(id, { numFlexoLine: lineDec(sku.numFlexoLine) })}
              incLabel="Add line"
              decLabel="Remove line"
            />
          </FieldGrid>
          <TileStrip>
            <InfoTile label="Print colours" value={v.colS} />
            <InfoTile label="Run speed" value={v.speedS} title="Effective web speed at this colour count" />
            <InfoTile label="Web meters (order)" value={v.metersS} />
            <InfoTile label="Effective speed" value={v.effSpeedS} />
            <InfoTile label="Effective runtime/day" value={v.runtimeS} title="Working hours/day × efficiency" />
            <InfoTile label="Meters printed/day" value={v.mPerDayS} title="Effective speed × 60 × working hours/day" />
            <InfoTile tone="accent" label="Number of days" value={v.daysS} />
          </TileStrip>
        </>
      ) : (
        <NotApplicable>
          Flexo planning applies to printed non-BOPP bags. Set the bag to printed (and a non-BOPP type) to plan the flexo line. BOPP jobs use the BOPP planning panel below.
        </NotApplicable>
      )}
    </PlanningSection>
  );
}
