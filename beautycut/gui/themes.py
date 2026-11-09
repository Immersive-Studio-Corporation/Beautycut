from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    background: str
    card: str
    surface: str
    border: str
    text: str
    muted: str
    accent: str
    accent_light: str
    accent_text: str
    selected: str
    preview: str
    groove: str
    scrollbar: str


THEMES = (
    Theme("forest_night", "Forêt de nuit", "#0c1411", "#14201b", "#101a16", "#2b4035", "#f0f7f2", "#b5c9bd", "#42c78b", "#85e5ae", "#062218", "#193c2c", "#0a120f", "#35463c", "#4a7660"),
    Theme("northern_lights", "Aurore boréale", "#0b1217", "#142027", "#101a20", "#2b4146", "#eff8f7", "#b5cacb", "#40d2bb", "#8becd9", "#06231f", "#153a37", "#091316", "#34484a", "#4a8980"),
    Theme("canopy", "Canopée", "#10140e", "#1a2115", "#141b11", "#38442c", "#f3f7e9", "#c2ceb0", "#a4d75d", "#d2ed88", "#172306", "#34451d", "#10140e", "#424b37", "#718b47"),
    Theme("sage_garden", "Jardin sauge", "#101512", "#1a211d", "#141b17", "#35483c", "#f0f7f1", "#bdcfc2", "#8ed0a1", "#b8e7b7", "#102416", "#263d2e", "#0d1410", "#3c4b40", "#61846b"),
    Theme("desert_dusk", "Dunes au crépuscule", "#17120e", "#241c16", "#1d1712", "#514033", "#fff5e9", "#d5c3ad", "#e5b46e", "#f7d59b", "#30200c", "#49341d", "#130f0b", "#58483a", "#9a7449"),
    Theme("sunset_glow", "Coucher de soleil", "#180f12", "#25181c", "#1e1317", "#523039", "#fff1f0", "#d6bfc1", "#ff8d79", "#ffc29b", "#35120c", "#48252a", "#130d10", "#564047", "#a15d63"),
    Theme("volcanic_ember", "Lueur volcanique", "#17100e", "#241916", "#1d1412", "#51372e", "#fff3ed", "#d6beb1", "#ef7955", "#ffad70", "#35150d", "#472820", "#130d0b", "#56423a", "#a55e46"),
    Theme("alpine_mist", "Brume alpine", "#0d1417", "#172126", "#111a1e", "#30454a", "#edf8fa", "#b7cbd0", "#7dc6d0", "#b4e8e5", "#09252a", "#1d3a41", "#0a1114", "#394b50", "#58858d"),
    Theme("deep_ocean", "Océan profond", "#091316", "#132126", "#0f1a1f", "#29434b", "#edf8fa", "#b2c9ce", "#39b9d1", "#78e8e1", "#06232a", "#153b44", "#081114", "#314850", "#438393"),
    Theme("lagoon", "Lagon turquoise", "#0b1514", "#142320", "#101c1a", "#2b4943", "#eff8f4", "#b5cdc5", "#39d0bd", "#8be9c8", "#062520", "#16443a", "#091211", "#354c45", "#4d9083"),
    Theme("coral_reef", "Récif corallien", "#160f11", "#24191c", "#1d1417", "#50343a", "#fff2f0", "#d4c0c2", "#fa857f", "#ffb19a", "#35100f", "#47252a", "#120c0e", "#574047", "#a35f6c"),
    Theme("glacier", "Glacier", "#0c131a", "#161f29", "#111a23", "#304554", "#f0f7ff", "#bacbdc", "#79c3f2", "#b6e7ff", "#08243a", "#183d54", "#091117", "#394b5b", "#5683a3"),
    Theme("tropical_rain", "Pluie tropicale", "#0d1512", "#17221c", "#111b16", "#2f493b", "#eff8f1", "#b9cdc0", "#65d49d", "#a4edbd", "#08261a", "#1c4230", "#0a120e", "#3b4c40", "#54876a"),
    Theme("meadow", "Prairie étoilée", "#14150d", "#202116", "#191b12", "#45472e", "#f8f8eb", "#ccceb1", "#c2d75f", "#e5eb90", "#292d08", "#3d421b", "#11120b", "#4b4c38", "#84884c"),
    Theme("cosmic_cherry", "Cerise cosmique", "#150f18", "#211924", "#1a131e", "#48344d", "#f7efff", "#cbbdd4", "#e58ac2", "#ffc0db", "#321329", "#442643", "#100c13", "#514358", "#91628d"),
    Theme("nebula", "Nébuleuse", "#11101a", "#1c1a29", "#151422", "#3b3859", "#f5f1ff", "#c4bfdc", "#b49aff", "#e1aaff", "#241644", "#352b55", "#0d0c15", "#46435a", "#766ab1"),
    Theme("milky_way", "Voie lactée", "#10111a", "#191c29", "#131623", "#343d58", "#f1f4ff", "#bbc6df", "#98aaff", "#d1a8ff", "#17234a", "#293858", "#0b0d14", "#3b455a", "#657cae"),
    Theme("starry_night", "Nuit étoilée", "#0d111b", "#161e2a", "#111824", "#2d405c", "#eef4ff", "#b7c7e1", "#83a7ff", "#b7d3ff", "#102044", "#1f3458", "#090d16", "#36465c", "#5c7ba9"),
    Theme("mars", "Mars", "#17110f", "#241b18", "#1d1512", "#523a32", "#fff3ed", "#d5c0b5", "#ed8564", "#ffb08b", "#34150e", "#482a22", "#130d0b", "#59463e", "#a3634e"),
    Theme("moonlight", "Clair de lune", "#10121a", "#1a1c27", "#141621", "#393e55", "#f5f5ff", "#c7c9dc", "#c8c8f5", "#a7c3ff", "#22213f", "#343653", "#0c0e15", "#46495d", "#777ea8"),
)

THEME_BY_KEY = {theme.key: theme for theme in THEMES}
