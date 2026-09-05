import styles from "./page.module.css";

/**
 * One term and its value, inside a `.facts` `<dl>`. `supplied={false}` marks a
 * value that is a reason rather than a figure — "Not measured", an admission —
 * and the stylesheet keeps it from reading as a number (#91).
 */
export function Fact({
  term,
  value,
  supplied = true,
}: {
  readonly term: string;
  readonly value: string;
  readonly supplied?: boolean;
}) {
  return (
    <div className={styles.fact}>
      <dt className={styles.term}>{term}</dt>
      <dd className={styles.value} data-supplied={supplied}>
        {value}
      </dd>
    </div>
  );
}
