import { BrowserRouter } from 'react-router-dom';
import { AppShell } from '@/components/layout/AppShell';
import { CopilotProvider } from '@/context/CopilotContext';

export default function App() {
  return (
    <BrowserRouter>
      {/* CopilotProvider hosts the single WebSocket / message buffer
          shared between the new /ask surface and the legacy ChatDrawer
          on other pages.  Mounted above AppShell so the connection
          persists across route changes. */}
      <CopilotProvider>
        <AppShell />
      </CopilotProvider>
    </BrowserRouter>
  );
}
