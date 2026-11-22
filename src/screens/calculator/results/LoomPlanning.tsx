import type { CalcResult, Sku } from '../../../engine';
import { Field, NumberInput, PlateIcon } from '../../../components/ui';
import { useStore } from '../../../state/store';
import { hoursPlaceholder, lineCountOf, lineDec, lineInc, loomView } from './model';
import { CountField, FieldGrid, PlanningSection } from './PlanningSection';
import { InfoTile, TileStrip } from './Tile';

/** Circular-loom planning: ring size, warp count, output per loom and days for the order. */
export function LoomPlanning({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state, actions } = useStore();
  const v = loomView(state, sku, result);
  const id = sku.id;
  return (
    <PlanningSection
      title="Loom planning"
      summary={v.summary}
      icon={PlateIcon}
      open={!!sku.loomOpen}
      onToggle={() => actions.updateSku(id, { loomOpen: !sku.loomOpen })}
    >
      <FieldGrid>
        <Field label="Loom PPM">
          {(fid) => (
            <NumberInput id={fid} tone={v.ppmMissing ? 'missing' : 'default'} value={sku.loomPpm ?? ''} placeholder="e.g. 750" onChange={actions.bindSku(id, 'loomPpm')} />
          )}
        </Field>
        <Field label="Target efficiency %">
          {(fid) => <NumberInput id={fid} max={100} value={sku.loomEff ?? ''} placeholder="e.g. 85" onChange={actions.bindSku(id, 'loomEff')} />}
        </Field>
        <Field label="Working hours/day">
          {(fid) => <NumberInput id={fid} max={24} value={sku.loomHours ?? ''} placeholder={hoursPlaceholder(state)} onChange={actions.bindSku(id, 'loomHours')} />}
        </Field>
        <CountField
          label="Looms required"
          value={v.numLoom}
          onChange={(e) => actions.updateSku(id, { numLoom: lineCountOf(e.target.value) })}
          onInc={() => actions.updateSku(id, { numLoom: lineInc(sku.numLoom) })}
          onDec={() => actions.updateSku(id, { numLoom: lineDec(sku.numLoom) })}
          incLabel="Add loom"
          decLabel="Remove loom"
        />
      </FieldGrid>
      <TileStrip>
        <InfoTile label="Weaving ring recommended" value={v.ringS} />
        <InfoTile label="Number of warp" value={v.numWarpS} />
        <InfoTile label="Mesh HMI (per dm)" value={v.dmWeftS} />
        <InfoTile label="100% production / day" value={v.hproS} />
        <InfoTile label="Target production / day" value={v.tproS} />
        <InfoTile tone="accent" label="Number of days" value={v.numDaysS} />
      </TileStrip>
    </PlanningSection>
  );
}
