import type { CalcResult, Sku } from '../../engine';
import { APP_CONFIG } from '../../config';
import { useStore } from '../../state/store';
import { BoppPlanning } from './results/BoppPlanning';
import { CostBreakdown } from './results/CostBreakdown';
import { DerivedStrip } from './results/DerivedStrip';
import { FlexoPlanning } from './results/FlexoPlanning';
import { LamPlanning } from './results/LamPlanning';
import { LoomPlanning } from './results/LoomPlanning';
import { PlanStrip } from './results/PlanStrip';
import { QuantitiesStrip } from './results/QuantitiesStrip';
import { TapePlanning } from './results/TapePlanning';

/**
 * Lower half of the cost sheet: derived figures, order quantities, the five
 * planning panels, BOM, conversion, totals and (optionally) the plan strip.
 * Rendered inside the detail card's 14px-gap column, so each section is a sibling.
 */
export function DetailResults({ sku, result }: { sku: Sku; result: CalcResult }) {
  const { state } = useStore();
  return (
    <>
      <DerivedStrip sku={sku} result={result} />
      <QuantitiesStrip sku={sku} result={result} wast={state.wast} />
      <LoomPlanning sku={sku} result={result} />
      <TapePlanning sku={sku} result={result} />
      <LamPlanning sku={sku} result={result} />
      <FlexoPlanning sku={sku} result={result} />
      <BoppPlanning sku={sku} result={result} />
      <CostBreakdown result={result} glob={state.glob} />
      {APP_CONFIG.showPlanStrip && <PlanStrip sku={sku} result={result} />}
    </>
  );
}
