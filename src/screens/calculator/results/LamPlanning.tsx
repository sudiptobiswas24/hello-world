import type { CalcResult, Sku } from '../../../engine';
import { Field, NumberInput, RollIcon } from '../../../components/ui';
import { useStore } from '../../../state/store';
import { hoursPlaceholder, lamView, lineCountOf, lineDec, lineInc } from './model';
import { CountField, FieldGrid, NotApplicable, PlanningSection } from './PlanningSection';
import { InfoTile, TileStrip } from './Tile';

/** Extrusion-coating line: run meters, coating throughput and days. */
export function LamPlanning({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state, actions } = useStore();
  const v = lamView(state, sku, result);
  const id = sku.id;
  return (
    <PlanningSection
      title="Lamination planning"
      summary={v.summary}
      icon={RollIcon}
      open={!!sku.lamPlanOpen}
      onToggle={() => actions.updateSku(id, { lamPlanOpen: !sku.lamPlanOpen })}
    >
      {v.hasLam ? (
        <>
          <FieldGrid>
            <Field label="Line speed m/min">
              {(fid) => <NumberInput id={fid} value={sku.lamSpeed ?? ''} placeholder="e.g. 120" onChange={actions.bindSku(id, 'lamSpeed')} />}
            </Field>
            <Field label="Target efficiency %">
              {(fid) => (
                <NumberInput
                  id={fid}
                  max={100}
                  tone={v.effMissing ? 'missing' : 'default'}
                  value={sku.lamPlanEff ?? ''}
                  placeholder="e.g. 85"
                  onChange={actions.bindSku(id, 'lamPlanEff')}
                />
              )}
            </Field>
            <Field label="Working hours/day">
              {(fid) => <NumberInput id={fid} max={24} value={sku.lamHours ?? ''} placeholder={hoursPlaceholder(state)} onChange={actions.bindSku(id, 'lamHours')} />}
            </Field>
            <CountField
              label="Lines running"
              value={v.lines}
              onChange={(e) => actions.updateSku(id, { numLamLine: lineCountOf(e.target.value) })}
              onInc={() => actions.updateSku(id, { numLamLine: lineInc(sku.numLamLine) })}
              onDec={() => actions.updateSku(id, { numLamLine: lineDec(sku.numLamLine) })}
              incLabel="Add line"
              decLabel="Remove line"
            />
          </FieldGrid>
          <TileStrip>
            <InfoTile label="Lam meters (order)" value={v.lamMS} title="Lamination meters for the order (+1.5% allowance)" />
            <InfoTile label="Coating passes" value={v.passesS} title="Double side coats both faces in one unwind/rewind pass — same run length" />
            <InfoTile label="Coating g/m" value={v.coatGmS} />
            <InfoTile label="Total run meters" value={v.runMS} title="Meters run through the line = lamination meters (same for single or double side)" />
            <InfoTile label="Effective speed" value={v.effSpeedS} />
            <InfoTile label="Effective runtime/day" value={v.runtimeS} title="Working hours/day × efficiency" />
            <InfoTile label="Meters processed/day" value={v.mPerDayS} title="Effective speed × 60 × working hours/day" />
            <InfoTile label="Coating kg/hr" value={v.coatKgHrS} title="Coating throughput = coating g/m × effective speed × 60 ÷ 1000" />
            <InfoTile label="Coating kg (order)" value={v.coatKgS} />
            <InfoTile tone="accent" label="Number of days" value={v.daysS} />
          </TileStrip>
        </>
      ) : (
        <NotApplicable>This bag has no lamination. Set lamination to single or double side to plan the coating line.</NotApplicable>
      )}
    </PlanningSection>
  );
}
