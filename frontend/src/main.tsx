import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, atLeast, useAuth, type Role } from "./auth";
import Layout from "./components/Layout";
import { Spinner, ToastProvider } from "./components/ui";
import "./index.css";
import Agents from "./pages/Agents";
import Appointments from "./pages/Appointments";
import Branding from "./pages/Branding";
import Dashboard from "./pages/Dashboard";
import InboxPage from "./pages/Inbox";
import Knowledge from "./pages/Knowledge";
import Leads from "./pages/Leads";
import Login from "./pages/Login";
import SettingsPage from "./pages/Settings";
import Tenants from "./pages/Tenants";
import UsersPage from "./pages/Users";

function Guard({ min, children }: { min: Role; children: JSX.Element }) {
  const { me } = useAuth();
  return atLeast(me?.role, min) ? children : <Navigate to="/" replace />;
}

function App() {
  const { me, loading } = useAuth();
  if (loading) return <Spinner />;
  if (!me) return <Login />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/inbox" element={<Guard min="agent"><InboxPage /></Guard>} />
        <Route path="/leads" element={<Leads />} />
        <Route path="/appointments" element={<Appointments />} />
        <Route path="/knowledge" element={<Knowledge />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/branding" element={<Guard min="tenant_admin"><Branding /></Guard>} />
        <Route path="/users" element={<Guard min="tenant_admin"><UsersPage /></Guard>} />
        <Route path="/settings" element={<Guard min="tenant_admin"><SettingsPage /></Guard>} />
        <Route path="/tenants" element={<Guard min="tenant_admin"><Tenants /></Guard>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <ToastProvider>
          <App />
        </ToastProvider>
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
