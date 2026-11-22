import type { DeltaTone } from './model';
import s from './RatesScreen.module.css';

/** Colour class for a week-over-week / draft change. */
export const DELTA_TONE: Record<DeltaTone, string> = {
  neutral: s.deltaNeutral,
  up: s.deltaUp,
  down: s.deltaDown,
};
