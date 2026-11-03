import React from "react";
import { createRoot } from "react-dom/client";
import { SkulletProvider } from "skullet/react";
import { App } from "./App.jsx";
import { I18nProvider } from "./i18n.jsx";
import { useTheme } from "./theme.jsx";
import "skullet/styles.css";
import "./styles.css";

// Skeleton colours come from the same ink scale as the page, so a loading table
// looks like a dim version of the table rather than a grey box on top of it.
// Both palettes are declared and `mode` picks one, because the theme is a user
// choice here, not a media query.
const THEME = {
  dark: { base: "rgba(250, 250, 249, 0.06)", highlight: "rgba(250, 250, 249, 0.12)" },
  light: { base: "rgba(15, 14, 13, 0.07)", highlight: "rgba(15, 14, 13, 0.12)" },
  radius: "6px",
  animation: "shimmer",
  speed: "fast",
};

function Shell() {
  const [theme] = useTheme();
  return (
    <SkulletProvider mode={theme} theme={THEME} reveal="smart">
      <App />
    </SkulletProvider>
  );
}

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <I18nProvider>
      <Shell />
    </I18nProvider>
  </React.StrictMode>,
);
