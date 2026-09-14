import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import "./index.css";
import { App } from "./App";
import { Goals } from "./pages/Goals";
import { NeedsYou } from "./pages/NeedsYou";
import { GoalDetail } from "./pages/GoalDetail";
import { Projects } from "./pages/Projects";
import { Settings } from "./pages/Settings";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename="/console">
      <Routes>
        <Route element={<App />}>
          <Route index element={<Navigate to="/needs-you" replace />} />
          <Route path="needs-you" element={<NeedsYou />} />
          <Route path="goals" element={<Goals />} />
          <Route path="goals/:id" element={<GoalDetail />} />
          <Route path="projects" element={<Projects />} />
          <Route path="settings" element={<Settings />} />
          <Route path="*" element={<Navigate to="/needs-you" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
