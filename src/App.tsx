import { AppShell } from './components/shell/AppShell';
import { useStore } from './state/store';
import { CalculatorScreen } from './screens/calculator/CalculatorScreen';
import { RatesScreen } from './screens/rates/RatesScreen';
import { SettingsScreen } from './screens/settings/SettingsScreen';
import { MachinesScreen } from './screens/machines/MachinesScreen';
import { JobCardScreen } from './screens/jobcard/JobCardScreen';
import { QuoteScreen } from './screens/quote/QuoteScreen';

export function App() {
  const { state } = useStore();
  return (
    <AppShell>
      {state.tab === 'calc' && <CalculatorScreen />}
      {state.tab === 'rates' && <RatesScreen />}
      {state.tab === 'settings' && <SettingsScreen />}
      {state.tab === 'mach' && <MachinesScreen />}
      {state.tab === 'job' && <JobCardScreen />}
      {state.tab === 'quote' && <QuoteScreen />}
    </AppShell>
  );
}
