import React from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { TableProvider } from "./contexts/TableContext";
import { ViewStoreProvider } from "./view/ViewStore";
import LuxembourgSExternalTradeAtAGlance from "./pages/LuxembourgSExternalTradeAtAGlance";
import TrendsPage from "./features/trends/TrendsPage";
import SideNav from "./components/SideNav";
import "./App.css";

// The view store holds the spec the backend pushes (SSE, or polling), so every
// page under it renders the same version and sends its ops through one place.
function App() {
  return (
    <TableProvider>
      <ViewStoreProvider>
        <div className="app-container">
          <SideNav />
          {/* A div, not <main>: each page holds its own <main> landmark. */}
          <div className="app-main">
            <Routes>
              <Route path="/" element={<LuxembourgSExternalTradeAtAGlance />} />
              <Route path="/trends" element={<TrendsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </div>
        </div>
      </ViewStoreProvider>
    </TableProvider>
  );
}
export default App;
