/*
 * AEDI - IONITY GLOBAL | @ionity/portal | main.tsx | Policy 986 AED
 * (c) 2018-2026 Antwerp Designs | Ionity (Pty) Ltd | Ionity Global (Pty) Ltd
 */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './app/App.tsx';
import './index.css';
import type { Simulation } from './sim/simulator.ts';

async function boot(): Promise<void> {
  const root = document.getElementById('root');
  if (!root) throw new Error('#root missing from index.html');

  // ?simulate=v1|v2|v2-usb loads the simulated board (separate chunk, never in the normal path).
  let sim: Simulation | null = null;
  const mode = new URLSearchParams(window.location.search).get('simulate');
  if (mode === 'v1' || mode === 'v2' || mode === 'v2-usb') {
    const { createSimulation } = await import('./sim/simulator.ts');
    sim = createSimulation(mode);
  }

  createRoot(root).render(
    <StrictMode>
      <App sim={sim} />
    </StrictMode>,
  );
}

void boot();
