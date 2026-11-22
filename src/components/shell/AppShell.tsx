import { Fragment } from 'react';
import type { ComponentType, ReactNode } from 'react';
import { defaultState, fmt } from '../../engine';
import type { AppState, Tab } from '../../engine';
import { useStore } from '../../state/store';
import {
  Button,
  CalculatorIcon,
  DownloadIcon,
  FactoryIcon,
  JobCardIcon,
  QuoteIcon,
  RatesIcon,
  SettingsIcon,
  UploadIcon,
  cx,
} from '../ui';
import type { IconProps } from '../ui';
import logo from '../../assets/logo.svg';
import s from './AppShell.module.css';

interface NavEntry {
  tab: Tab;
  label: string;
  title: string;
  Icon: ComponentType<IconProps>;
}

const NAV: { group: string; items: NavEntry[] }[] = [
  {
    group: 'Costing',
    items: [
      { tab: 'calc', label: 'Calculator', title: 'Costing calculator', Icon: CalculatorIcon },
      { tab: 'rates', label: 'Rates', title: 'Rates', Icon: RatesIcon },
      { tab: 'settings', label: 'Settings', title: 'Settings', Icon: SettingsIcon },
    ],
  },
  {
    group: 'Operations',
    items: [
      { tab: 'mach', label: 'Machines & planning', title: 'Machines & planning', Icon: FactoryIcon },
      { tab: 'job', label: 'Job card', title: 'Job card', Icon: JobCardIcon },
      { tab: 'quote', label: 'Quote', title: 'Quote', Icon: QuoteIcon },
    ],
  },
];

export const PAGE_TITLES: Record<Tab, string> = Object.fromEntries(NAV.flatMap((g) => g.items.map((i) => [i.tab, i.title]))) as Record<Tab, string>;

function downloadBackup(state: AppState) {
  const blob = new Blob([JSON.stringify(state, null, 1)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'pp-sacks-backup-' + new Date().toISOString().slice(0, 10) + '.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

function pickBackup(current: AppState, onLoad: (s: AppState) => void) {
  const inp = document.createElement('input');
  inp.type = 'file';
  inp.accept = '.json,application/json';
  inp.onchange = () => {
    const f = inp.files?.[0];
    if (!f) return;
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const j = JSON.parse(rd.result as string);
        if (!j || !Array.isArray(j.skus)) {
          alert('Not a valid backup file — no bag specs found in it.');
          return;
        }
        if (confirm('Replace all current data (' + current.skus.length + ' bags) with this backup (' + j.skus.length + ' bags)?')) {
          onLoad(Object.assign({}, current, j));
        }
      } catch (e) {
        alert('Could not read backup: ' + (e as Error).message);
      }
    };
    rd.readAsText(f);
  };
  inp.click();
}

function storageText(state: AppState, quota: number): string {
  const mb = JSON.stringify(state).length / 1048576;
  const q = quota ? (quota >= 1073741824 ? fmt(quota / 1073741824, 1) + ' GB' : fmt(quota / 1048576, 0) + ' MB') : '—';
  return fmt(mb, 2) + ' MB of ' + q + ' available';
}

export function AppShell({ children }: { children: ReactNode }) {
  const { state, actions, storage } = useStore();
  const size = JSON.stringify(state).length;
  const storWarn = storage.error || (storage.quota > 0 && size > storage.quota * 0.8);
  const storMsg = storage.error
    ? 'Save failed — storage error. Export a backup now, then remove images or old bags.'
    : 'Storage nearly full. Export a backup and remove unused images.';

  const resetData = () => {
    if (confirm('Replace all data with the sample dataset?')) actions.replaceState(defaultState());
  };

  return (
    <div data-app="1" className={s.app}>
      <aside data-noprint="1" className={s.sidebar}>
        <div className={s.brand}>
          <img src={logo} alt="Bellwort" />
        </div>
        <nav className={s.nav} aria-label="Sections">
          {NAV.map((g) => (
            <Fragment key={g.group}>
              <div className={s.navGroup}>{g.group}</div>
              {g.items.map(({ tab, label, Icon }) => (
                <button
                  key={tab}
                  type="button"
                  aria-current={state.tab === tab ? 'page' : undefined}
                  className={cx(s.navItem, state.tab === tab && s.navItemActive)}
                  onClick={() => actions.setState({ tab })}
                >
                  <Icon />
                  <span>{label}</span>
                </button>
              ))}
            </Fragment>
          ))}
        </nav>
        <div className={s.footer}>
          <div className={s.footerTitle}>Backup</div>
          <div className={s.footerRow}>
            <Button size="sm" icon={<DownloadIcon size={13} />} title="Download all bags, rates and machines as a JSON file" onClick={() => downloadBackup(state)} style={{ fontWeight: 500 }}>
              Export
            </Button>
            <Button size="sm" icon={<UploadIcon size={13} />} title="Restore from a backup JSON file" onClick={() => pickBackup(state, actions.replaceState)} style={{ fontWeight: 500 }}>
              Import
            </Button>
          </div>
          <div className={s.footerNote}>Data: {storageText(state, storage.quota)}</div>
          {storWarn && (
            <div role="alert" className={s.storWarn}>
              {storMsg}
            </div>
          )}
          <div className={s.footerNote}>Data saved in this browser only.</div>
        </div>
      </aside>

      <div className={s.main}>
        <header data-noprint="1" className={s.topbar}>
          <h1 className={s.pageTitle}>{PAGE_TITLES[state.tab]}</h1>
          <div style={{ flex: 1 }} />
          <Button size="sm" className={s.resetBtn} title="Restore sample data" onClick={resetData}>
            Reset sample data
          </Button>
        </header>
        <main data-scroll="1" className={s.scroll}>
          <div className={s.content}>{children}</div>
        </main>
      </div>
    </div>
  );
}
