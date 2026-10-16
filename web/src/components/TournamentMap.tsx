import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { tournamentHref } from "../hooks";
import { useI18n } from "../i18n";
import type { TournamentPlace } from "../types";
import { dateRange } from "./tournamentUtil";

// swisstopo's national map in grey: the markers stand out (free, with attribution)
const TILES = "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.pixelkarte-grau/default/current/3857/{z}/{x}/{y}.jpeg";
const ATTRIBUTION = '&copy; <a href="https://www.swisstopo.admin.ch/" target="_blank" rel="noopener">swisstopo</a>';
const SWITZERLAND = L.latLngBounds([45.82, 5.96], [47.81, 10.49]);

/** The tournaments on a map: one marker per place, its popup lists them (links to their pages). */
export default function TournamentMap({ places }: { places: TournamentPlace[] }) {
  const { t, locale } = useI18n();
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<L.Map | null>(null);
  const layer = useRef<L.LayerGroup | null>(null);

  useEffect(() => {
    // swisstopo's tiles end not far past the border: zoomed out further, the edges are blank (a
    // phone needs one level more to see the whole country)
    const minZoom = el.current!.clientWidth < 700 ? 7 : 8;
    const m = L.map(el.current!, { maxBounds: SWITZERLAND.pad(0.2), maxBoundsViscosity: 1, minZoom, maxZoom: 16 });
    L.tileLayer(TILES, { attribution: ATTRIBUTION, maxZoom: 16 }).addTo(m);
    m.fitBounds(SWITZERLAND);
    map.current = m;
    layer.current = L.layerGroup().addTo(m);
    return () => {
      m.remove();
      map.current = null;
    };
  }, []);

  useEffect(() => {
    const m = map.current;
    const group = layer.current;
    if (!m || !group) return;
    group.clearLayers();
    // the tournaments of each place, in the list's order
    const byPlace = new Map<string, TournamentPlace[]>();
    for (const p of places) {
      const k = `${p.lat},${p.lon}`;
      byPlace.set(k, [...(byPlace.get(k) ?? []), p]);
    }
    for (const list of byPlace.values()) {
      const { lat, lon } = list[0];
      const popup = document.createElement("div");
      popup.className = "map-popup";
      const title = document.createElement("strong");
      title.textContent = list[0].city ?? "";
      popup.append(title);
      const ul = document.createElement("ul");
      for (const p of list) {
        const li = document.createElement("li");
        const a = document.createElement("a");
        a.href = tournamentHref(p.id);
        a.textContent = p.name;
        const when = document.createElement("small");
        when.textContent = dateRange(p.dateBegin, p.dateEnd, locale);
        li.append(a, when);
        ul.append(li);
      }
      popup.append(ul);
      L.circleMarker([lat, lon], {
        radius: Math.min(8 + 2 * (list.length - 1), 15),
        className: "map-marker",
      })
        .bindPopup(popup, { maxHeight: 260 })
        .bindTooltip(list.length > 1 ? `${list[0].city ?? ""} · ${t("tournamentsCount", { n: list.length })}` : list[0].name)
        .addTo(group);
    }
    if (places.length) m.fitBounds(L.latLngBounds(places.map((p) => [p.lat, p.lon])), { padding: [24, 24], maxZoom: 12 });
  }, [places, t, locale]);

  return <div className="tourn-map" ref={el} role="region" aria-label={t("mapLabel")} />;
}
