/*
 * ponytail: copied from `apps/web`, not shared. ADR 0001 gives the two
 * applications no TypeScript package in common — their contract is the OpenAPI
 * schema — so extracting this would mean a new pnpm package and a build step
 * for a handful of declarations, and **nothing detects it if the two drift**.
 * Two apps is when copying is cheaper; a third is when `packages/ui` earns its
 * own ADR.
 */
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
