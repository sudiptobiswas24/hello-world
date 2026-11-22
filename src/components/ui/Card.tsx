import type { HTMLAttributes, ReactNode } from 'react';
import { cx } from './cx';
import s from './Card.module.css';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  /** clip children to the rounded corners (tables, lists) */
  clip?: boolean;
}

/** White surface with hairline border and soft shadow. */
export function Card({ clip, className, ...rest }: CardProps) {
  return <div className={cx(s.card, clip && s.clip, className)} {...rest} />;
}

export interface CardHeaderProps extends Omit<HTMLAttributes<HTMLDivElement>, 'title'> {
  title?: ReactNode;
  /** right-aligned controls */
  actions?: ReactNode;
}

/** Card heading row: small uppercase title plus optional actions. */
export function CardHeader({ title, actions, className, children, ...rest }: CardHeaderProps) {
  return (
    <div className={cx(s.header, className)} {...rest}>
      {title != null && <h2 className={s.title}>{title}</h2>}
      {children}
      {actions != null && (
        <>
          <div style={{ flex: 1 }} />
          {actions}
        </>
      )}
    </div>
  );
}

export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cx(s.body, className)} {...rest} />;
}

/** Small uppercase group heading with a hairline rule (e.g. "Lamination & films"). */
export function SectionTitle({ className, ...rest }: HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cx(s.sectionTitle, className)} {...rest} />;
}
