/*
 * ponytail: copied from `apps/web`, not shared. ADR 0001 gives the two
 * applications no TypeScript package in common — their contract is the OpenAPI
 * schema — so extracting this would mean a new pnpm package and a build step
 * for a handful of declarations, and **nothing detects it if the two drift**.
 * Two apps is when copying is cheaper; a third is when `packages/ui` earns its
 * own ADR.
 */
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
