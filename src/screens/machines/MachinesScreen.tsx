import type { ChangeEvent } from 'react';
import type { Machine } from '../../engine';
import { useResults, useStore } from '../../state/store';
import { Button, Card, CellInput, CellSelect, IconButton, PlusIcon, TrashIcon, cx } from '../../components/ui';
import { MACHINE_TYPE_OPTIONS, PARAM_DEFS, TYPE_DEFAULT_PARAMS, isMachineType, orderOptions, planView } from './model';
import s from './MachinesScreen.module.css';

type InputEvent = ChangeEvent<HTMLInputElement>;

function MachineRow({ m }: { m: Machine }) {
  const { actions } = useStore();
  const p = m.p || {};
  const params = PARAM_DEFS[m.typ] || [];
  const setType = (e: ChangeEvent<HTMLSelectElement>) => {
    const t = e.target.value;
    if (isMachineType(t)) actions.updMachine(m.id, { typ: t, p: { ...TYPE_DEFAULT_PARAMS[t] } });
  };
  return (
    <tr>
      <td>
        <CellInput
          aria-label="Machine name"
          className={s.nameInput}
          value={m.name}
          onChange={(e: InputEvent) => actions.updMachine(m.id, { name: e.target.value })}
        />
      </td>
      <td>
        <CellSelect aria-label={'Stage — ' + m.name} value={m.typ} onChange={setType} options={MACHINE_TYPE_OPTIONS} />
      </td>
      <td>
        <div className={s.params}>
          {params.map(([k, label]) => (
            <label key={k} className={s.param}>
              {label}
              <input
                type="number"
                className={cx(s.numInput, s.paramInput)}
                value={p[k] == null ? '' : p[k]}
                onChange={(e: InputEvent) => actions.updMachineP(m.id, k, e.target.value)}
              />
            </label>
          ))}
        </div>
      </td>
      <td className={s.right}>
        <input
          type="number"
          aria-label={'Count — ' + m.name}
          className={cx(s.numInput, s.cellNum)}
          value={m.count}
          onChange={(e: InputEvent) => actions.updMachine(m.id, { count: e.target.value })}
        />
      </td>
      <td className={s.right}>
        <input
          type="number"
          aria-label={'Eff % — ' + m.name}
          className={cx(s.numInput, s.cellNum)}
          value={m.eff}
          onChange={(e: InputEvent) => actions.updMachine(m.id, { eff: e.target.value })}
        />
      </td>
      <td className={s.actionsCell}>
        <IconButton label="Delete machine" tone="danger" onClick={() => actions.delMachine(m.id)}>
          <TrashIcon size={14} />
        </IconButton>
      </td>
    </tr>
  );
}

export function MachinesScreen() {
  const { state, actions } = useStore();
  const results = useResults();
  const pv = planView(state, results);
  const opts = orderOptions(state.skus);

  return (
    <div data-screen-label="Machines and planning" className={s.screen}>
      <Card>
        <div className={s.header}>
          <h2 className={s.title}>Plant machines</h2>
          <div className={s.spacer} />
          <Button className={s.addBtn} icon={<PlusIcon size={13} />} onClick={actions.addMachine}>
            Add machine
          </Button>
        </div>
        <div className={s.tableScroll}>
          <table className={s.machTable}>
            <thead>
              <tr>
                <th scope="col">Machine</th>
                <th scope="col">Stage</th>
                <th scope="col">Parameters</th>
                <th scope="col" className={s.right}>
                  Count
                </th>
                <th scope="col" className={s.right}>
                  Eff %
                </th>
                <th scope="col" className={s.actionsHead}>
                  <span className="visually-hidden">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {state.machines.map((m) => (
                <MachineRow key={m.id} m={m} />
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card>
        <div className={cx(s.header, s.headerWrap)}>
          <h2 className={s.title}>Order scheduling</h2>
          <select
            aria-label="Order to schedule"
            className={s.planSelect}
            value={pv.planSelId || ''}
            onChange={(e) => actions.setState({ planSel: e.target.value })}
          >
            {opts.map((o) => (
              <option key={o.id} value={o.id}>
                {o.label}
              </option>
            ))}
          </select>
          <div className={s.spacer} />
          <div className={s.shiftNote}>{pv.shiftNote}</div>
        </div>
        <div className={s.chips}>
          <div className={s.chip}>
            <div className={s.chipLabel}>Order qty</div>
            <div className={s.chipValue}>{pv.qtyS}</div>
          </div>
          <div className={s.chip}>
            <div className={s.chipLabel}>Material (incl. allowances)</div>
            <div className={s.chipValue}>{pv.kgS}</div>
          </div>
          <div className={s.chip}>
            <div className={s.chipLabel}>Fabric to weave</div>
            <div className={s.chipValue}>{pv.mS}</div>
          </div>
          <div className={cx(s.chip, s.chipBrand)}>
            <div className={s.chipLabel}>Delivery estimate</div>
            <div className={s.chipValue}>{pv.totalS}</div>
          </div>
        </div>
        <div className={s.planBody}>
          <table className={s.planTable}>
            <thead>
              <tr>
                <th scope="col">Stage</th>
                <th scope="col">Requirement</th>
                <th scope="col">Capacity</th>
                <th scope="col">Hours</th>
                <th scope="col">Days</th>
              </tr>
            </thead>
            <tbody>
              {pv.rows.map((p) => (
                <tr key={p.stage} className={cx(p.tone === 'bottleneck' && s.rowBottleneck, p.tone === 'noMachine' && s.rowNoMachine)}>
                  <th scope="row">
                    {p.stage} {p.flag}
                  </th>
                  <td className={s.dim}>{p.req}</td>
                  <td className={s.dim}>{p.cap}</td>
                  <td>{p.hours}</td>
                  <td className={s.days}>{p.days}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className={s.bottleneckNote}>{pv.bottleneckS}</div>
        </div>
      </Card>
    </div>
  );
}
