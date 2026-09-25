import { useEffect, useState } from "react";

/** Chart colours (validated default palette), stepped separately for light and dark surfaces. */
const LIGHT = {
  s1: "#2a78d6", // W
  s2: "#eb6834", // C
  s3: "#1baf7a",
  win: "#0ca30c",
  loss: "#d03b3b",
  grid: "#e6e6e3",
  axis: "#6b6b66",
  text: "#1c1c1a",
  surface: "#ffffff",
};
const DARK: typeof LIGHT = {
  s1: "#3987e5",
  s2: "#d95926",
  s3: "#199e70",
  win: "#0ca30c",
  loss: "#d03b3b",
  grid: "#2e2e2c",
  axis: "#9a9a94",
  text: "#ececea",
  surface: "#1f1f1d",
};

export type ChartColors = typeof LIGHT;

export function useChartColors(): ChartColors {
  const query = "(prefers-color-scheme: dark)";
  const [dark, setDark] = useState(() => {
    try {
      return window.matchMedia(query).matches;
    } catch {
      return false;
    }
  });
  useEffect(() => {
    let mq: MediaQueryList;
    try {
      mq = window.matchMedia(query);
    } catch {
      return;
    }
    const on = (e: MediaQueryListEvent) => setDark(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return dark ? DARK : LIGHT;
}
