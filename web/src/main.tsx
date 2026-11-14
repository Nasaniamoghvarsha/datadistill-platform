import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "@fontsource-variable/plus-jakarta-sans";
import "@fontsource-variable/inter"; // fallback for scripts Plus Jakarta Sans lacks
import App from "./App";
import { UIProvider } from "./ui";
import "./index.css";

try {
  localStorage.removeItem("dd-theme"); // the dim theme was removed (12 Nov)
} catch {}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <UIProvider>
        <App />
      </UIProvider>
    </BrowserRouter>
  </StrictMode>,
);
