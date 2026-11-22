import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { cx } from './cx';
import s from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'soft' | 'dashed' | 'ghost' | 'pill';
export type ButtonSize = 'xs' | 'sm' | 'md';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** leading icon */
  icon?: ReactNode;
}

export function Button({ variant = 'secondary', size = 'sm', icon, className, children, type = 'button', ...rest }: ButtonProps) {
  return (
    <button type={type} className={cx(s.button, s[size], s[variant], className)} {...rest}>
      {icon}
      {children}
    </button>
  );
}

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'aria-label'> {
  /** accessible name — required, the button has no visible text */
  label: string;
  /** hover colour: brand (default), danger, or neutral */
  tone?: 'brand' | 'danger' | 'neutral';
  children: ReactNode;
}

export function IconButton({ label, tone = 'brand', className, children, type = 'button', ...rest }: IconButtonProps) {
  return (
    <button
      type={type}
      aria-label={label}
      title={rest.title ?? label}
      className={cx(s.icon, tone === 'danger' && s.iconDanger, tone === 'neutral' && s.iconNeutral, className)}
      {...rest}
    >
      {children}
    </button>
  );
}
