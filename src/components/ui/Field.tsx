import { forwardRef, useId } from 'react';
import type { CSSProperties, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from 'react';
import { cx } from './cx';
import s from './Field.module.css';

export interface FieldProps {
  label: ReactNode;
  /** small grey line under the control (unit equivalents, derived values) */
  hint?: ReactNode;
  /** tooltip on the label */
  help?: string;
  /** highlight the label in brand colour (driver inputs) */
  brand?: boolean;
  className?: string;
  style?: CSSProperties;
  /** render-prop receives the generated id to put on the control */
  children: (id: string) => ReactNode;
}

/** Uppercase label + control + optional hint, wired with a shared id. */
export function Field({ label, hint, help, brand, className, style, children }: FieldProps) {
  const id = useId();
  return (
    <div className={cx(s.field, className)} style={style}>
      <label htmlFor={id} title={help} className={cx(s.label, brand && s.labelBrand)}>
        {label}
      </label>
      {children(id)}
      {hint != null && hint !== '' && <div className={s.hint}>{hint}</div>}
    </div>
  );
}

/** Standalone field label (for groups that hold several controls). */
export function FieldLabel({ children, brand, help, htmlFor, id }: { children: ReactNode; brand?: boolean; help?: string; htmlFor?: string; id?: string }) {
  return (
    <label htmlFor={htmlFor} id={id} title={help} className={cx(s.label, brand && s.labelBrand)}>
      {children}
    </label>
  );
}

export function Hint({ children, className, style }: { children: ReactNode; className?: string; style?: CSSProperties }) {
  return (
    <div className={cx(s.hint, className)} style={style}>
      {children}
    </div>
  );
}

export type InputTone = 'default' | 'brand' | 'missing';

export interface TextInputProps extends InputHTMLAttributes<HTMLInputElement> {
  tone?: InputTone;
  mono?: boolean;
}

export const TextInput = forwardRef<HTMLInputElement, TextInputProps>(function TextInput({ tone = 'default', mono, className, ...rest }, ref) {
  return <input ref={ref} className={cx(s.input, mono && s.mono, tone === 'brand' && s.brand, tone === 'missing' && s.missing, className)} {...rest} />;
});

/** Number input; blank stays blank (the engine reads blanks as 0). */
export const NumberInput = forwardRef<HTMLInputElement, Omit<TextInputProps, 'type'>>(function NumberInput(props, ref) {
  return <TextInput ref={ref} type="number" inputMode="decimal" min={0} {...props} />;
});

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  options?: { value: string; label: string }[];
}

export function Select({ options, className, children, ...rest }: SelectProps) {
  return (
    <select className={cx(s.select, className)} {...rest}>
      {options ? options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>) : children}
    </select>
  );
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(s.textarea, className)} {...rest} />;
}

export interface CellInputProps extends InputHTMLAttributes<HTMLInputElement> {
  align?: 'left' | 'right';
}

/** Borderless-until-hover input for editable table cells. */
export function CellInput({ align = 'left', className, ...rest }: CellInputProps) {
  return <input className={cx(s.cell, align === 'right' && s.right, className)} {...rest} />;
}

export function CellSelect({ options, className, children, ...rest }: SelectProps) {
  return (
    <select className={cx(s.cell, s.cellSelect, className)} {...rest}>
      {options ? options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>) : children}
    </select>
  );
}

export interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: ReactNode;
}

export function Checkbox({ label, className, ...rest }: CheckboxProps) {
  return (
    <label className={cx(s.check, className)}>
      <input type="checkbox" {...rest} />
      {label}
    </label>
  );
}

/** Class names for one-off controls that need the field styling. */
export const fieldClasses = s;
