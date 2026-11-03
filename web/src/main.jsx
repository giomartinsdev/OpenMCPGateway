import React from "react";
import { createRoot } from "react-dom/client";
import { SkulletProvider } from "skullet/react";
import { App } from "./App.jsx";
import "skullet/styles.css";
import "./styles.css";

// Skeleton colours come from the same ink scale as the page, so a loading table
// looks like a dim version of the table rather than a grey box on top of it.
const THEME = {
  dark: { base: "rgba(250, 250, 249, 0.06)", highlight: "rgba(250, 250, 249, 0.12)" },
  light: { base: "rgba(15, 14, 13, 0.06)", highlight: "rgba(15, 14, 13, 0.12)" },
  radius: "6px",
  animation: "shimmer",
  speed: "fast",
};

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <SkulletProvider mode="dark" theme={THEME} reveal="smart">
      <App />
    </SkulletProvider>
  </React.StrictMode>,
);
