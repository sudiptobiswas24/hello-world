import { useId } from 'react';
import type { ChangeEventHandler, ComponentType, ReactNode } from 'react';
import { ChevronDownIcon, Field, NumberInput, cx } from '../../../components/ui';
import type { IconProps } from '../../../components/ui';
import s from './PlanningSection.module.css';

export interface PlanningSectionProps {
  title: string;
  /** one-line result shown in the header (e.g. "2 looms · 4.5 days") */
  summary: string;
  icon: ComponentType<IconProps>;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}

/** Bordered panel whose header toggles the planning inputs and outputs. */
export function PlanningSection({ title, summary, icon: Icon, open, onToggle, children }: PlanningSectionProps) {
  const bodyId = useId();
  return (
    <div className={s.section}>
      <button type="button" className={s.header} aria-expanded={open} aria-controls={open ? bodyId : undefined} onClick={onToggle}>
        <Icon size={14} className={s.icon} />
        <span className={s.title}>{title}</span>
        <span className={s.summary}>{summary}</span>
        <ChevronDownIcon size={14} strokeWidth={2} className={cx(s.chevron, open && s.chevronOpen)} />
      </button>
      {open && (
        <div id={bodyId} className={s.body}>
          {children}
        </div>
      )}
    </div>
  );
}

/** Auto-fitting row of planning inputs. */
export function FieldGrid({ children }: { children: ReactNode }) {
  return <div className={s.grid}>{children}</div>;
}

export interface CountFieldProps {
  label: string;
  value: number;
  onChange: ChangeEventHandler<HTMLInputElement>;
  onInc: () => void;
  onDec: () => void;
  /** accessible names for the + / − buttons */
  incLabel: string;
  decLabel: string;
}

/** Whole-number count (looms, lines) with + / − buttons. */
export function CountField({ label, value, onChange, onInc, onDec, incLabel, decLabel }: CountFieldProps) {
  return (
    <Field label={label}>
      {(id) => (
        <div className={s.stepper}>
          <NumberInput id={id} min={1} value={value} onChange={onChange} />
          <button type="button" className={s.stepBtn} aria-label={incLabel} onClick={onInc}>
            +
          </button>
          <button type="button" className={s.stepBtn} aria-label={decLabel} onClick={onDec}>
            −
          </button>
        </div>
      )}
    </Field>
  );
}

/** Message shown in place of the panel's fields when it does not apply to this bag. */
export function NotApplicable({ children }: { children: ReactNode }) {
  return <div className={s.na}>{children}</div>;
}

export const planningClasses = s;
