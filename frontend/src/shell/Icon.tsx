import type { IconName } from "../app/registry";

const PATHS: Record<IconName, string> = {
  home: "M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z",
  sales: "M4 4h16v4H4zM4 10h10v10H4zM16 10h4v10h-4z",
  purchasing: "M3 5h2l2.5 10h11L21 8H7M9 19.5a1.5 1.5 0 1 0 0 .01M17 19.5a1.5 1.5 0 1 0 0 .01",
  stock: "M3 8l9-5 9 5v8l-9 5-9-5zM3 8l9 5 9-5M12 13v8",
  production: "M3 21V11l5 3V11l5 3V7l8 4v10z",
  accounts: "M4 20V10M10 20V4M16 20v-7M22 20H2",
};

export function Icon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" className="icon" aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  );
}
