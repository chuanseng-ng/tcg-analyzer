import type { ElementType, ReactNode } from "react";

import styles from "./Container.module.css";

export interface ContainerProps {
  children: ReactNode;
  /** Landmark or block element to render as. Defaults to `div`. */
  as?: ElementType;
  /** Constrain to a comfortable reading measure rather than the full width. */
  prose?: boolean;
  className?: string;
}

export function Container({
  children,
  as: Element = "div",
  prose = false,
  className,
}: ContainerProps) {
  const classes = [styles.container, prose ? styles.prose : null, className]
    .filter(Boolean)
    .join(" ");

  return <Element className={classes}>{children}</Element>;
}
