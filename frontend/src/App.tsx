import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Layout from "./components/Layout";
import UploadPage from "./pages/UploadPage";
import DashboardPage from "./pages/DashboardPage";
import BatchListPage from "./pages/BatchListPage";
import PartnerMappingPage from "./pages/PartnerMappingPage";
import AnalyticsPage from "./pages/AnalyticsPage";
import SettlementTypePage from "./pages/SettlementTypePage";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/upload" replace />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/batches" element={<BatchListPage />} />
          <Route path="/dashboard/:batchId" element={<DashboardPage />} />
          <Route path="/partner-mapping" element={<PartnerMappingPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/settlement-type" element={<SettlementTypePage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
