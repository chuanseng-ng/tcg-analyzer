import type { CSSProperties, ElementType, ReactNode } from "react";

import styles from "./Stack.module.css";

/** Indices into the `--space-*` scale in `styles/tokens.css`. */
export type SpaceStep = 1 | 2 | 3 | 4 | 5 | 6;

export interface StackProps {
  children: ReactNode;
  gap?: SpaceStep;
  as?: ElementType;
  className?: string;
}

export function Stack({ children, gap = 4, as: Element = "div", className }: StackProps) {
  const classes = [styles.stack, className].filter(Boolean).join(" ");
  const style = { "--stack-gap": `var(--space-${gap})` } as CSSProperties;

  return (
    <Element className={classes} style={style}>
      {children}
    </Element>
  );
}
